"""DirectLangChainToolsProvider: tools grouped into apps by ``metadata["app_name"]``."""

import pytest
from langchain_core.tools import tool

from cuga.backend.cuga_graph.nodes.cuga_lite.providers.langchain import DirectLangChainToolsProvider

pytestmark = pytest.mark.unit


@tool
def gmail_send_email(to: str, body: str) -> str:
    """Send an email."""
    return "sent"


@tool
def gmail_find_email(query: str) -> str:
    """Find emails."""
    return "[]"


@tool
def slack_post_message(channel: str, text: str) -> str:
    """Post a message."""
    return "ok"


@tool
def calculator(expression: str) -> str:
    """Evaluate an expression."""
    return "0"


gmail_send_email.metadata = {"app_name": "gmail"}
gmail_find_email.metadata = {"app_name": "gmail"}
slack_post_message.metadata = {"app_name": "slack"}


@pytest.mark.asyncio
async def test_tools_without_app_metadata_stay_in_one_default_app():
    p = DirectLangChainToolsProvider(tools=[calculator])
    apps = await p.get_apps()
    assert [a.name for a in apps] == ["runtime_tools"]
    assert await p.get_tools("runtime_tools") == [calculator]


@pytest.mark.asyncio
async def test_tools_are_grouped_by_app_metadata():
    p = DirectLangChainToolsProvider(
        tools=[gmail_send_email, slack_post_message, gmail_find_email, calculator]
    )
    apps = await p.get_apps()
    assert [a.name for a in apps] == ["gmail", "slack", "runtime_tools"]
    assert apps[0].description == "gmail (2 tools)"
    assert await p.get_tools("gmail") == [gmail_send_email, gmail_find_email]
    assert await p.get_tools("slack") == [slack_post_message]
    assert await p.get_tools("runtime_tools") == [calculator]
    assert await p.get_tools("unknown") == []
    assert len(await p.get_all_tools()) == 4


@pytest.mark.asyncio
async def test_app_descriptions_and_tools_added_later():
    p = DirectLangChainToolsProvider(tools=[gmail_send_email], app_descriptions={"slack": "Team chat"})
    p.add_tool(slack_post_message)
    apps = {a.name: a.description for a in await p.get_apps()}
    assert apps == {"gmail": "gmail (1 tools)", "slack": "Team chat"}
    assert p.app_of(slack_post_message) == "slack"
    assert p.app_of(calculator) == "runtime_tools"


@pytest.mark.asyncio
async def test_no_tools_still_lists_the_default_app():
    p = DirectLangChainToolsProvider(tools=[])
    assert [a.name for a in await p.get_apps()] == ["runtime_tools"]
    assert await p.get_tools("runtime_tools") == []
