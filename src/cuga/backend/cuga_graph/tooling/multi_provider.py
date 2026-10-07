from __future__ import annotations

from typing import List

from langchain_core.tools import BaseTool

from cuga.backend.cuga_graph.nodes.cuga_lite.providers.base import (
    AppDefinition,
    ToolProviderInterface,
)


class MultiToolProvider(ToolProviderInterface):
    """
    Compose multiple ToolProviderInterface instances into one provider.

    This lets internal mode expose CUGA's normal tools plus optional
    caller-supplied runtime tools.
    """

    def __init__(self, providers: List[ToolProviderInterface]):
        self.providers = providers
        self.initialized = False

    async def initialize(self):
        for provider in self.providers:
            if not getattr(provider, "initialized", False):
                await provider.initialize()
        self.initialized = True

    async def get_apps(self) -> List[AppDefinition]:
        if not self.initialized:
            await self.initialize()

        apps: List[AppDefinition] = []
        seen = set()

        for provider in self.providers:
            for app in await provider.get_apps():
                app_name = getattr(app, "name", None)
                if app_name in seen:
                    continue
                seen.add(app_name)
                apps.append(app)

        return apps

    async def get_tools(self, app_name: str) -> List[BaseTool]:
        if not self.initialized:
            await self.initialize()

        tools: List[BaseTool] = []
        seen = set()

        for provider in self.providers:
            for tool in await provider.get_tools(app_name):
                tool_name = getattr(tool, "name", None)
                if tool_name in seen:
                    continue
                seen.add(tool_name)
                tools.append(tool)

        return tools

    async def get_all_tools(self) -> List[BaseTool]:
        if not self.initialized:
            await self.initialize()

        tools: List[BaseTool] = []
        seen = set()

        for provider in self.providers:
            for tool in await provider.get_all_tools():
                tool_name = getattr(tool, "name", None)
                if tool_name in seen:
                    continue
                seen.add(tool_name)
                tools.append(tool)

        return tools