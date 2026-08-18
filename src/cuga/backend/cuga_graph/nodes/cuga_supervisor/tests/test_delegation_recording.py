"""Tests for supervisor delegation state recording and adapter parity hooks."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from cuga.backend.cuga_graph.nodes.cuga_supervisor.delegation import create_agent_delegation_func
from cuga.backend.cuga_graph.nodes.cuga_supervisor.execution_context import (
    SUPERVISOR_EXEC_KEY,
    SupervisorExecutionContext,
)
from cuga.backend.cuga_graph.nodes.cuga_supervisor.supervisor_graph_adapter import SupervisorGraphAdapter

pytestmark = pytest.mark.unit


def _make_adapter(**kwargs):
    return SupervisorGraphAdapter(
        agents=kwargs.get("agents", {}),
        special_instructions=kwargs.get("special_instructions"),
        tool_provider=kwargs.get("tool_provider"),
        base_callbacks=kwargs.get("base_callbacks"),
        static_prompt=kwargs.get("static_prompt"),
    )


def test_prepare_system_content_injects_run_local_task_todos():
    adapter = _make_adapter()
    state = SimpleNamespace(task_todos=[{"text": "Step 1", "status": "pending"}])
    result = adapter.prepare_system_content(state, {}, "Base prompt")
    assert "Current task todos" in result
    assert "Step 1" in result


def test_prepare_system_content_no_todos_returns_base_prompt():
    adapter = _make_adapter()
    state = SimpleNamespace(task_todos=None)
    assert adapter.prepare_system_content(state, {}, "Base") == "Base"


@pytest.mark.asyncio
async def test_create_update_todos_writes_to_run_local_state_via_exec_context():
    """The supervisor's todos tool persists into the active run's state, not a shared list."""
    from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.todos import create_update_todos_tool
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.execution_context import (
        resolve_supervisor_execution_context,
    )

    def writer(serialized):
        ctx = resolve_supervisor_execution_context()
        if ctx is not None and ctx.state is not None:
            ctx.state.task_todos = serialized

    tool = await create_update_todos_tool(write_todos=writer)

    state_a = SimpleNamespace(task_todos=None)
    state_b = SimpleNamespace(task_todos=None)

    async def run_for(state, text):
        # The runtime injects the context under SUPERVISOR_EXEC_KEY into the executing
        # frame; the resolver scans the call stack for it.
        __supervisor_exec__ = SupervisorExecutionContext(state=state)  # noqa: F841
        await tool.func({"todos": [{"text": text, "status": "pending"}]})

    await run_for(state_a, "Plan A")
    await run_for(state_b, "Plan B")

    # Each run's todos land on its own state — no cross-run bleed.
    assert state_a.task_todos == [{"text": "Plan A", "status": "pending"}]
    assert state_b.task_todos == [{"text": "Plan B", "status": "pending"}]


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
        supervisor_metadata={},
        metrics={},
    )

    adapter.record_delegation(
        state,
        "crm_agent",
        result=SimpleNamespace(
            chat_messages=["msg1"],
            policy_decisions=[
                {
                    "policy_id": "worker-guard",
                    "policy_name": "Worker guard",
                    "policy_type": "intent_guard",
                    "action_type": "block_intent",
                    "stage": "input",
                    "outcome": "blocked",
                }
            ],
        ),
        answer="done",
        variables={"order_id": "42"},
    )

    assert state.selected_agents == ["crm_agent"]
    assert state.agent_results["crm_agent"] == "done"
    assert state.agent_variables["crm_agent"] == {"order_id": "42"}
    assert state.agent_chat_messages["crm_agent"] == ["msg1"]
    decision = state.supervisor_metadata["policy_decisions"][0]
    assert decision["policy_id"] == "worker-guard"
    assert decision["agent_name"] == "crm_agent"
    assert state.metrics["delegation_count"] == 1
    assert state.metrics["last_delegated_agent"] == "crm_agent"


class _FakeCugaAgent:
    pass


class _FakeVM:
    def __init__(self, values):
        self._values = dict(values)

    def get_variable_names(self):
        return list(self._values)

    def get_variable(self, name):
        return self._values.get(name)


def _empty_delegation_state():
    return SimpleNamespace(
        selected_agents=[],
        agent_results={},
        agent_variables={},
        agent_chat_messages={},
        metrics={},
    )


def _fake_agent(*, answer="ok", variables=None, chat_messages=None):
    agent = _FakeCugaAgent()
    agent.invoke = AsyncMock(
        return_value=SimpleNamespace(answer=answer, variables=variables, chat_messages=chat_messages)
    )
    return agent


def _run_delegate(state, mock_agent, source, *, variable_manager=None):
    delegate = create_agent_delegation_func(_make_adapter(), "worker", mock_agent)
    namespace = {
        SUPERVISOR_EXEC_KEY: SupervisorExecutionContext(state=state, variable_manager=variable_manager),
        "delegate": delegate,
    }
    exec(source, namespace, namespace)
    return namespace["_run"]()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delegation_func_records_internal_agent_result():
    state = _empty_delegation_state()
    mock_agent = _fake_agent(answer="worker answer", variables={"x": 1})
    with patch("cuga.sdk.CugaAgent", _FakeCugaAgent):
        answer = await _run_delegate(
            state,
            mock_agent,
            "async def _run():\n    return await delegate('do work')\n",
        )

    assert answer == "worker answer"
    assert state.agent_results["worker"] == "worker answer"
    assert state.agent_variables["worker"] == {"x": 1}
    assert state.selected_agents == ["worker"]
    mock_agent.invoke.assert_awaited_once()
    assert mock_agent.invoke.await_args.kwargs.get("variables") is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delegation_auto_passes_supervisor_variables_when_omitted():
    mock_agent = _fake_agent()
    with patch("cuga.sdk.CugaAgent", _FakeCugaAgent):
        await _run_delegate(
            _empty_delegation_state(),
            mock_agent,
            "async def _run():\n    return await delegate('get account value')\n",
            variable_manager=_FakeVM({"user_id": "user_alice_99"}),
        )

    assert mock_agent.invoke.await_args.kwargs["variables"] == {"user_id": "user_alice_99"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delegation_forwards_explicit_variables():
    mock_agent = _fake_agent()
    with patch("cuga.sdk.CugaAgent", _FakeCugaAgent):
        await _run_delegate(
            _empty_delegation_state(),
            mock_agent,
            "async def _run():\n    return await delegate('get account value', variables=['user_id'])\n",
            variable_manager=_FakeVM({"user_id": "user_alice_99"}),
        )

    assert mock_agent.invoke.await_args.kwargs["variables"] == {"user_id": "user_alice_99"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delegation_forwards_mid_script_explicit_variables():
    mock_agent = _fake_agent()
    with patch("cuga.sdk.CugaAgent", _FakeCugaAgent):
        await _run_delegate(
            _empty_delegation_state(),
            mock_agent,
            (
                "async def _run():\n"
                "    user_id = 'from_script'\n"
                "    return await delegate('get account value', variables=['user_id'])\n"
            ),
            variable_manager=_FakeVM({"user_id": "from_vm"}),
        )

    assert mock_agent.invoke.await_args.kwargs["variables"] == {"user_id": "from_script"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delegation_empty_variables_list_does_not_auto_pass():
    mock_agent = _fake_agent()
    with patch("cuga.sdk.CugaAgent", _FakeCugaAgent):
        await _run_delegate(
            _empty_delegation_state(),
            mock_agent,
            "async def _run():\n    return await delegate('get account value', variables=[])\n",
            variable_manager=_FakeVM({"user_id": "user_alice_99"}),
        )

    assert mock_agent.invoke.await_args.kwargs.get("variables") is None


_A2A_CONFIG = {
    "type": "external",
    "config": {"a2a_protocol": {"endpoint": "http://a2a.test", "transport": "http"}},
}
_A2A_MODULE = "cuga.backend.cuga_graph.nodes.cuga_supervisor.a2a_protocol"
_DELEGATION_PASS_A2A = (
    "cuga.backend.cuga_graph.nodes.cuga_supervisor.delegation.settings.supervisor.pass_variables_a2a"
)
_OMITTED = "async def _run():\n    return await delegate('get account value')\n"
_EXPLICIT = "async def _run():\n    return await delegate('get account value', variables=['user_id'])\n"
_EMPTY = "async def _run():\n    return await delegate('get account value', variables=[])\n"
_VM = _FakeVM({"user_id": "user_alice_99"})


def _exec_delegate(delegate, source, *, variable_manager=None):
    namespace = {
        SUPERVISOR_EXEC_KEY: SupervisorExecutionContext(
            state=_empty_delegation_state(), variable_manager=variable_manager
        ),
        "delegate": delegate,
    }
    exec(source, namespace, namespace)
    return namespace["_run"]()


async def _run_a2a_sdk(source, *, pass_variables=True, variable_manager=_VM):
    sdk = AsyncMock(return_value={"result": "ok"})
    with (
        patch(_DELEGATION_PASS_A2A, pass_variables),
        patch(f"{_A2A_MODULE}.HAS_A2A_SDK", True),
        patch(f"{_A2A_MODULE}.delegate_task_via_a2a_sdk", sdk),
    ):
        delegate = create_agent_delegation_func(_make_adapter(), "worker", _A2A_CONFIG, agent_card=object())
        await _exec_delegate(delegate, source, variable_manager=variable_manager)
    return sdk


async def _run_legacy_a2a(source, *, pass_variables=True, variable_manager=_VM):
    protocol = SimpleNamespace(
        connect=AsyncMock(),
        disconnect=AsyncMock(),
        delegate_task=AsyncMock(return_value={"result": "ok"}),
    )
    with (
        patch(_DELEGATION_PASS_A2A, pass_variables),
        patch(f"{_A2A_MODULE}.A2AProtocol", return_value=protocol),
    ):
        delegate = create_agent_delegation_func(_make_adapter(), "worker", _A2A_CONFIG)
        await _exec_delegate(delegate, source, variable_manager=variable_manager)
    return protocol


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source, expected",
    [(_OMITTED, None), (_EXPLICIT, {"user_id": "user_alice_99"}), (_EMPTY, None)],
    ids=["omitted", "explicit", "empty"],
)
async def test_a2a_sdk_variable_forwarding(source, expected):
    sdk = await _run_a2a_sdk(source)
    sdk.assert_awaited_once()
    assert sdk.await_args.kwargs["variables"] == expected


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source, expected",
    [(_OMITTED, {}), (_EXPLICIT, {"user_id": "user_alice_99"}), (_EMPTY, {})],
    ids=["omitted", "explicit", "empty"],
)
async def test_legacy_a2a_variable_forwarding(source, expected):
    protocol = await _run_legacy_a2a(source)
    protocol.delegate_task.assert_awaited_once()
    assert protocol.delegate_task.await_args.kwargs["variables"] == expected


@pytest.mark.unit
@pytest.mark.asyncio
async def test_legacy_a2a_does_not_send_variables_when_setting_off():
    protocol = await _run_legacy_a2a(_EXPLICIT, pass_variables=False)
    protocol.delegate_task.assert_awaited_once()
    assert protocol.delegate_task.await_args.kwargs["variables"] == {}
