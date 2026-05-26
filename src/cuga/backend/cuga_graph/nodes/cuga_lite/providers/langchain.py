"""Direct LangChain Tools Provider

Provides LangChain tools that are passed directly at runtime (in-process).
"""

from __future__ import annotations

from typing import List, Optional
from langchain_core.tools import BaseTool, StructuredTool
from loguru import logger

from cuga.backend.cuga_graph.nodes.cuga_lite.providers.base import (
    AppDefinition,
    ToolProviderInterface,
)


class DirectLangChainToolsProvider(ToolProviderInterface):
    """Tool provider for direct LangChain tools (in-process).

    This provider accepts LangChain tools directly at initialization time.
    Useful when CUGA is embedded as a component in another system.

    Example:
        ```python
        from langchain_core.tools import tool

        @tool
        def my_tool(query: str) -> str:
            '''A custom tool'''
            return "result"

        provider = DirectLangChainToolsProvider(tools=[my_tool])
        agent = CugaAgent(tool_provider=provider)
        ```
    """

    def __init__(self, tools: Optional[List[BaseTool]] = None, app_name: str = "runtime_tools"):
        """Initialize the direct tools provider.

        Args:
            tools: List of LangChain BaseTool or StructuredTool instances
            app_name: Name to use for the virtual app containing these tools
        """
        self.tools = tools or []
        self.app_name = app_name
        self.initialized = False

        self._validate_tools()

    def _validate_tools(self):
        """Validate that all tools are valid LangChain tools."""
        for i, tool in enumerate(self.tools):
            if not isinstance(tool, BaseTool):
                raise ValueError(
                    f"Tool at index {i} is not a valid LangChain tool. "
                    f"Got {type(tool).__name__}, expected BaseTool or StructuredTool."
                )

            if not hasattr(tool, "name") or not tool.name:
                raise ValueError(f"Tool at index {i} is missing a name")

            if isinstance(tool, StructuredTool) and not hasattr(tool, "func"):
                logger.warning(
                    f"StructuredTool '{tool.name}' is missing .func attribute. "
                    f"Adding it for CodeAct compatibility."
                )
                if hasattr(tool, "coroutine") and tool.coroutine:
                    tool.func = tool.coroutine
                elif hasattr(tool, "_run"):
                    tool.func = tool._run

    async def initialize(self):
        """Initialize the provider (validates tools)."""
        logger.info(f"Initializing DirectLangChainToolsProvider with {len(self.tools)} tools")

        if not self.tools:
            logger.warning("DirectLangChainToolsProvider initialized with no tools")

        for tool in self.tools:
            logger.debug(
                f"  - {tool.name}: {tool.description[:100] if tool.description else 'No description'}"
            )

        self.initialized = True

    async def get_apps(self) -> List[AppDefinition]:
        """Get list of applications (single virtual app for runtime tools).

        Returns:
            List with one AppDefinition representing the runtime tools
        """
        if not self.initialized:
            await self.initialize()

        return [
            AppDefinition(
                name=self.app_name,
                url=None,
                description=f"Runtime LangChain tools ({len(self.tools)} tools)",
                type="langchain",
            )
        ]

    async def get_tools(self, app_name: str) -> List[StructuredTool]:
        """Get tools for the specified app.

        Args:
            app_name: Name of the application (should match self.app_name)

        Returns:
            List of LangChain tools if app_name matches, empty list otherwise
        """
        if not self.initialized:
            await self.initialize()

        if app_name != self.app_name:
            logger.warning(f"App '{app_name}' not found in DirectLangChainToolsProvider")
            return []

        return self.tools

    async def get_all_tools(self) -> List[StructuredTool]:
        """Get all available tools.

        Returns:
            List of all LangChain tools
        """
        if not self.initialized:
            await self.initialize()

        return self.tools

    def add_tool(self, tool: BaseTool):
        """Add a tool dynamically after initialization.

        Args:
            tool: LangChain BaseTool or StructuredTool instance
        """
        if not isinstance(tool, BaseTool):
            raise ValueError(f"Tool must be a LangChain BaseTool, got {type(tool).__name__}")

        self.tools.append(tool)
        logger.info(f"Added tool '{tool.name}' to DirectLangChainToolsProvider")

    def add_tools(self, tools: List[BaseTool]):
        """Add multiple tools dynamically after initialization.

        Args:
            tools: List of LangChain BaseTool or StructuredTool instances
        """
        for tool in tools:
            self.add_tool(tool)
