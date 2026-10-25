"""Supervisor child threads on the shared saver (#774)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


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


async def test_record_child_checkpoint_id_appends_each_id_once():
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.delegation import _record_child_checkpoint_id

    class FakeState:
        service_scope: dict = {}

    state = FakeState()
    for child_id in ("sup_child_abc123", "sup_child_abc123", "sup_child_def456"):
        _record_child_checkpoint_id(state, child_id)
    assert state.service_scope["child_checkpoint_ids"] == ["sup_child_abc123", "sup_child_def456"]


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
