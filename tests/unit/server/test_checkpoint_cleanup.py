"""Checkpoint cleanup (#774): scheduled retention, and supervisor child threads deleted with the parent.

Retention: idle conversations lose their checkpoint and become legacy. Child threads: only
conversation-scoped sub-agents share the saver, and their recorded IDs are deleted with the parent.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cuga.backend.server import retention
from cuga.backend.server.conversation_history import ConversationHistoryDB, _instance_id, _tenant_id
from cuga.backend.server.thread_lease import ThreadLease

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
    return await retention._prune_batch(
        saver=saver,
        conversation_db=db,
        lease=lease,
        checkpoint_thread_id_fn=_key,
        cutoff_iso=_iso(cutoff_days),
    )


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
        return 0

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


@pytest.mark.parametrize("scope, shared", [("conversation", True), ("call", False)])
async def test_only_conversation_scoped_subagents_get_the_shared_saver(scope, shared):
    sentinel = object()
    captured_kwargs: list[dict] = []

    class FakeCugaAgent:
        def __init__(self, **kwargs):
            captured_kwargs.append(dict(kwargs))
            self._feature_overrides = {}
            self._memory_scope = None

    with patch("cuga.sdk.CugaAgent", FakeCugaAgent):
        from cuga.supervisor_utils.supervisor_config import build_agents_from_list

        await build_agents_from_list(
            [{"name": "agent1", "memory_scope": scope, "tools": [], "apps": [], "mcp_servers": []}],
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
