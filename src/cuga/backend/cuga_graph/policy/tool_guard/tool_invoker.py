"""
ToolGuard invoker for CUGA's tool provider.

This module provides integration between toolguard's runtime validation
and CUGA's tool provider system.
"""

import asyncio
from typing import Any, Dict, Optional, Type, TypeVar
from loguru import logger

from toolguard.runtime import IToolInvoker

T = TypeVar('T')


class ToolGuardInvoker(IToolInvoker):
    """
    Tool invoker that uses CUGA's tool provider for executing tools
    during toolguard validation.

    This class bridges toolguard's runtime validation with CUGA's
    tool execution system, allowing guards to invoke tools for
    validation purposes.

    Similar to LangchainToolInvoker and MCPToolInvoker from the toolguard
    library, but adapted to work with CUGA's tool provider.
    """

    def __init__(self, tool_provider):
        """
        Initialize the ToolGuardInvoker.

        Args:
            tool_provider: CUGA's tool provider instance that manages
                          and executes tools
        """
        self.tool_provider = tool_provider
        self._tools_cache: Optional[Dict[str, Any]] = None
        logger.debug("Initialized ToolGuardInvoker with CUGA tool provider")

    async def _get_tools(self) -> Dict[str, Any]:
        """
        Get all available tools from the tool provider.

        Returns:
            Dictionary mapping tool names to tool instances

        Raises:
            ValueError: If duplicate tool names are detected
        """
        if self._tools_cache is None:
            if hasattr(self.tool_provider, "get_all_raw_tools"):
                tools_list = await self.tool_provider.get_all_raw_tools()
            else:
                tools_list = await self.tool_provider.get_all_tools()

            # Check for duplicate tool names before building cache
            tools_map: Dict[str, Any] = {}
            for tool in tools_list:
                if tool.name in tools_map:
                    raise ValueError(
                        f"Duplicate tool name detected: '{tool.name}'. "
                        f"Tool names must be unique across all providers to ensure "
                        f"correct routing of guards to tools."
                    )
                tools_map[tool.name] = tool

            self._tools_cache = tools_map
            logger.debug(f"Cached {len(self._tools_cache)} tools")
        return self._tools_cache

    async def invoke(self, toolname: str, arguments: Dict[str, Any], return_type: Type[T]) -> T:
        """
        Invoke a tool by name with the given arguments.

        This method is called by toolguard during guard validation
        to execute tools and verify their behavior.

        Args:
            toolname: Name of the tool to invoke
            arguments: Dictionary of arguments to pass to the tool
            return_type: Expected return type for the tool invocation

        Returns:
            The result of the tool invocation, cast to the expected type

        Raises:
            ValueError: If the tool is not found
            RuntimeError: If tool invocation fails
        """
        try:
            # Redact sensitive arguments before logging
            arg_summary = {
                k: f"<{type(v).__name__}>" if v is not None else None
                for k, v in (arguments.items() if isinstance(arguments, dict) else {})
            }
            logger.debug(f"Invoking tool '{toolname}' with arg keys: {list(arg_summary.keys())}")

            # Get the tool from the provider
            tools = await self._get_tools()

            if toolname not in tools:
                available_tools = list(tools.keys())
                raise ValueError(f"Tool '{toolname}' not found. Available tools: {available_tools}")

            tool = tools[toolname]

            # Invoke the tool using LangChain's invoke method
            # LangChain tools typically accept a single input or dict of inputs
            result = await tool.ainvoke(arguments)

            logger.debug(f"Tool '{toolname}' invocation successful")
            return result

        except ValueError:
            # Re-raise ValueError as-is (tool not found)
            raise
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Failed to invoke tool '{toolname}': {e}")
            raise RuntimeError(f"Tool invocation failed for '{toolname}': {str(e)}") from e

    def clear_cache(self) -> None:
        """
        Clear the cached tools.

        Call this method if tools are added or removed from the
        tool provider after initialization.
        """
        self._tools_cache = None
        logger.debug("Cleared tools cache")
