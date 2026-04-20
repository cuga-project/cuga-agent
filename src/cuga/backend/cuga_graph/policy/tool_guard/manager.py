"""
Manager for generating tool guard examples using the toolguard library.

This module provides integration between CUGA's policy system and toolguard's
example generation capabilities to create violating and compliance examples
for tool usage policies.
"""

from typing import List, Tuple, Dict, Any
from pathlib import Path
from loguru import logger

from cuga.backend.cuga_graph.policy.models import ToolGuide, PolicyType
from cuga.backend.cuga_graph.policy.tool_guard.cuga_llm_adapter import CugaLLMAdapter

from cuga.sdk import CugaAgent
from toolguard.buildtime.buildtime import ToolGuardSpec, generate_guard_examples
from toolguard.extra.langchain_to_oas import langchain_tools_to_openapi
from toolguard.runtime.data_types import ToolGuardSpecItem



class ToolGuardManager:
    
    def __init__(
        self,
        agent: CugaAgent,
    ):
       
        self.langchain_tools: List = []  # Store LangChain tools
        self.tools_dict: Dict[str, Any] = {}  # Store OpenAPI dict for ToolGuard
        self._initialized = False
      
        self.tool_provider = agent.tool_provider
        
        # Wrap CUGA's LLM with adapter for ToolGuard compatibility
        if agent._model is None:
            raise ValueError(
                "Agent model is not initialized. Ensure the CugaAgent has a valid model "
                "before creating ToolGuardManager."
            )
        
        self.llm = CugaLLMAdapter(agent._model)
        logger.info(f"Initialized ToolGuardManager with {type(agent._model).__name__} via CugaLLMAdapter")
        
        # Use Path to properly handle path concatenation
        self.work_dir = str(Path(agent.cuga_folder) / "toolguard")
       
    
    async def initialize(
        self,
    ) -> None:
     
        logger.info("Initializing ToolGuardManager...")
        
        # Get all LangChain tools from the tool provider
        self.langchain_tools = await self.tool_provider.get_all_tools()
        
        # Convert LangChain tools to OpenAPI dict using ToolGuard's utility
        self.tools_dict = langchain_tools_to_openapi(self.langchain_tools)
        
        self._initialized = True
        logger.info(f"✅ ToolGuardManager initialized with {len(self.langchain_tools)} tools")
    
    async def generate_examples(
        self,
        policy: ToolGuide,
        target_tool: str
    ) -> Tuple[List[str], List[str]]:
        """
        Generate violating and compliance examples for a specific tool in a ToolGuide policy.
        
        Args:
            policy: ToolGuide policy to generate examples for
            target_tool: Specific tool name to generate examples for
            
        Returns:
            Tuple of (violating_examples, compliance_examples)
            
        Raises:
            RuntimeError: If manager not initialized
            ValueError: If policy is not a ToolGuide or target_tool not in policy.target_tools
        """
        # Ensure manager is initialized
        if not self._initialized or not self.langchain_tools:
            raise RuntimeError(
                "ToolGuardManager not initialized. Call initialize() first with a tool provider."
            )
        
        # Validate policy type
        if policy.type != PolicyType.TOOL_GUIDE:
            raise ValueError(
                f"Policy must be of type 'tool_guide', got '{policy.type}'. "
                f"Only tool_guide policies can generate examples."
            )
        
        # Validate that target_tool is in policy.target_tools or policy has wildcard
        if "*" not in policy.target_tools and target_tool not in policy.target_tools:
            raise ValueError(
                f"Tool '{target_tool}' is not in policy.target_tools. "
                f"Policy targets: {policy.target_tools}"
            )
        
        # Verify tool exists in our LangChain tools
        tool_names = [tool.name for tool in self.langchain_tools]
        if target_tool not in tool_names:
            raise ValueError(
                f"Tool '{target_tool}' not found in available tools. "
                f"Available tools: {tool_names}"
            )
        
        logger.info(f"Generating examples for tool '{target_tool}'...")
        
        # Create ToolGuardSpecItem with policy information
        # Concatenate description and guide_content for the description field
        description = policy.description
        if policy.guide_content:
            description = f"{description}\n\n{policy.guide_content}"
        
        spec_item = ToolGuardSpecItem(
            name=policy.name,
            description=description,
            references=[policy.guide_content] if policy.guide_content else []
        )
        
        # Create ToolGuardSpec with the spec item
        spec = ToolGuardSpec(
            tool_name=target_tool,
            policy_items=[spec_item]
        )
        
        # Generate examples using toolguard
        try:
            updated_specs = await generate_guard_examples(
                tools=self.tools_dict,  # Pass the OpenAPI dict
                tool_specs=[spec],
                llm=self.llm,  # type: ignore
                work_dir=self.work_dir
            )
            
            # Extract examples from the updated spec
            if updated_specs and len(updated_specs) > 0:
                updated_spec = updated_specs[0]
                if updated_spec.policy_items and len(updated_spec.policy_items) > 0:
                    policy_item = updated_spec.policy_items[0]
                    
                    violating_examples = policy_item.violation_examples
                    compliance_examples = policy_item.compliance_examples
                    
                    logger.info(
                        f"✅ Generated {len(violating_examples)} violating and {len(compliance_examples)} "
                        f"compliance examples for tool '{target_tool}'"
                    )
                    
                    return violating_examples, compliance_examples
                else:
                    logger.warning(f"No policy items in updated spec for tool '{target_tool}'")
                    return [], []
            else:
                logger.warning(f"No results returned for tool '{target_tool}'")
                return [], []
                
        except Exception as e:
            logger.error(
                f"❌ Failed to generate examples for tool '{target_tool}': {e}"
            )
            raise
    
    @property
    def is_initialized(self) -> bool:
        """Check if the manager has been initialized."""
        return self._initialized
    
    def get_available_tools(self) -> List[str]:
        """
        Get list of available tool names.
        
        Returns:
            List of tool names that can be used in policies
            
        Raises:
            RuntimeError: If manager not initialized
        """
        if not self._initialized or not self.langchain_tools:
            raise RuntimeError("ToolGuardManager not initialized. Call initialize() first.")
        
        return [tool.name for tool in self.langchain_tools]


# Made with Bob