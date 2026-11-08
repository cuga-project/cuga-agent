"""Tool-call hooks on tools that reach CugaAgent after construction: add_tool(), and a hooked custom provider."""

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.tools import tool

from cuga.backend.cuga_graph.nodes.cuga_lite.providers.langchain import DirectLangChainToolsProvider
from cuga.backend.hooks import AgentHooks, HookBlocked
from cuga.sdk import CugaAgent

pytestmark = pytest.mark.unit

calls = []


@tool
async def read(key: str) -> str:
    """Read a value."""
    calls.append(("read", key))
    return "value"


@tool
async def delete(key: str) -> str:
    """Delete a value."""
    calls.append(("delete", key))
    return "deleted"


class BlockDelete(AgentHooks):
    def before_tool_call(self, tool, args):
        return "deletes are not allowed" if tool == "delete" else None


def _agent(**kwargs):
    return CugaAgent(
        model=FakeListChatModel(responses=["unused"]),
        hooks=[BlockDelete()],
        auto_load_policies=False,
        filesystem_sync=False,
        **kwargs,
    )


async def _tool(agent, name):
    return next(t for t in await agent.tool_provider.get_all_tools() if t.name == name)


@pytest.mark.asyncio
async def test_a_tool_added_later_gets_the_tool_call_hooks():
    calls.clear()
    agent = _agent(tools=[read])
    agent.add_tool(delete)
    with pytest.raises(HookBlocked, match="deletes are not allowed"):
        await (await _tool(agent, "delete")).ainvoke({"key": "k"})
    assert await (await _tool(agent, "read")).ainvoke({"key": "k"}) == "value"
    assert calls == [("read", "k")]


@pytest.mark.asyncio
async def test_add_tool_works_with_a_hooked_direct_provider():
    calls.clear()
    agent = _agent(tool_provider=DirectLangChainToolsProvider(tools=[read], app_name="kv"))
    agent.add_tool(delete)  # raised ValueError while the hooks wrapper hid the direct provider
    assert sorted(t.name for t in await agent.tool_provider.get_all_tools()) == ["delete", "read"]
    with pytest.raises(HookBlocked):
        await (await _tool(agent, "delete")).ainvoke({"key": "k"})
    assert await (await _tool(agent, "read")).ainvoke({"key": "k"}) == "value"
    assert calls == [("read", "k")]
