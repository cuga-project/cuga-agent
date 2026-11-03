"""Direct LangChain Tools Provider

Provides LangChain tools that are passed directly at runtime (in-process).
"""

from __future__ import annotations

from typing import Dict, List, Optional
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

    Tools are grouped into apps by ``tool.metadata["app_name"]``; tools without it go to the default app
    (``app_name``, ``runtime_tools`` by default). The apps then appear in the prompt and in ``find_tools`` like
    registry or MCP apps:

        ```python
        send = StructuredTool.from_function(gmail_send_email, metadata={"app_name": "gmail"})
        my_tool.metadata = {"app_name": "crm"}  # or set it on an existing tool

        agent = CugaAgent(tools=[send, my_tool])  # apps: gmail, crm
        ```
    """

    APP_METADATA_KEY = "app_name"

    def __init__(
        self,
        tools: Optional[List[BaseTool]] = None,
        app_name: str = "runtime_tools",
        app_descriptions: Optional[Dict[str, str]] = None,
    ):
        """Initialize the direct tools provider.

        Args:
            tools: List of LangChain BaseTool or StructuredTool instances
            app_name: Name of the default app, for tools without ``metadata["app_name"]``
            app_descriptions: Optional description per app name, shown with the app
        """
        self.tools = tools or []
        self.app_name = app_name
        self.app_descriptions = dict(app_descriptions or {})
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

    def app_of(self, tool: BaseTool) -> str:
        """The app a tool belongs to: ``tool.metadata["app_name"]``, else the default app."""
        name = (getattr(tool, "metadata", None) or {}).get(self.APP_METADATA_KEY)
        return str(name) if name else self.app_name

    def _groups(self) -> Dict[str, List[BaseTool]]:
        groups: Dict[str, List[BaseTool]] = {}
        for tool in self.tools:
            groups.setdefault(self.app_of(tool), []).append(tool)
        return groups

    async def get_apps(self) -> List[AppDefinition]:
        """Get the list of applications: one per ``metadata["app_name"]`` group, in first-seen order.

        Returns:
            One AppDefinition per app; the default app only when no tool names another app, or for the
            tools that name none
        """
        if not self.initialized:
            await self.initialize()

        groups = self._groups() or {self.app_name: []}
        return [
            AppDefinition(
                name=name,
                url=None,
                description=self.app_descriptions.get(name)
                or (
                    f"Runtime LangChain tools ({len(tools)} tools)"
                    if name == self.app_name
                    else f"{name} ({len(tools)} tools)"
                ),
                type="langchain",
            )
            for name, tools in groups.items()
        ]

    async def get_tools(self, app_name: str) -> List[StructuredTool]:
        """Get tools for the specified app.

        Args:
            app_name: Name of the application (an app from ``get_apps``)

        Returns:
            The app's LangChain tools, or an empty list for an unknown app
        """
        if not self.initialized:
            await self.initialize()

        groups = self._groups()
        if app_name not in groups:
            if app_name != self.app_name:
                logger.warning(f"App '{app_name}' not found in DirectLangChainToolsProvider")
            return []

        return groups[app_name]

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
