from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from cuga.backend.cuga_graph.nodes.cuga_lite.providers.base import ToolProviderInterface


@dataclass(frozen=True)
class ToolingCapabilities:
    """
    Concrete runtime capabilities.

    Lower-level components should depend on these booleans, not on
    the string value "internal" or "external".
    """

    enable_find_tools: bool = True
    enable_todos: bool = True
    enable_skills: bool = True
    enable_knowledge: bool = True

    enable_filesystem_tools: bool = True
    enable_shell_tool: bool = True

    enable_browser_actions: bool = True
    enable_legacy_api_path: bool = True
    enable_supervisor_default_agents: bool = True

    def as_configurable_overrides(self) -> Dict[str, bool]:
        """
        Values that can be merged into LangGraph configurable.
        These are runtime feature gates, not mode names.
        """
        return {
            "enable_find_tools": self.enable_find_tools,
            "enable_todos": self.enable_todos,
            "skills_enabled": self.enable_skills,
            "enable_knowledge": self.enable_knowledge,
            "enable_filesystem_tools": self.enable_filesystem_tools,
            "enable_shell_tool": self.enable_shell_tool,
            "enable_browser_actions": self.enable_browser_actions,
            "enable_legacy_api_path": self.enable_legacy_api_path,
            "enable_supervisor_default_agents": self.enable_supervisor_default_agents,
        }


@dataclass(frozen=True)
class ToolingProfile:
    """
    Resolved tool surface for one runtime.

    The provider says which external/app/API tools exist.
    The capabilities say which CUGA-native tool-producing systems are enabled.
    """

    base_tool_provider: ToolProviderInterface
    capabilities: ToolingCapabilities