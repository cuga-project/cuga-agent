"""Phase 2 wiring: CodeExecutor accepts an explicit ExecutionPlan and an
explicit variable_manager, without changing behavior for existing callers.

- explicit ``variable_manager`` is used instead of ``state.variables_manager``
  (foundation for the supervisor variable-coupling fix, phase 9);
- ``plan.python_backend`` drives the e2b-vs-local choice when ``mode`` is
  not explicitly given;
- an explicit ``mode`` argument still wins over the plan.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from cuga.backend.cuga_graph.state.agent_state import AgentState, VariablesManager
from cuga.backend.cuga_graph.nodes.cuga_lite.executors import CodeExecutor
from cuga.backend.cuga_graph.nodes.cuga_agent_core.policy.execution_policy import ExecutionPlan


def _plan(python_backend: str) -> ExecutionPlan:
    return ExecutionPlan(
        requested_backend=python_backend,
        python_backend=python_backend,
        shell_backend="none",
        filesystem_backend="none",
    )


@pytest.fixture
def mock_state():
    state = Mock(spec=AgentState)
    state.variables_manager = VariablesManager()
    return state


@pytest.mark.asyncio
async def test_explicit_variable_manager_is_used_instead_of_state(mock_state):
    explicit_vm = VariablesManager()

    await CodeExecutor.eval_with_tools_async(
        code="result = 41 + 1\nprint(result)",
        _locals={},
        state=mock_state,
        mode="local",
        variable_manager=explicit_vm,
    )

    assert "result" in explicit_vm.get_variable_names()
    assert "result" not in mock_state.variables_manager.get_variable_names()


@pytest.mark.asyncio
async def test_omitting_variable_manager_falls_back_to_state(mock_state):
    await CodeExecutor.eval_with_tools_async(
        code="result = 7\nprint(result)",
        _locals={},
        state=mock_state,
        mode="local",
    )

    assert "result" in mock_state.variables_manager.get_variable_names()


@pytest.mark.asyncio
async def test_plan_python_backend_e2b_routes_to_e2b_executor(mock_state, monkeypatch):
    called = {}

    class FakeE2B:
        async def execute_for_cuga_lite(self, *, wrapped_code, context_locals, state, thread_id, apps_list):
            called["e2b"] = True
            return "ok", {}

    monkeypatch.setattr(CodeExecutor, "_get_e2b_executor", classmethod(lambda cls: FakeE2B()))

    await CodeExecutor.eval_with_tools_async(
        code="print('hi')",
        _locals={},
        state=mock_state,
        plan=_plan("e2b"),
    )

    assert called.get("e2b") is True


@pytest.mark.asyncio
async def test_supervisor_adapter_variable_manager_routes_to_supervisor_vm(mock_state):
    """Phase-9 regression guard: passing supervisor_variables_manager as variable_manager
    routes new variables to that manager, not to state.variables_manager.

    The structural fix (passing the adapter's get_variable_manager result to
    eval_with_tools_async inside execute_agent_tool) is validated here via the
    mechanism that the wiring enables — the closure itself cannot be isolated
    without full graph construction.
    """
    sup_vm = VariablesManager()
    mock_state.supervisor_variables_manager = sup_vm

    await CodeExecutor.eval_with_tools_async(
        code="phase9_var = 99\nprint(phase9_var)",
        _locals={},
        state=mock_state,
        mode="local",
        variable_manager=sup_vm,
    )

    assert "phase9_var" in sup_vm.get_variable_names()
    assert "phase9_var" not in mock_state.variables_manager.get_variable_names()


@pytest.mark.asyncio
async def test_explicit_mode_overrides_plan(mock_state, monkeypatch):
    called = {}

    class FakeE2B:
        async def execute_for_cuga_lite(self, **kwargs):
            called["e2b"] = True
            return "ok", {}

    monkeypatch.setattr(CodeExecutor, "_get_e2b_executor", classmethod(lambda cls: FakeE2B()))

    result, new_vars = await CodeExecutor.eval_with_tools_async(
        code="result = 5\nprint(result)",
        _locals={},
        state=mock_state,
        mode="local",
        plan=_plan("e2b"),
    )

    assert called.get("e2b") is None
    assert new_vars.get("result") == 5
