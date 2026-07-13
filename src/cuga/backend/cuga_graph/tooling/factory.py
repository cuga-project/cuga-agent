from __future__ import annotations

from typing import List, Optional

from langchain_core.tools import BaseTool

from cuga.backend.cuga_graph.nodes.cuga_lite.providers.base import ToolProviderInterface
from cuga.backend.cuga_graph.nodes.cuga_lite.providers.combined import CombinedToolProvider
from cuga.backend.cuga_graph.nodes.cuga_lite.providers.langchain import (
    DirectLangChainToolsProvider,
)
from cuga.backend.cuga_graph.tooling.modes import ToolMode
from cuga.backend.cuga_graph.tooling.profile import (
    ToolingCapabilities,
    ToolingProfile,
)

from cuga.backend.cuga_graph.tooling.multi_provider import MultiToolProvider

def build_tooling_profile(
    *,
    tool_mode: ToolMode,
    tools: Optional[List[BaseTool]] = None,
    tool_provider: Optional[ToolProviderInterface] = None,
) -> ToolingProfile:
    """
    Composition-root decision.

    This should be the only place that maps:
      "internal" -> CUGA's normal tool surface
      "external" -> caller-supplied runtime tools only
    """

    if tool_mode == "internal":
        internal_provider = tool_provider or CombinedToolProvider()

        if tools:
            base_provider = MultiToolProvider(
                [
                    internal_provider,
                    DirectLangChainToolsProvider(
                        tools=tools,
                        app_name="runtime_tools",
                    ),
                ]
            )
        else:
            base_provider = internal_provider

        return ToolingProfile(
            base_tool_provider=base_provider,
            capabilities=ToolingCapabilities(
                enable_find_tools=True,
                enable_todos=True,
                enable_skills=True,
                enable_knowledge=True,
                enable_filesystem_tools=True,
                enable_shell_tool=True,
                enable_browser_actions=True,
                enable_legacy_api_path=True,
                enable_supervisor_default_agents=True,
            ),
        )

    if tool_mode == "external":
        return ToolingProfile(
            base_tool_provider=tool_provider
            or DirectLangChainToolsProvider(
                tools=tools or [],
                app_name="external_tools",
            ),
            capabilities=ToolingCapabilities(
                enable_find_tools=False,
                enable_todos=False,
                enable_skills=False,
                enable_knowledge=False,
                enable_filesystem_tools=False,
                enable_shell_tool=False,
                enable_browser_actions=False,
                enable_legacy_api_path=False,
                enable_supervisor_default_agents=False,
            ),
        )

    raise ValueError(
        f"Unsupported tool_mode={tool_mode!r}. "
        "Expected 'internal' or 'external'."
    )