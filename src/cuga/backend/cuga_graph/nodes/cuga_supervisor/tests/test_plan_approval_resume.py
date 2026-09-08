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


@pytest.mark.asyncio
async def test_execute_node_pauses_for_plan_approval():
    from langgraph.graph import END

    from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_state import CugaSupervisorState
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.execute_agent_tool import (
        create_execute_agent_tool_node,
    )
    from cuga.backend.cuga_graph.utils.nodes_names import ActionIds

    adapter = SupervisorGraphAdapter(agents={"crm-agent": object()}, plan_approval=True)
    node = create_execute_agent_tool_node(adapter)
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.prepare_agents_and_prompt import (
        _delegate_tool_name,
    )

    state = CugaSupervisorState(
        input="book a flight",
        script=f"{_delegate_tool_name('crm-agent')}(task='lookup')",
        supervisor_chat_messages=[],
    )
    result = await node(state)
    assert result.goto == END
    assert result.update["hitl_action"].action_id == ActionIds.AGENT_APPROVAL
    assert result.update["supervisor_metadata"]["plan_approved"] is False


@pytest.mark.asyncio
async def test_supervisor_node_approve_sets_plan_approved_and_deny_cancels():
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_node import CugaSupervisorNode
    from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import ActionResponse, ActionType
    from cuga.backend.cuga_graph.utils.nodes_names import ActionIds

    captured = {}

    class _FakeSubgraph:
        async def ainvoke(self, state, config=None):
            captured["plan_approved"] = (state.supervisor_metadata or {}).get("plan_approved")
            return state

    node = CugaSupervisorNode()
    node.set_subgraph(_FakeSubgraph())

    approved = AgentState(
        input="book a flight",
        script="delegate_to_crm_agent(task='lookup')",
        sender="WaitForResponse",
        hitl_response=ActionResponse(
            action_id=ActionIds.AGENT_APPROVAL,
            response_type=ActionType.CONFIRMATION,
            timestamp="t0",
            confirmed=True,
        ),
    )
    await node.node(approved)
    assert captured["plan_approved"] is True

    denied = AgentState(
        input="book a flight",
        script="delegate_to_crm_agent(task='lookup')",
        sender="WaitForResponse",
        hitl_response=ActionResponse(
            action_id=ActionIds.AGENT_APPROVAL,
            response_type=ActionType.CONFIRMATION,
            timestamp="t0",
            confirmed=False,
        ),
    )
    denied_result = await node.node(denied)
    assert denied_result.goto == "FinalAnswerAgent"
    assert "cancelled" in (denied_result.update.get("final_answer") or denied.final_answer or "").lower()
