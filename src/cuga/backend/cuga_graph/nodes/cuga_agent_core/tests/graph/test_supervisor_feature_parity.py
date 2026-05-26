"""Phase 11 — Supervisor feature-parity regression tests.

These tests pin behaviors that were added to both Lite and Supervisor during
the modular sandbox refactor but were not directly covered by earlier test
slices:

1. Real ``_SUPERVISOR_LOOP_ADAPTER.get_variable_manager`` returns
   ``state.supervisor_variables_manager`` (Phase-9 coupling fix).
2. ``filter(None, [...])`` join pattern used in both graphs' prepare nodes
   produces the expected combined special-instructions string.
3. ``InvokeResult.variables`` round-trips through ``VariableBridge.extract_values``
   so the variable dict exposed to callers matches what the sub-agent stored.
4. ``split_execution_note`` is non-empty exactly when the plan is split-active,
   ensuring both graphs' ``filter(None, [...])`` joins include it correctly.
5. ``VariableBridge.bridge`` skips entries with ``None`` values so the supervisor
   namespace only contains non-null sub-agent variables.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


# ── 1. Real _SUPERVISOR_LOOP_ADAPTER.get_variable_manager ─────────────────


def test_real_supervisor_adapter_get_variable_manager_returns_supervisor_vm():
    """Phase-9 pin: _SUPERVISOR_LOOP_ADAPTER.get_variable_manager must return
    state.supervisor_variables_manager (not state.variables_manager).

    If this breaks, CodeExecutor will silently write sub-task variables into
    the root agent's manager instead of the supervisor's.
    """
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_graph import (
        _SUPERVISOR_LOOP_ADAPTER,
    )

    sentinel = object()
    state = SimpleNamespace(supervisor_variables_manager=sentinel)
    assert _SUPERVISOR_LOOP_ADAPTER.get_variable_manager(state) is sentinel


# ── 2. filter(None, [...]) join pattern ───────────────────────────────────


def test_special_instructions_join_all_present():
    """All three components (base, skills_section, split_note) are joined with \\n\\n."""
    base = "Do X."
    skills = "<skills>foo</skills>"
    note = "Split active."
    result = "\n\n".join(filter(None, [base, skills, note])) or None
    assert result == "Do X.\n\n<skills>foo</skills>\n\nSplit active."


def test_special_instructions_join_skips_none_and_empty():
    """None and empty-string components are dropped from the join."""
    base = "Do X."
    skills = ""  # empty → filtered out
    note = None  # None → filtered out
    result = "\n\n".join(filter(None, [base, skills, note])) or None
    assert result == "Do X."


def test_special_instructions_join_all_empty_returns_none():
    """When all components are empty, the join returns None (not empty string)."""
    result = "\n\n".join(filter(None, [None, "", None])) or None
    assert result is None


def test_special_instructions_join_only_note_present():
    """Only split note (no base or skills) still produces a valid string."""
    note = "Split active."
    result = "\n\n".join(filter(None, [None, "", note])) or None
    assert result == "Split active."


# ── 3. InvokeResult.variables round-trip ─────────────────────────────────


def test_invoke_result_variables_round_trips_via_extract_values():
    """Variables stored in variables_storage and extracted by VariableBridge
    are identical to what InvokeResult.variables carries.

    This pins the contract between CugaAgent.invoke() and the bridge wiring.
    """
    from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.variable_bridge import VariableBridge
    from cuga.sdk import InvokeResult

    # Simulate what the graph result dict contains
    variables_storage = {
        "order_id": {"value": "ORD-42", "type": "str", "description": ""},
        "total": {"value": 199.99, "type": "float", "description": ""},
    }
    extracted = VariableBridge.extract_values(variables_storage)

    # Build InvokeResult as CugaAgent.invoke() would
    result = InvokeResult(answer="done", variables=extracted)

    assert result.variables["order_id"] == "ORD-42"
    assert result.variables["total"] == pytest.approx(199.99)
    # Metadata (type, description) is stripped — only raw values survive
    assert set(result.variables.keys()) == {"order_id", "total"}


def test_invoke_result_variables_empty_storage_gives_empty_dict():
    """Empty variables_storage → InvokeResult.variables == {}."""
    from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.variable_bridge import VariableBridge
    from cuga.sdk import InvokeResult

    result = InvokeResult(answer="done", variables=VariableBridge.extract_values({}))
    assert result.variables == {}


# ── 4. split_execution_note in filter(None, ...) context ─────────────────


def test_split_note_absent_means_join_skips_it():
    """When split execution is not active, split_execution_note returns '',
    and the join correctly omits it."""
    from cuga.backend.cuga_graph.nodes.cuga_agent_core.policy.execution_policy import (
        ExecutionPlan,
        split_execution_note,
    )

    plan = ExecutionPlan(
        requested_backend="local",
        python_backend="local",
        shell_backend="none",
        filesystem_backend="none",
    )
    note = split_execution_note(plan)
    base = "Base instructions."
    result = "\n\n".join(filter(None, [base, note])) or None
    assert result == "Base instructions."


def test_split_note_present_means_join_includes_it():
    """When split execution is active, the note is non-empty and joins."""
    from cuga.backend.cuga_graph.nodes.cuga_agent_core.policy.execution_policy import (
        ExecutionPlan,
        split_execution_note,
    )

    plan = ExecutionPlan(
        requested_backend="local",
        python_backend="local",
        shell_backend="opensandbox",
        filesystem_backend="none",
    )
    note = split_execution_note(plan)
    base = "Base."
    result = "\n\n".join(filter(None, [base, note])) or None
    assert result is not None
    assert "Base." in result
    assert note in result


# ── 5. VariableBridge.bridge skips None values ────────────────────────────


def test_bridge_skips_none_values():
    """bridge() must not write None values into the target VM.

    None-valued variables are noise from sub-agents that never assigned the
    variable; writing them pollutes the supervisor namespace with un-usable
    placeholders.
    """
    from cuga.backend.cuga_graph.state.agent_state import VariablesManager
    from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.variable_bridge import VariableBridge

    target = VariablesManager()
    bridged = VariableBridge.bridge({"good": 42, "bad": None}, target)

    assert "good" in target.get_variable_names()
    assert target.get_variable("good") == 42
    # None-valued entries must be silently dropped, not written
    assert "bad" not in target.get_variable_names()
    assert "bad" not in bridged
