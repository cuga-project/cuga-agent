from langchain_core.tools import tool

from cuga import CugaAgent
from cuga.backend.cuga_graph.nodes.cuga_lite.providers.langchain import (
    DirectLangChainToolsProvider,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.providers.toolguard import (
    unwrap_tool_provider,
)


@tool
def external_ping() -> str:
    """Return pong."""
    return "pong"


def test_sdk_default_tool_mode_is_internal():
    agent = CugaAgent(
        enable_knowledge=False,
        enable_skills=False,
    )

    assert agent.tool_mode == "internal"
    assert agent.tooling_profile.capabilities.enable_find_tools is True
    assert agent.tooling_profile.capabilities.enable_todos is True


def test_sdk_external_mode_uses_direct_provider_and_disables_internal_capabilities():
    agent = CugaAgent(
        tool_mode="external",
        tools=[external_ping],
        enable_knowledge=False,
        enable_skills=False,
    )

    provider = unwrap_tool_provider(agent.tool_provider)

    assert isinstance(provider, DirectLangChainToolsProvider)
    assert agent.tooling_profile.capabilities.enable_todos is False
    assert agent.tooling_profile.capabilities.enable_skills is False
    assert agent.tooling_profile.capabilities.enable_knowledge is False
    assert agent.tooling_profile.capabilities.enable_browser_actions is False