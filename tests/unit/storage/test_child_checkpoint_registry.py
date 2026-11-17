"""Child ownership is durable before invocation, even when parent state is lost."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langgraph.checkpoint.memory import MemorySaver

from cuga.backend.storage.checkpoint.children import ChildCheckpointRecorder, recorded_child_ids

pytestmark = pytest.mark.unit


async def test_parallel_recording_is_deduplicated_and_parent_deletion_removes_registry():
    saver = MemorySaver()
    record = ChildCheckpointRecorder(saver, "parent")
    await asyncio.gather(*(record(child) for child in ["one", "two", "one", "three"]))
    assert set(await recorded_child_ids(saver, "parent")) == {"one", "two", "three"}
    # The registry never creates or advances the execution checkpoint.
    assert await saver.aget_tuple({"configurable": {"thread_id": "parent"}}) is None
    await saver.adelete_thread("parent")
    assert await recorded_child_ids(saver, "parent") == []


async def test_registry_survives_sqlite_reopen(tmp_path):
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    path = str(tmp_path / "checkpoint.db")
    async with AsyncSqliteSaver.from_conn_string(path) as saver:
        await ChildCheckpointRecorder(saver, "parent")("one")
    async with AsyncSqliteSaver.from_conn_string(path) as saver:
        await ChildCheckpointRecorder(saver, "parent")("two")
        assert await recorded_child_ids(saver, "parent") == ["one", "two"]


@pytest.mark.parametrize("stopped", [False, True], ids=["child-error", "user-stop"])
async def test_child_is_registered_before_it_runs_and_cleanup_survives_lost_parent_update(
    monkeypatch, stopped
):
    from typing_extensions import TypedDict
    from langgraph.graph import StateGraph, START, END
    from cuga.backend.cuga_graph.nodes.cuga_supervisor import delegation
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.execution_context import SupervisorExecutionContext
    from cuga.backend.server.main import _checkpoint_thread_id, _delete_child_checkpoints
    from cuga.sdk import CugaAgent

    saver = MemorySaver()
    parent_key = _checkpoint_thread_id("parent", "alice", "supervisor")
    state = SimpleNamespace(thread_id="parent", user_id="alice", service_scope={})
    context = SupervisorExecutionContext(
        state=state, child_checkpoint_recorder=ChildCheckpointRecorder(saver, parent_key)
    )
    monkeypatch.setattr(delegation, "resolve_supervisor_execution_context", lambda: context)

    class ChildState(TypedDict):
        value: str

    builder = StateGraph(ChildState)
    builder.add_node("answer", lambda state: state)
    builder.add_edge(START, "answer")
    builder.add_edge("answer", END)
    graph = builder.compile(checkpointer=saver)
    child_id = None

    async def invoke(task, *, thread_id, variables):
        nonlocal child_id
        child_id = thread_id
        assert await recorded_child_ids(saver, parent_key) == [thread_id]
        await graph.ainvoke({"value": "conversation content"}, {"configurable": {"thread_id": thread_id}})
        if stopped:
            raise asyncio.CancelledError()
        raise RuntimeError("child failed")

    child = object.__new__(CugaAgent)
    child._memory_scope = "conversation"
    child.invoke = invoke
    delegate = delegation.create_agent_delegation_func(
        SimpleNamespace(_supervisor_id="supervisor"), "child", child
    )
    with pytest.raises(asyncio.CancelledError if stopped else RuntimeError):
        await delegate("work")
    # Simulate a parent exit that drops all in-memory delegation updates.
    state.service_scope = {}
    assert await saver.aget_tuple({"configurable": {"thread_id": child_id}}) is not None
    await _delete_child_checkpoints(
        saver=saver, agent_id="supervisor", conversation_id="parent", user_id="alice"
    )
    assert await saver.aget_tuple({"configurable": {"thread_id": child_id}}) is None


async def test_failed_registry_write_prevents_child_invocation(monkeypatch):
    from cuga.backend.cuga_graph.nodes.cuga_supervisor import delegation
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.execution_context import SupervisorExecutionContext
    from cuga.sdk import CugaAgent

    saver = MemorySaver()
    monkeypatch.setattr(saver, "aput", AsyncMock(side_effect=RuntimeError("store down")))
    context = SupervisorExecutionContext(
        state=SimpleNamespace(thread_id="parent", service_scope={}),
        child_checkpoint_recorder=ChildCheckpointRecorder(saver, "parent"),
    )
    monkeypatch.setattr(delegation, "resolve_supervisor_execution_context", lambda: context)
    child = object.__new__(CugaAgent)
    child._memory_scope = "conversation"
    child.invoke = AsyncMock()
    delegate = delegation.create_agent_delegation_func(SimpleNamespace(), "child", child)
    with pytest.raises(RuntimeError, match="store down"):
        await delegate("work")
    child.invoke.assert_not_awaited()


def test_trusted_recorder_is_forwarded_to_graph_runtime_config():
    from unittest.mock import MagicMock
    from cuga.backend.cuga_graph.utils.agent_loop import AgentLoop

    graph = MagicMock()
    recorder = ChildCheckpointRecorder(MemorySaver(), "trusted-parent-key")
    loop = AgentLoop(
        thread_id="visible-parent",
        checkpoint_thread_id="trusted-parent-key",
        graph=graph,
        langfuse_handler=None,
        tracker=MagicMock(),
        child_checkpoint_recorder=recorder,
    )
    loop.get_stream(None)
    config = graph.astream.call_args.kwargs["config"]["configurable"]
    assert config["child_checkpoint_recorder"] is recorder
    assert config["thread_id"] == "trusted-parent-key"
