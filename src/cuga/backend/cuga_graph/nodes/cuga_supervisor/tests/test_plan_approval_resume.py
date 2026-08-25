"""Approved supervisor plans must execute the frozen script, not re-plan."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.prepare_agents_and_prompt import (
    next_node_after_prepare,
)
from cuga.backend.cuga_graph.nodes.cuga_supervisor.supervisor_graph_adapter import SupervisorGraphAdapter
from cuga.backend.cuga_graph.state.agent_state import AgentState

pytestmark = pytest.mark.unit


def test_agent_state_persists_script_across_hitl_boundary():
    state = AgentState(input="book a flight", script="delegate_to_crm_agent(task='lookup')")
    restored = AgentState(**state.model_dump())
    assert restored.script == "delegate_to_crm_agent(task='lookup')"


def test_prepare_skips_call_model_when_plan_already_approved():
    adapter = SupervisorGraphAdapter(agents={"crm-agent": object()}, plan_approval=True)
    state = SimpleNamespace(
        script="delegate_to_crm_agent(task='lookup')",
        supervisor_metadata={"plan_approved": True},
    )
    assert next_node_after_prepare(adapter, state) == "execute_agent_tool"


def test_prepare_still_calls_model_without_approved_script():
    adapter = SupervisorGraphAdapter(agents={"crm-agent": object()}, plan_approval=True)
    state = SimpleNamespace(script=None, supervisor_metadata={"plan_approved": True})
    assert next_node_after_prepare(adapter, state) == "call_model"


def test_prepare_calls_model_when_plan_approval_is_off():
    adapter = SupervisorGraphAdapter(agents={"crm-agent": object()}, plan_approval=False)
    state = SimpleNamespace(
        script="delegate_to_crm_agent(task='lookup')",
        supervisor_metadata={"plan_approved": True},
    )
    assert next_node_after_prepare(adapter, state) == "call_model"
