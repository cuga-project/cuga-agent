"""Checkpoint cleanup (#774): scheduled retention, and supervisor child threads deleted with the parent.

Retention: idle conversations lose their checkpoint and become legacy. Child threads: only
conversation-scoped sub-agents share the saver, and their recorded IDs are deleted with the parent.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.checkpoint.memory import MemorySaver

from cuga.backend.server import retention
from cuga.backend.server.conversation_history import ConversationHistoryDB, _instance_id, _tenant_id
from cuga.backend.server.thread_lease import ThreadLease

from cuga.backend.storage.checkpoint.children import ChildCheckpointRecorder, recorded_child_ids

pytestmark = pytest.mark.unit

USER = "alice"
AGENT = "agent-1"


def _key(thread_id: str, user_id: str, agent_id: str) -> str:
    return f"{thread_id}:{user_id}:{agent_id}"


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


@pytest.fixture
async def db():
    return ConversationHistoryDB()


@pytest.fixture
def lease():
    return ThreadLease()


@pytest.fixture
def saver():
    saver = MagicMock()
    saver.adelete_thread = AsyncMock()
    saver.aget_tuple = AsyncMock(return_value=None)
    return saver


async def _seed(db: ConversationHistoryDB, thread_id: str, *, status: str, created_days_ago: float):
    await db.create_conversation(thread_id, USER, AGENT)
    store = db._get_store()
    await store.execute(
        """UPDATE runtime_thread_owners SET status = ?, created_at = ?
        WHERE tenant_id = ? AND instance_id = ? AND thread_id = ?""",
        (status, _iso(created_days_ago), _tenant_id(), _instance_id(), thread_id),
    )
    await store.commit()


async def _sweep(db, lease, saver, cutoff_days: float = 90) -> int:
    result = await retention._prune_batch(
        saver=saver,
        conversation_db=db,
        lease=lease,
        checkpoint_thread_id_fn=_key,
        cutoff_iso=_iso(cutoff_days),
    )
    return result.pruned


async def test_idle_conversation_loses_its_checkpoint_and_becomes_legacy(db, lease, saver):
    await _seed(db, "old", status="active", created_days_ago=120)

    assert await _sweep(db, lease, saver) == 1

    saver.adelete_thread.assert_awaited_once_with(_key("old", USER, AGENT))
    assert await db.get_thread_status("old", USER, AGENT) == "legacy"


async def test_recent_and_non_continuable_conversations_are_left_alone(db, lease, saver):
    await _seed(db, "fresh", status="active", created_days_ago=400)
    await db.mark_last_turn_at("fresh", USER)  # a recent turn overrides the old created_at
    await _seed(db, "legacy", status="legacy", created_days_ago=400)
    await _seed(db, "deleted", status="deleted", created_days_ago=400)

    assert await _sweep(db, lease, saver) == 0

    saver.adelete_thread.assert_not_awaited()
    assert await db.get_thread_status("fresh", USER, AGENT) == "active"


async def test_conversation_with_a_running_turn_is_skipped(db, lease, saver):
    await _seed(db, "busy", status="active", created_days_ago=120)
    token = await ThreadLease().acquire(_key("busy", USER, AGENT))

    assert await _sweep(db, lease, saver) == 0

    saver.adelete_thread.assert_not_awaited()
    assert await db.get_thread_status("busy", USER, AGENT) == "active"
    await ThreadLease().release(token)


async def test_failed_delete_keeps_the_conversation_continuable_and_releases_the_lease(db, lease, saver):
    await _seed(db, "flaky", status="active", created_days_ago=120)
    saver.adelete_thread.side_effect = RuntimeError("db down")

    assert await _sweep(db, lease, saver) == 0

    assert await db.get_thread_status("flaky", USER, AGENT) == "active"
    await lease.release(await lease.acquire(_key("flaky", USER, AGENT)))  # lease is free again


async def test_loop_sweeps_each_interval_and_survives_a_failing_sweep(monkeypatch, saver):
    sweeps = []

    async def prune_batch(**kwargs):
        sweeps.append(kwargs["cutoff_iso"])
        if len(sweeps) == 1:
            raise RuntimeError("transient")
        return retention.PruneBatchResult(pruned=0, next_cursor=None)

    monkeypatch.setattr(retention, "_prune_batch", prune_batch)
    task = asyncio.create_task(
        retention.run_retention_loop(
            handle=MagicMock(saver=saver),
            checkpoint_thread_id_fn=_key,
            interval_seconds=0.01,
            retention_days=90,
        )
    )
    try:
        await asyncio.wait_for(_until(lambda: len(sweeps) >= 2), timeout=5.0)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert len(sweeps) >= 2


async def _until(predicate):
    while not predicate():
        await asyncio.sleep(0.01)


# --- supervisor child threads on the shared saver -------------------------------------------


@pytest.mark.parametrize(
    "global_scope, config, shared",
    [
        ("conversation", {"memory_scope": "conversation"}, True),
        ("conversation", {"memory_scope": "call"}, False),
        ("conversation", {"feature_overrides": {"sub_agent_memory_scope": "call"}}, False),
        ("call", {"feature_overrides": {"sub_agent_memory_scope": "conversation"}}, True),
        (
            "call",
            {"memory_scope": "conversation", "feature_overrides": {"sub_agent_memory_scope": "call"}},
            True,
        ),
    ],
)
async def test_only_conversation_scoped_subagents_get_the_shared_saver(
    monkeypatch, global_scope, config, shared
):
    from cuga.config import settings

    monkeypatch.setattr(settings.supervisor, "sub_agent_memory_scope", global_scope)
    sentinel = object()
    captured_kwargs: list[dict] = []

    class FakeCugaAgent:
        def __init__(
            self,
            tools=None,
            tool_provider=None,
            policy_system=None,
            special_instructions=None,
            model=None,
            auto_load_policies=None,
            checkpointer=None,
        ):
            captured_kwargs.append({"checkpointer": checkpointer})
            self._feature_overrides = {}
            self._memory_scope = None

    with patch("cuga.sdk.CugaAgent", FakeCugaAgent):
        from cuga.supervisor_utils.supervisor_config import build_agents_from_list

        await build_agents_from_list(
            [{"name": "agent1", "tools": [], "apps": [], "mcp_servers": [], **config}],
            checkpointer=sentinel,
        )

    assert len(captured_kwargs) == 1
    assert captured_kwargs[0].get("checkpointer") is (sentinel if shared else None)


async def test_recorded_child_survives_checkpoint_and_next_turn_then_cleanup():
    """A child recorded at delegation is deleted with the parent, even after turns without it."""
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph

    from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_state import CugaSupervisorState
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.delegation import _record_child_checkpoint_id
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.execute_agent_tool import (
        _delegation_state_update,
    )
    from cuga.backend.cuga_graph.state.agent_state import AgentState
    from cuga.backend.server.main import (
        _checkpoint_thread_id,
        _delete_child_checkpoints,
        apply_request_user_context,
    )

    child_id = "sup_child_" + "a" * 64
    saver = MemorySaver()
    builder = StateGraph(CugaSupervisorState)

    async def delegate(state):
        _record_child_checkpoint_id(state, child_id)
        _record_child_checkpoint_id(state, child_id)  # a second delegation records the ID once
        return _delegation_state_update(state)

    builder.add_node("delegate", delegate)
    builder.add_edge(START, "delegate")
    builder.add_edge("delegate", END)
    graph = builder.compile(checkpointer=saver)
    key = {"configurable": {"thread_id": _checkpoint_thread_id("parent", "u", "supervisor-agent")}}
    await graph.ainvoke(CugaSupervisorState(thread_id="parent", user_id="u").model_dump(), key)
    first = await graph.aget_state(key)
    assert first.values["service_scope"]["child_checkpoint_ids"] == [child_id]

    next_turn = AgentState(**first.values)
    apply_request_user_context(next_turn, "u")
    assert next_turn.service_scope["child_checkpoint_ids"] == [child_id]

    async def no_delegation(state):
        return {"service_scope": dict(state.service_scope or {})}

    later = StateGraph(CugaSupervisorState)
    later.add_node("NoDelegation", no_delegation)
    later.add_edge(START, "NoDelegation")
    later.add_edge("NoDelegation", END)
    await later.compile(checkpointer=saver).ainvoke(next_turn.model_dump(), key)

    child = StateGraph(AgentState)
    child.add_node("Answer", lambda _state: {"final_answer": "child"})
    child.add_edge(START, "Answer")
    child.add_edge("Answer", END)
    child_key = {"configurable": {"thread_id": child_id}}
    await child.compile(checkpointer=saver).ainvoke(AgentState().model_dump(), child_key)
    assert await saver.aget_tuple(child_key) is not None

    # The later turn did not delegate, as when the child was removed from the roster.
    await _delete_child_checkpoints(
        saver=saver,
        agent_id="supervisor-agent",
        conversation_id="parent",
        user_id="u",
    )
    assert await saver.aget_tuple(child_key) is None


async def test_child_cleanup_aborts_when_parent_checkpoint_read_fails():
    from cuga.backend.server.main import _delete_child_checkpoints

    saver = MagicMock()
    saver.aget_tuple = AsyncMock(side_effect=RuntimeError("saver unavailable"))
    saver.adelete_thread = AsyncMock()

    with pytest.raises(RuntimeError, match="saver unavailable"):
        await _delete_child_checkpoints(
            saver=saver,
            agent_id="supervisor-agent",
            conversation_id="parent",
            user_id="u",
        )
    saver.adelete_thread.assert_not_awaited()


@pytest.mark.parametrize("change", ["recent-turn", "deleted"])
async def test_retention_rechecks_eligibility_after_selection(db, lease, saver, monkeypatch, change):
    await _seed(db, "stale-selection", status="active", created_days_ago=120)
    acquire = lease.acquire

    async def changed_before_acquire(thread_id, ttl_seconds=None):
        if change == "recent-turn":
            await db.mark_last_turn_at("stale-selection", USER)
        else:
            await db.set_thread_status("stale-selection", USER, "deleted")
        return await acquire(thread_id, ttl_seconds=ttl_seconds)

    monkeypatch.setattr(lease, "acquire", changed_before_acquire)
    assert await _sweep(db, lease, saver) == 0
    saver.adelete_thread.assert_not_awaited()
    assert await db.get_thread_status("stale-selection", USER, AGENT) == (
        "active" if change == "recent-turn" else "deleted"
    )


async def test_retention_read_error_does_not_delete_and_releases_lease(db, lease, saver, monkeypatch):
    await _seed(db, "unreadable", status="active", created_days_ago=120)
    monkeypatch.setattr(db, "get_idle_thread_before", AsyncMock(side_effect=RuntimeError("store down")))
    assert await _sweep(db, lease, saver) == 0
    saver.adelete_thread.assert_not_awaited()
    await lease.release(await lease.acquire(_key("unreadable", USER, AGENT)))


async def test_retention_never_moves_deleted_status_back_to_legacy(db, lease, saver):
    await _seed(db, "old", status="active", created_days_ago=120)

    async def concurrent_status_change(thread_id):
        await db.set_thread_status("old", USER, "deleted")

    saver.adelete_thread.side_effect = concurrent_status_change
    await _sweep(db, lease, saver)
    assert await db.get_thread_status("old", USER, AGENT) == "deleted"


async def test_idle_paging_uses_unique_thread_id_for_tied_activity(db):
    for thread_id in ["c", "a", "b"]:
        await _seed(db, thread_id, status="active", created_days_ago=120)
    store = db._get_store()
    await store.execute(
        "UPDATE runtime_thread_owners SET created_at = ? WHERE tenant_id = ? AND instance_id = ?",
        (_iso(120), _tenant_id(), _instance_id()),
    )
    await store.commit()
    first = await db.get_idle_before(_iso(90), limit=2)
    assert [row["thread_id"] for row in first] == ["a", "b"]
    after = (first[-1]["last_activity"], first[-1]["thread_id"])
    second = await db.get_idle_before(_iso(90), limit=2, after=after)
    assert [row["thread_id"] for row in second] == ["c"]


async def test_busy_and_failing_first_page_do_not_starve_later_candidates(db, lease, saver, monkeypatch):
    for thread_id, age in [("busy", 160), ("failure", 150), ("later", 140), ("tail", 130)]:
        await _seed(db, thread_id, status="active", created_days_ago=age)
    token = await lease.acquire(_key("busy", USER, AGENT))

    async def delete(thread_id):
        if thread_id == _key("failure", USER, AGENT):
            raise RuntimeError("checkpoint unavailable")

    saver.adelete_thread.side_effect = delete
    monkeypatch.setattr(retention, "_BATCH_SIZE", 2)
    monkeypatch.setattr(retention, "get_conversation_db", lambda: db)
    monkeypatch.setattr(retention, "get_thread_lease", lambda: lease)
    task = asyncio.create_task(
        retention.run_retention_loop(
            handle=MagicMock(saver=saver),
            checkpoint_thread_id_fn=_key,
            interval_seconds=0.01,
            retention_days=90,
        )
    )

    async def tail_expired():
        while await db.get_thread_status("tail", USER, AGENT) != "legacy":
            await asyncio.sleep(0.01)

    try:
        await asyncio.wait_for(tail_expired(), timeout=5)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await lease.release(token)
    assert await db.get_thread_status("later", USER, AGENT) == "legacy"
    assert await db.get_thread_status("failure", USER, AGENT) == "active"
    assert await db.get_thread_status("busy", USER, AGENT) == "active"


async def test_retention_renews_lease_during_slow_cleanup(db, lease, saver, monkeypatch):
    from cuga.config import settings
    from cuga.backend.server.thread_lease import ThreadBusy

    monkeypatch.setattr(settings.storage, "thread_lease_ttl_seconds", 0.06)
    monkeypatch.setattr(settings.storage, "thread_lease_renew_seconds", 0.01)
    await _seed(db, "slow", status="active", created_days_ago=120)
    started, finish = asyncio.Event(), asyncio.Event()

    async def delete(thread_id):
        started.set()
        await finish.wait()

    saver.adelete_thread.side_effect = delete
    task = asyncio.create_task(_sweep(db, lease, saver))
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        await asyncio.sleep(0.12)  # longer than the original TTL
        with pytest.raises(ThreadBusy):
            await ThreadLease().acquire(_key("slow", USER, AGENT))
    finally:
        finish.set()
        await task
    assert await db.get_thread_status("slow", USER, AGENT) == "legacy"


async def test_retention_stops_before_parent_delete_when_lease_is_lost(db, lease, saver, monkeypatch):
    from cuga.config import settings

    monkeypatch.setattr(settings.storage, "thread_lease_renew_seconds", 0.01)
    await _seed(db, "lost", status="active", created_days_ago=120)
    noticed = asyncio.Event()
    renew = lease.renew

    async def observe_renewal(token, ttl_seconds=None):
        held = await renew(token, ttl_seconds=ttl_seconds)
        if not held:
            noticed.set()
        return held

    async def child_cleanup(*, saver, agent_id, conversation_id, user_id):
        store = lease._get_store()
        await store.execute(
            "UPDATE thread_leases SET holder_id = 'replacement' WHERE thread_id = ?",
            (_key("lost", USER, AGENT),),
        )
        await store.commit()
        await asyncio.wait_for(noticed.wait(), timeout=5)

    monkeypatch.setattr(lease, "renew", observe_renewal)
    monkeypatch.setattr("cuga.backend.server.main._delete_child_checkpoints", child_cleanup)
    assert await _sweep(db, lease, saver) == 0
    saver.adelete_thread.assert_not_awaited()
    assert await db.get_thread_status("lost", USER, AGENT) == "active"


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
