"""
Runtime execution of tool guards for policy enforcement.

This module provides runtime validation of tool calls against registered
ToolGuide policies with policy_code.
"""

from typing import Dict, Any, List, Optional
from loguru import logger

from cuga.backend.cuga_graph.policy.tool_guard.tool_invoker import ToolGuardInvoker
from cuga.backend.cuga_graph.policy.models import ToolGuide, PolicyType
from cuga.backend.cuga_graph.policy.storage import PolicyStorage


class ToolGuardRuntime:
    """
    Runtime system for executing tool guards during tool invocation.
    
    This class:
    1. Initializes a ToolGuardInvoker for tool execution
    2. Loads all ToolGuide policies with policy_code
    3. Creates a mapping: tool_name -> List[ToolGuide with code]
    4. Executes guard validation when tools are called
    """
    
    def __init__(self, tool_provider, policy_storage: PolicyStorage):
        """
        Initialize the ToolGuardRuntime.
        
        Args:
            tool_provider: CUGA's tool provider instance
            policy_storage: PolicyStorage instance to load policies from
        """
        self.tool_provider = tool_provider
        self.policy_storage = policy_storage
        
        # Create ToolGuardInvoker instance
        self.invoker = ToolGuardInvoker(tool_provider)
        
        # Mapping: tool_name -> List[ToolGuide policies with code]
        self.tool_to_guards: Dict[str, List[ToolGuide]] = {}
        
        self._initialized = False
        logger.debug("Created ToolGuardRuntime instance")
    
    async def initialize(self) -> None:
        """
        Initialize the runtime by loading all ToolGuide policies with code.
        
        This method:
        1. Fetches all ToolGuide policies from storage
        2. Filters for policies that have tool_guards with policy_code
        3. Builds the tool_to_guards mapping
        """
        logger.info("Initializing ToolGuardRuntime...")
        
        # Load all ToolGuide policies
        policies = await self.policy_storage.list_policies(
            policy_type=PolicyType.TOOL_GUIDE,
            enabled_only=True
        )
        
        logger.debug(f"Found {len(policies)} ToolGuide policies")
        
        # Build mapping: tool_name -> List[ToolGuide with code]
        for policy in policies:
            # Type guard: ensure we're working with ToolGuide
            if not isinstance(policy, ToolGuide):
                logger.warning(
                    f"Expected ToolGuide but got {type(policy).__name__}, skipping"
                )
                continue
            
            if not policy.tool_guards:
                logger.debug(f"Policy '{policy.name}' has no tool_guards, skipping")
                continue
            
            # Iterate through each tool's guard configuration
            for tool_name, tool_guard in policy.tool_guards.items():
                # Only include if policy_code exists
                if tool_guard.policy_code:
                    if tool_name not in self.tool_to_guards:
                        self.tool_to_guards[tool_name] = []
                    
                    self.tool_to_guards[tool_name].append(policy)
                    logger.debug(
                        f"Registered guard for tool '{tool_name}' "
                        f"from policy '{policy.name}'"
                    )
                else:
                    logger.debug(
                        f"Tool guard for '{tool_name}' in policy '{policy.name}' "
                        f"has no policy_code, skipping"
                    )
        
        self._initialized = True
        logger.info(
            f"✅ ToolGuardRuntime initialized with guards for "
            f"{len(self.tool_to_guards)} tools"
        )
        
        # Log summary of registered guards
        for tool_name, guards in self.tool_to_guards.items():
            logger.debug(
                f"  - Tool '{tool_name}': {len(guards)} guard(s) "
                f"({', '.join(g.name for g in guards)})"
            )
    
    async def guard_tool_call(
        self,
        app_name: str,
        function_name: str,
        arguments: Dict[str, Any]
    ) -> Optional[str]:
        """
        Validate a tool call against registered guards.
        
        This method executes all registered guards for the specified tool.
        If any guard returns an error message, validation fails and that
        error message is returned. If all guards pass (return None), 
        validation succeeds and None is returned.
        
        Args:
            app_name: Name of the application calling the tool
            function_name: Name of the tool/function being called
            arguments: Arguments being passed to the tool
        
        Returns:
            Error message string if validation fails, None if validation passes
        """
        if not self._initialized:
            logger.warning("ToolGuardRuntime not initialized, skipping validation")
            return None
        
        # Check if this tool has any guards
        if function_name not in self.tool_to_guards:
            logger.debug(f"No guards registered for tool '{function_name}'")
            return None
        
        # Get all guards for this tool
        guards = self.tool_to_guards[function_name]
        
        logger.debug(
            f"Validating tool call '{function_name}' against "
            f"{len(guards)} guard(s)"
        )
        
        # Execute each guard's policy_code
        for policy in guards:
            # Ensure policy has tool_guards and the function_name exists
            if not policy.tool_guards or function_name not in policy.tool_guards:
                logger.warning(
                    f"Policy '{policy.name}' missing tool_guard for '{function_name}', skipping"
                )
                continue
            
            tool_guard = policy.tool_guards[function_name]
            
            try:
                logger.debug(
                    f"Executing guard '{policy.name}' for tool '{function_name}'"
                )
                
                # Create execution context with necessary variables
                exec_globals = {
                    'app_name': app_name,
                    'function_name': function_name,
                    'arguments': arguments,
                    'invoker': self.invoker,
                }
                
                # Execute the policy code
                # The policy_code should define a 'validate' function
                exec(tool_guard.policy_code, exec_globals)
                
                # Check if validate function was defined
                if 'validate' not in exec_globals:
                    logger.warning(
                        f"Policy code for '{policy.name}' does not define "
                        f"a 'validate' function, skipping"
                    )
                    continue
                
                validate_fn = exec_globals['validate']
                
                # Call the validate function
                # Support both sync and async validate functions
                import inspect
                if inspect.iscoroutinefunction(validate_fn):
                    error = await validate_fn(
                        app_name=app_name,
                        function_name=function_name,
                        arguments=arguments
                    )
                else:
                    error = validate_fn(
                        app_name=app_name,
                        function_name=function_name,
                        arguments=arguments
                    )
                
                # If validation failed, return the error message
                if error:
                    logger.warning(
                        f"Tool guard '{policy.name}' blocked call to "
                        f"'{function_name}': {error}"
                    )
                    return error
                
                logger.debug(
                    f"Guard '{policy.name}' passed for tool '{function_name}'"
                )
                    
            except Exception as e:
                logger.error(
                    f"Error executing guard '{policy.name}' for tool "
                    f"'{function_name}': {e}",
                    exc_info=True
                )
                # Continue to next guard instead of failing completely
                # This ensures one broken guard doesn't break all validation
                continue
        
        # All guards passed
        logger.debug(f"Tool call '{function_name}' passed all guards")
        return None
    
    @property
    def is_initialized(self) -> bool:
        """Check if the runtime has been initialized."""
        return self._initialized
    
    def get_guarded_tools(self) -> List[str]:
        """
        Get list of tool names that have guards registered.
        
        Returns:
            List of tool names with active guards
        """
        return list(self.tool_to_guards.keys())
    
    def get_guards_for_tool(self, tool_name: str) -> List[ToolGuide]:
        """
        Get all guards registered for a specific tool.
        
        Args:
            tool_name: Name of the tool
        
        Returns:
            List of ToolGuide policies with guards for this tool
        """
        return self.tool_to_guards.get(tool_name, [])


# Made with Bob