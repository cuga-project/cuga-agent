"""Isolation of supervisor sub-agent MemorySaver checkpoints (issue #731)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from cuga.backend.cuga_graph.nodes.cuga_supervisor.child_checkpoint import (
    CHILD_CHECKPOINT_PREFIX,
    MEMORY_SCOPE_CALL,
    MEMORY_SCOPE_CONVERSATION,
    child_checkpoint_id,
    child_checkpoint_lock,
    normalize_memory_scope,
    resolve_child_checkpoint_id,
    resolve_memory_scope,
)
from cuga.backend.cuga_graph.nodes.cuga_supervisor.delegation import create_agent_delegation_func
from cuga.backend.cuga_graph.nodes.cuga_supervisor.execution_context import (
    SUPERVISOR_EXEC_KEY,
    SupervisorExecutionContext,
)
from cuga.backend.cuga_graph.nodes.cuga_supervisor.supervisor_graph_adapter import SupervisorGraphAdapter

pytestmark = pytest.mark.unit

_BASE = {
    "tenant_id": "tenant-a",
    "user_id": "user-a",
    "supervisor_id": "crm-supervisor",
    "parent_thread_id": "conv-a",
    "sub_agent_id": "crm-agent",
}


def _state(**kwargs):
    return SimpleNamespace(
        user_id=kwargs.get("user_id", "user-a"),
        thread_id=kwargs.get("thread_id", "conv-a"),
        service_scope={"tenant_id": kwargs.get("tenant_id", "tenant-a"), "instance_id": "i1"},
        selected_agents=[],
        agent_results={},
        agent_variables={},
        agent_chat_messages={},
        metrics={},
    )


def _adapter(supervisor_id="crm-supervisor"):
    return SupervisorGraphAdapter(agents={}, supervisor_id=supervisor_id)


class _FakeCugaAgent:
    def __init__(self, *, memory_scope=None):
        self._memory_scope = memory_scope
        self.invoke = AsyncMock(return_value=SimpleNamespace(answer="ok", variables=None))


def test_same_identity_reuses_conversation_checkpoint():
    assert child_checkpoint_id(**_BASE) == child_checkpoint_id(**_BASE)


@pytest.mark.parametrize(
    "field",
    ["tenant_id", "user_id", "supervisor_id", "parent_thread_id", "sub_agent_id"],
)
def test_each_isolation_field_changes_checkpoint(field):
    other = dict(_BASE)
    other[field] = f"{_BASE[field]}-other"
    assert child_checkpoint_id(**_BASE) != child_checkpoint_id(**other)


def test_checkpoint_id_is_opaque():
    checkpoint = child_checkpoint_id(**_BASE)
    assert checkpoint.startswith(CHILD_CHECKPOINT_PREFIX)
    for value in _BASE.values():
        assert value not in checkpoint


def test_call_nonce_creates_distinct_checkpoints():
    first = child_checkpoint_id(**_BASE, call_nonce="n1")
    second = child_checkpoint_id(**_BASE, call_nonce="n2")
    assert first != second
    assert first != child_checkpoint_id(**_BASE)


def test_normalize_memory_scope_defaults_unknown_to_conversation():
    assert normalize_memory_scope("call") == MEMORY_SCOPE_CALL
    assert normalize_memory_scope("CONVERSATION") == MEMORY_SCOPE_CONVERSATION
    assert normalize_memory_scope("nope") == MEMORY_SCOPE_CONVERSATION
    assert normalize_memory_scope(None) == MEMORY_SCOPE_CONVERSATION


def test_resolve_memory_scope_prefers_agent_attribute():
    agent = SimpleNamespace(
        _memory_scope="call", _feature_overrides={"sub_agent_memory_scope": "conversation"}
    )
    assert resolve_memory_scope(agent) == MEMORY_SCOPE_CALL


def test_resolve_memory_scope_uses_feature_override():
    agent = SimpleNamespace(_feature_overrides={"sub_agent_memory_scope": "call"})
    assert resolve_memory_scope(agent) == MEMORY_SCOPE_CALL


def test_resolve_reuses_id_within_one_parent_conversation():
    adapter = _adapter()
    agent = _FakeCugaAgent()
    state = _state()
    first = resolve_child_checkpoint_id(
        state=state, adapter=adapter, agent_name="crm-agent", agent_or_config=agent
    )
    second = resolve_child_checkpoint_id(
        state=state, adapter=adapter, agent_name="crm-agent", agent_or_config=agent
    )
    assert first == second


def test_resolve_isolates_users_and_conversations():
    adapter = _adapter()
    agent = _FakeCugaAgent()
    user_a = resolve_child_checkpoint_id(
        state=_state(user_id="alice"), adapter=adapter, agent_name="crm-agent", agent_or_config=agent
    )
    user_b = resolve_child_checkpoint_id(
        state=_state(user_id="bob"), adapter=adapter, agent_name="crm-agent", agent_or_config=agent
    )
    other_conv = resolve_child_checkpoint_id(
        state=_state(thread_id="conv-b"), adapter=adapter, agent_name="crm-agent", agent_or_config=agent
    )
    assert user_a != user_b
    assert user_a != other_conv


def test_missing_parent_thread_is_call_scoped():
    adapter = _adapter()
    agent = _FakeCugaAgent()
    state = _state(thread_id=None)
    first = resolve_child_checkpoint_id(
        state=state, adapter=adapter, agent_name="crm-agent", agent_or_config=agent
    )
    second = resolve_child_checkpoint_id(
        state=state, adapter=adapter, agent_name="crm-agent", agent_or_config=agent
    )
    assert first != second


def test_call_scope_is_unique_per_delegation():
    adapter = _adapter()
    agent = _FakeCugaAgent(memory_scope="call")
    state = _state()
    first = resolve_child_checkpoint_id(
        state=state, adapter=adapter, agent_name="crm-agent", agent_or_config=agent
    )
    second = resolve_child_checkpoint_id(
        state=state, adapter=adapter, agent_name="crm-agent", agent_or_config=agent
    )
    assert first != second


def test_adapter_stores_supervisor_id():
    adapter = SupervisorGraphAdapter(agents={}, supervisor_id="sup-9")
    assert adapter._supervisor_id == "sup-9"


def _run_delegate(state, mock_agent, adapter=None):
    delegate = create_agent_delegation_func(adapter or _adapter(), "crm-agent", mock_agent)
    namespace = {
        SUPERVISOR_EXEC_KEY: SupervisorExecutionContext(state=state),
        "delegate": delegate,
    }
    exec("async def _run():\n    return await delegate('do work')\n", namespace, namespace)
    return namespace["_run"]()


@pytest.mark.asyncio
async def test_delegation_uses_isolated_checkpoint_not_shared_name():
    state = _state()
    mock_agent = _FakeCugaAgent()
    with patch("cuga.sdk.CugaAgent", _FakeCugaAgent):
        await _run_delegate(state, mock_agent)

    thread_id = mock_agent.invoke.await_args.kwargs["thread_id"]
    assert thread_id.startswith(CHILD_CHECKPOINT_PREFIX)
    assert "supervisor_conversational_crm-agent" != thread_id
    assert "crm-agent" not in thread_id
    assert "user-a" not in thread_id


@pytest.mark.asyncio
async def test_delegation_reuses_checkpoint_within_conversation():
    state = _state()
    mock_agent = _FakeCugaAgent()
    with patch("cuga.sdk.CugaAgent", _FakeCugaAgent):
        await _run_delegate(state, mock_agent)
        await _run_delegate(state, mock_agent)

    first = mock_agent.invoke.await_args_list[0].kwargs["thread_id"]
    second = mock_agent.invoke.await_args_list[1].kwargs["thread_id"]
    assert first == second


@pytest.mark.asyncio
async def test_delegation_isolates_two_users_on_cached_agent():
    mock_agent = _FakeCugaAgent()
    with patch("cuga.sdk.CugaAgent", _FakeCugaAgent):
        await _run_delegate(_state(user_id="alice", thread_id="t-alice"), mock_agent)
        await _run_delegate(_state(user_id="bob", thread_id="t-bob"), mock_agent)

    alice_id = mock_agent.invoke.await_args_list[0].kwargs["thread_id"]
    bob_id = mock_agent.invoke.await_args_list[1].kwargs["thread_id"]
    assert alice_id != bob_id


@pytest.mark.asyncio
async def test_delegation_call_scope_does_not_reuse_checkpoint():
    state = _state()
    mock_agent = _FakeCugaAgent(memory_scope="call")
    with patch("cuga.sdk.CugaAgent", _FakeCugaAgent):
        await _run_delegate(state, mock_agent)
        await _run_delegate(state, mock_agent)

    first = mock_agent.invoke.await_args_list[0].kwargs["thread_id"]
    second = mock_agent.invoke.await_args_list[1].kwargs["thread_id"]
    assert first != second


@pytest.mark.asyncio
async def test_concurrent_delegations_to_same_checkpoint_are_serialized():
    in_flight = 0
    max_in_flight = 0

    async def _slow_invoke(*_args, **_kwargs):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.05)
        in_flight -= 1
        return SimpleNamespace(answer="ok", variables=None)

    mock_agent = _FakeCugaAgent()
    mock_agent.invoke = _slow_invoke
    state = _state()
    with patch("cuga.sdk.CugaAgent", _FakeCugaAgent):
        await asyncio.gather(_run_delegate(state, mock_agent), _run_delegate(state, mock_agent))

    assert max_in_flight == 1


def test_child_checkpoint_lock_is_shared_for_same_agent_and_id():
    agent = object()
    assert child_checkpoint_lock(agent, "id-1") is child_checkpoint_lock(agent, "id-1")
    assert child_checkpoint_lock(agent, "id-1") is not child_checkpoint_lock(agent, "id-2")
    assert child_checkpoint_lock(agent, "id-1") is not child_checkpoint_lock(object(), "id-1")
