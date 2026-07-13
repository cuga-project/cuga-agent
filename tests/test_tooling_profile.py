from langchain_core.tools import tool

from cuga.backend.cuga_graph.tooling import build_tooling_profile
from cuga.backend.cuga_graph.tooling.multi_provider import MultiToolProvider
from cuga.backend.cuga_graph.nodes.cuga_lite.providers.combined import CombinedToolProvider
from cuga.backend.cuga_graph.nodes.cuga_lite.providers.langchain import (
    DirectLangChainToolsProvider,
)


@tool
def ping() -> str:
    """Return pong."""
    return "pong"


def test_internal_profile_without_runtime_tools_uses_combined_provider():
    profile = build_tooling_profile(tool_mode="internal")

    assert isinstance(profile.base_tool_provider, CombinedToolProvider)
    assert profile.capabilities.enable_find_tools is True
    assert profile.capabilities.enable_todos is True
    assert profile.capabilities.enable_skills is True
    assert profile.capabilities.enable_knowledge is True
    assert profile.capabilities.enable_browser_actions is True
    assert profile.capabilities.enable_supervisor_default_agents is True


def test_internal_profile_with_runtime_tools_augments_internal_provider():
    profile = build_tooling_profile(tool_mode="internal", tools=[ping])

    assert isinstance(profile.base_tool_provider, MultiToolProvider)
    assert profile.capabilities.enable_find_tools is True
    assert profile.capabilities.enable_browser_actions is True


def test_external_profile_uses_runtime_provider_only():
    profile = build_tooling_profile(tool_mode="external", tools=[ping])

    assert isinstance(profile.base_tool_provider, DirectLangChainToolsProvider)
    assert profile.capabilities.enable_find_tools is False
    assert profile.capabilities.enable_todos is False
    assert profile.capabilities.enable_skills is False
    assert profile.capabilities.enable_knowledge is False
    assert profile.capabilities.enable_filesystem_tools is False
    assert profile.capabilities.enable_shell_tool is False
    assert profile.capabilities.enable_browser_actions is False
    assert profile.capabilities.enable_legacy_api_path is False
    assert profile.capabilities.enable_supervisor_default_agents is False