"""Tests for supervisor delegation state recording and adapter parity hooks."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cuga.backend.cuga_graph.nodes.cuga_supervisor.delegation import create_agent_delegation_func
from cuga.backend.cuga_graph.nodes.cuga_supervisor.execution_context import (
    SUPERVISOR_EXEC_KEY,
    SupervisorExecutionContext,
)
from cuga.backend.cuga_graph.nodes.cuga_supervisor.supervisor_graph_adapter import SupervisorGraphAdapter


def _make_adapter(**kwargs):
    return SupervisorGraphAdapter(
        agents=kwargs.get("agents", {}),
        special_instructions=kwargs.get("special_instructions"),
        tool_provider=kwargs.get("tool_provider"),
        base_callbacks=kwargs.get("base_callbacks"),
        task_todos_ref=kwargs.get("task_todos_ref"),
        static_prompt=kwargs.get("static_prompt"),
    )


def test_prepare_system_content_injects_task_todos_ref():
    adapter = _make_adapter(task_todos_ref=[{"text": "Step 1", "status": "pending"}])
    state = SimpleNamespace(task_todos=None)
    result = adapter.prepare_system_content(state, {}, "Base prompt")
    assert "Current task todos" in result
    assert "Step 1" in result


def test_prepare_system_content_falls_back_to_state_task_todos():
    adapter = _make_adapter(task_todos_ref=[])
    state = SimpleNamespace(task_todos=[{"text": "From state", "status": "completed"}])
    result = adapter.prepare_system_content(state, {}, "Base")
    assert "Current Plan" in result
    assert "From state" in result


def test_get_invoke_config_uses_base_callbacks():
    sentinel = object()
    adapter = _make_adapter(base_callbacks=[sentinel])
    result = adapter.get_invoke_config({})
    assert result["callbacks"] == [sentinel]


def test_get_invoke_config_prefers_configurable_callbacks():
    adapter = _make_adapter(base_callbacks=[object()])
    override = object()
    result = adapter.get_invoke_config({"callbacks": [override]})
    assert result["callbacks"] == [override]


def test_record_delegation_updates_state_fields():
    adapter = _make_adapter()
    state = SimpleNamespace(
        selected_agents=[],
        agent_results={},
        agent_variables={},
        agent_chat_messages={},
        metrics={},
    )

    adapter.record_delegation(
        state,
        "crm_agent",
        result=SimpleNamespace(chat_messages=["msg1"]),
        answer="done",
        variables={"order_id": "42"},
    )

    assert state.selected_agents == ["crm_agent"]
    assert state.agent_results["crm_agent"] == "done"
    assert state.agent_variables["crm_agent"] == {"order_id": "42"}
    assert state.agent_chat_messages["crm_agent"] == ["msg1"]
    assert state.metrics["delegation_count"] == 1
    assert state.metrics["last_delegated_agent"] == "crm_agent"


@pytest.mark.asyncio
async def test_delegation_func_records_internal_agent_result():
    from cuga import CugaAgent

    adapter = _make_adapter()
    state = SimpleNamespace(
        selected_agents=[],
        agent_results={},
        agent_variables={},
        agent_chat_messages={},
        metrics={},
    )

    mock_agent = CugaAgent(tools=[])
    mock_result = SimpleNamespace(answer="worker answer", variables={"x": 1}, chat_messages=None)
    mock_agent.invoke = AsyncMock(return_value=mock_result)

    delegate = create_agent_delegation_func(adapter, "worker", mock_agent)

    namespace = {
        SUPERVISOR_EXEC_KEY: SupervisorExecutionContext(state=state, variable_manager=None),
        "delegate": delegate,
    }
    exec(
        "async def _run():\n    return await delegate('do work')\n",
        namespace,
        namespace,
    )
    answer = await namespace["_run"]()

    assert answer == "worker answer"
    assert state.agent_results["worker"] == "worker answer"
    assert state.agent_variables["worker"] == {"x": 1}
    assert state.selected_agents == ["worker"]
