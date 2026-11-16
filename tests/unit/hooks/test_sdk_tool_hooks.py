"""Tool-call hooks on tools that reach CugaAgent after construction: add_tool(), and a hooked custom provider."""

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.tools import StructuredTool, tool

from cuga.backend.cuga_graph.nodes.cuga_lite.providers.base import AppDefinition, ToolProviderInterface
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


@pytest.mark.asyncio
async def test_tools_of_a_hooked_provider_are_tracked_once():
    from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import ToolCallTracker

    calls.clear()
    agent = _agent(tool_provider=DirectLangChainToolsProvider(tools=[read], app_name="kv"))
    ToolCallTracker.start_tracking()
    try:
        assert await (await _tool(agent, "read")).ainvoke({"key": "k"}) == "value"
    finally:
        tracked = ToolCallTracker.stop_tracking()
    assert [(c["name"], c["arguments"]) for c in tracked] == [("read", {"key": "k"})]


@pytest.mark.asyncio
async def test_add_tool_works_with_a_caller_toolguard_provider():
    from cuga.backend.cuga_graph.nodes.cuga_lite.providers.toolguard import ToolGuardingToolProvider

    calls.clear()
    guarded = ToolGuardingToolProvider(
        DirectLangChainToolsProvider(tools=[read], app_name="kv"), enabled=False
    )
    agent = _agent(tool_provider=guarded)
    agent.add_tool(delete)  # raised ValueError: the unwrap stopped at the caller's ToolGuard
    assert sorted(t.name for t in await agent.tool_provider.get_all_tools()) == ["delete", "read"]
    with pytest.raises(HookBlocked):
        await (await _tool(agent, "delete")).ainvoke({"key": "k"})


class _SelfRecordingProvider(ToolProviderInterface):
    """Like registry and combined provider tools: each tool records its own call."""

    def __init__(self):
        async def lookup(key: str) -> str:
            from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import ToolCallTracker

            ToolCallTracker.record_call(
                tool_name="lookup", arguments={"key": key}, result="v", operation_id="source-operation"
            )
            return "v"

        self._tools = [StructuredTool.from_function(coroutine=lookup, name="lookup", description="Look up.")]

    async def initialize(self):
        return None

    async def get_apps(self):
        return [AppDefinition(name="src")]

    async def get_tools(self, app_name):
        return list(self._tools)

    async def get_all_tools(self):
        return list(self._tools)


@pytest.mark.asyncio
async def test_a_provider_tool_that_records_itself_is_tracked_once():
    from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import ToolCallTracker

    agent = _agent(tool_provider=_SelfRecordingProvider())
    ToolCallTracker.start_tracking()
    try:
        assert await (await _tool(agent, "lookup")).ainvoke({"key": "k"}) == "v"
    finally:
        tracked = ToolCallTracker.stop_tracking()
    assert len(tracked) == 1
    assert tracked[0]["operation_id"] == "source-operation"


@pytest.mark.asyncio
async def test_a_tool_added_to_a_hooked_direct_provider_is_tracked_once():
    from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import ToolCallTracker

    calls.clear()
    agent = _agent(tool_provider=DirectLangChainToolsProvider(tools=[read], app_name="kv"))

    @tool
    async def count(key: str) -> str:
        """Count values."""
        return "3"

    agent.add_tool(count)
    ToolCallTracker.start_tracking()
    try:
        assert await (await _tool(agent, "count")).ainvoke({"key": "k"}) == "3"
    finally:
        tracked = ToolCallTracker.stop_tracking()
    assert [c["name"] for c in tracked] == ["count"]


def test_stream_keeps_hook_state_in_its_own_run():
    agent = _agent(tools=[read])
    assert not agent.hooks.in_run()
    with agent._hooks_scope("stream-thread"):
        assert agent.hooks.in_run() and agent.hooks.thread_id == "stream-thread"
    assert not agent.hooks.in_run()
