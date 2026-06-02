"""Tool Guard Build-time Module

This module provides build-time functionality for the Tool Guard policy system.
It handles generation of examples and guard code for tool policies.
"""

import asyncio
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple
from loguru import logger

if TYPE_CHECKING:
    from cuga.sdk import CugaAgent

from cuga.backend.cuga_graph.policy.models import ToolGuide


class ToolGuardBuildtimeManager:
    """Manager for build-time tool guard operations.
    
    This class handles generation of examples and guard code for tool policies
    using the toolguard library.
    """

    def __init__(self, agent: "CugaAgent"):
        """Initialize the build-time manager.
        
        Args:
            agent: CugaAgent instance to extract configuration from
        """
        
        from toolguard.buildtime.llm import LangchainModelWrapper
        
        self.agent = agent
        
        # Validate agent has required attributes
        if agent._model is None:
            raise ValueError(
                "Agent model is not initialized. Ensure the CugaAgent has a valid model "
                "before creating ToolGuardBuildtimeManager."
            )
        
        if agent.tool_provider is None:
            raise ValueError(
                "Agent tool_provider is not initialized. Ensure the CugaAgent has a valid "
                "tool_provider before creating ToolGuardBuildtimeManager."
            )
        
        if not agent.cuga_folder:
            raise ValueError(
                "Agent cuga_folder is not set. Ensure the CugaAgent has a valid "
                "cuga_folder path before creating ToolGuardBuildtimeManager."
            )
        
        # Extract LLM - wrap the agent's model for toolguard compatibility
        self.llm = LangchainModelWrapper(agent._model)
        logger.info(f"Initialized ToolGuardBuildtimeManager with {type(agent._model).__name__} via LangchainModelWrapper")
        
        # Extract tool provider
        self.tool_provider = agent.tool_provider
        
        # Create toolguard subdirectory under cuga_folder
        self.toolguard_dir = Path(agent.cuga_folder) / "toolguard"
        self.toolguard_dir.mkdir(parents=True, exist_ok=True)
        logger.debug(f"ToolGuard working directory: {self.toolguard_dir}")
        
        # Store for lazy initialization
        self._langchain_tools = None
        self._tools_dict = None
        self._initialized = False
        self._init_lock = asyncio.Lock()
        

    async def _ensure_initialized(self):
        """Ensure the manager is initialized with tools."""
        async with self._init_lock:
            if self._initialized:
                logger.debug("ToolGuardBuildtimeManager already initialized, skipping")
                return
            
            logger.info("Initializing ToolGuardBuildtimeManager...")
            
            # Get all tools from the provider
            self._langchain_tools = await self.tool_provider.get_all_tools()
            
            # Convert LangChain tools to OpenAPI dict using ToolGuard's utility
            from toolguard.extra.langchain_to_oas import langchain_tools_to_openapi
            self._tools_dict = langchain_tools_to_openapi(self._langchain_tools)  # type: ignore
            
            self._initialized = True
            logger.info(f"✅ ToolGuardBuildtimeManager initialized with {len(self._langchain_tools)} tools")

    def _validate_policy_and_tool(self, policy: ToolGuide, target_tool: str):
        """Validate that policy is a ToolGuide and target_tool is in policy.target_tools.
        
        Args:
            policy: Policy to validate
            target_tool: Tool name to validate
            
        Raises:
            ValueError: If validation fails
        """
        if not isinstance(policy, ToolGuide):
            raise ValueError(f"Policy must be a ToolGuide, got {type(policy).__name__}")
        
        if target_tool not in policy.target_tools:
            raise ValueError(
                f"Tool '{target_tool}' not in policy.target_tools: {policy.target_tools}"
            )

    def _create_spec_item(
        self,
        policy: ToolGuide,
        violating_examples: Optional[List[str]] = None,
        compliance_examples: Optional[List[str]] = None
    ):
        """Create a ToolGuardSpecItem from a policy.
        
        Args:
            policy: ToolGuide policy
            violating_examples: Optional list of violating examples
            compliance_examples: Optional list of compliance examples
            
        Returns:
            ToolGuardSpecItem instance
        """
        from toolguard.runtime.data_types import ToolGuardSpecItem
        
        # Build description from policy
        description = policy.description or ""
        if hasattr(policy, 'guide_content') and policy.guide_content:
            description = f"{description}\n\n{policy.guide_content}"
        
        kwargs = {
            "name": policy.name,
            "description": description,
            "references": [policy.guide_content] if hasattr(policy, 'guide_content') and policy.guide_content else []
        }
        
        if violating_examples is not None:
            kwargs["violation_examples"] = violating_examples
        if compliance_examples is not None:
            kwargs["compliance_examples"] = compliance_examples
        
        return ToolGuardSpecItem(**kwargs)

    @contextmanager
    def _temp_directory(self):
        """Create a temporary directory context manager.
        
        Yields:
            Path to temporary directory
        """
        import tempfile
        import shutil
        
        tmp_dir = Path(tempfile.mkdtemp(prefix="toolguard_"))
        try:
            yield tmp_dir
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _infer_app_name_from_tool(self, target_tool: str) -> str:
        """Infer application name from tool metadata or use default.
        
        Args:
            target_tool: Tool name
            
        Returns:
            Application name string
        """
        # Try to get app_name from tool provider metadata
        if hasattr(self.tool_provider, 'app_name'):
            return self.tool_provider.app_name
        
        # Default fallback
        return "cuga_app"

    def _validate_app_name(self, app_name: str) -> str:
        """Validate app_name to prevent path traversal attacks.
        
        Args:
            app_name: Application name to validate
            
        Returns:
            Validated app_name
            
        Raises:
            ValueError: If app_name contains unsafe characters
        """
        import re
        
        # Only allow alphanumeric, underscore, and hyphen
        if not re.match(r'^[a-zA-Z0-9_-]+$', app_name):
            raise ValueError(
                f"Invalid app_name '{app_name}': must contain only alphanumeric, underscore, or hyphen"
            )
        
        return app_name

    def _save_domain_files(self, result):
        """Save RuntimeDomain files to the toolguard directory.
        
        Args:
            result: ToolGuardsCodeGenerationResult containing domain files
        """
        from toolguard.runtime.data_types import ToolGuardsCodeGenerationResult
        
        if not isinstance(result, ToolGuardsCodeGenerationResult):
            logger.warning(f"Expected ToolGuardsCodeGenerationResult, got {type(result)}")
            return
        
        # Save domain files to domain directory
        work_dir_path = Path(self.toolguard_dir)
        domain_dir = work_dir_path / "domain"
        domain_dir.mkdir(parents=True, exist_ok=True)

        for attr_name in ["app_types", "app_api", "app_api_impl"]:
            domain_file = getattr(result.domain, attr_name)
            domain_file.save(domain_dir)
            logger.info(f"Saved {attr_name} to {domain_dir / domain_file.file_name}")

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
        await self._ensure_initialized()
        self._validate_policy_and_tool(policy, target_tool)

        logger.info(f"Generating examples for tool '{target_tool}'...")

        # Create ToolGuardSpecItem with policy information
        spec_item = self._create_spec_item(policy)

        # Create ToolGuardSpec with the spec item
        from toolguard.buildtime import ToolGuardSpec, generate_guard_examples
        spec = ToolGuardSpec(
            tool_name=target_tool,
            policy_items=[spec_item],
        )

        # Generate examples using toolguard
        with self._temp_directory() as tmp_dir:
            try:
                updated_specs = await generate_guard_examples(
                    tools=self._tools_dict,  # Pass the OpenAPI dict
                    tool_specs=[spec],
                    llm=self.llm,  # type: ignore
                    work_dir=str(tmp_dir),
                    example_number=3,
                )

                # Extract examples from the updated spec
                if updated_specs:
                    updated_spec = updated_specs[0]
                    if updated_spec.policy_items:
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

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(
                    f"❌ Failed to generate examples for tool '{target_tool}': {e}"
                )
                raise RuntimeError(f"Failed to generate examples for tool '{target_tool}'") from e
    
    async def generate_guard_code(
        self,
        policy: ToolGuide,
        target_tool: str,
        app_name: Optional[str] = None
    ) -> str:
        """
        Generate guard code for a specific tool in a ToolGuide policy.

        This method creates a ToolGuardSpec from the policy, validates it has examples,
        calls toolguard's generate_guards_code, saves the RuntimeDomain to a file,
        and returns the generated guard code content.

        Args:
            policy: ToolGuide policy to generate guard code for
            target_tool: Specific tool name to generate guard code for
            app_name: Application name for the generated code. If None, will be auto-detected
                     from tool metadata or default to "cuga_app"

        Returns:
            String containing the generated guard code

        Raises:
            RuntimeError: If manager not initialized
            ValueError: If policy is not a ToolGuide, target_tool not in policy.target_tools,
                       if the policy doesn't have examples for the target tool,
                       or if app_name contains unsafe characters
        """
        await self._ensure_initialized()
        self._validate_policy_and_tool(policy, target_tool)
        
        # Auto-detect app_name if not provided
        if app_name is None:
            app_name = self._infer_app_name_from_tool(target_tool)
            logger.info(f"Auto-detected app_name '{app_name}' for tool '{target_tool}'")
        
        # Validate app_name to prevent path traversal attacks
        app_name = self._validate_app_name(app_name)

        logger.info(f"Generating guard code for tool '{target_tool}' with app_name '{app_name}'...")

        # Check if policy has tool_guards for this specific tool
        tool_guard = None
        if policy.tool_guards and target_tool in policy.tool_guards:
            tool_guard = policy.tool_guards[target_tool]

        # Validate that we have examples (either from tool_guards or need to generate them first)
        if tool_guard:
            violating_examples = tool_guard.violating_examples
            compliance_examples = tool_guard.compliance_examples
        else:
            violating_examples = []
            compliance_examples = []

        # Ensure we have examples
        if not violating_examples and not compliance_examples:
            raise ValueError(
                f"Policy for tool '{target_tool}' must have examples before generating guard code. "
                f"Call generate_examples() first to create examples, or provide them in the policy's tool_guards."
            )

        # Create ToolGuardSpecItem with policy information and examples
        spec_item = self._create_spec_item(
            policy,
            violating_examples=violating_examples,
            compliance_examples=compliance_examples
        )

        # Create ToolGuardSpec with the spec item
        from toolguard.buildtime import ToolGuardSpec, generate_guards_code
        from toolguard.runtime.data_types import ToolGuardsCodeGenerationResult
        
        spec = ToolGuardSpec(
            tool_name=target_tool,
            policy_items=[spec_item]
        )

        # Generate guard code using toolguard
        with self._temp_directory() as tmp_dir:
            try:
                result: ToolGuardsCodeGenerationResult = await generate_guards_code(
                    tools=self._tools_dict,  # Pass the OpenAPI dict
                    tool_specs=[spec],
                    work_dir=str(tmp_dir),
                    llm=self.llm,  # type: ignore
                    app_name=app_name
                )

                # Save RuntimeDomain files directly under toolguard directory (not in tmp)
                self._save_domain_files(result)

                # Extract guard code from the result
                if target_tool in result.tools:
                    tool_result = result.tools[target_tool]
                    
                    # Get the item guard file content (should be only one)
                    if not tool_result.item_guard_files:
                        raise ValueError(
                            f"No item guard files generated for tool '{target_tool}'"
                        )
                    
                    if len(tool_result.item_guard_files) > 1:
                        logger.warning(
                            f"Multiple item guard files found for tool '{target_tool}', using the first one"
                        )
                    
                    item_guard_file = tool_result.item_guard_files[0]
                    if item_guard_file is None:
                        raise ValueError(
                            f"Item guard file is None for tool '{target_tool}'"
                        )
                    
                    guard_code = item_guard_file.content

                    logger.info(
                        f"✅ Generated guard code for tool '{target_tool}' "
                        f"(guard function: {tool_result.guard_fn_name})"
                    )

                    return guard_code
                else:
                    raise ValueError(
                        f"Tool '{target_tool}' not found in generation results. "
                        f"Available tools: {list(result.tools.keys())}"
                    )

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(
                    f"❌ Failed to generate guard code for tool '{target_tool}': {e}"
                )
                raise RuntimeError(f"Failed to generate guard code for tool '{target_tool}'") from e

