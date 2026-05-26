"""Phase 8 — VariableBridge tests.

Pins three behaviors:
1. VariableBridge.extract_values converts variables_storage format → {name: value}.
2. VariableBridge.bridge copies values into a target VariablesManager.
3. InvokeResult carries a `variables` field (default empty, populated from
   sub-agent graph state after invocation).
4. Mechanism test: delegate_to_agent bridges variables via the shared VM ref
   that execute_agent_tool populates before each code run.
"""

from __future__ import annotations

from typing import Any, List

from cuga.backend.cuga_graph.state.agent_state import VariablesManager


# ── VariableBridge utility ─────────────────────────────────────────────────


def test_extract_values_returns_name_value_dict():
    """extract_values strips metadata, leaving {name: raw_value}."""
    from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.variable_bridge import VariableBridge

    storage = {
        "amount": {"value": 99, "description": "desc", "type": "int", "created_at": "...", "count_items": 0},
        "name": {"value": "Alice", "description": "", "type": "str", "created_at": "...", "count_items": 5},
    }
    result = VariableBridge.extract_values(storage)
    assert result == {"amount": 99, "name": "Alice"}


def test_extract_values_skips_entries_without_value_key():
    """Malformed storage entries (no 'value' key) are silently skipped."""
    from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.variable_bridge import VariableBridge

    storage = {
        "good": {"value": 42, "type": "int"},
        "bad": {"type": "int"},  # no 'value'
    }
    result = VariableBridge.extract_values(storage)
    assert result == {"good": 42}
    assert "bad" not in result


def test_extract_values_empty_storage_returns_empty():
    from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.variable_bridge import VariableBridge

    assert VariableBridge.extract_values({}) == {}


def test_bridge_copies_values_into_target_manager():
    """bridge() writes each value into the target VariablesManager under its name."""
    from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.variable_bridge import VariableBridge

    target = VariablesManager()
    bridged = VariableBridge.bridge({"x": 10, "y": "hello"}, target)

    assert "x" in target.get_variable_names()
    assert "y" in target.get_variable_names()
    assert target.get_variable("x") == 10
    assert target.get_variable("y") == "hello"
    assert set(bridged) == {"x", "y"}


def test_bridge_empty_source_returns_empty_list_and_leaves_manager_unchanged():
    from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.variable_bridge import VariableBridge

    target = VariablesManager()
    target.add_variable(1, name="pre_existing")
    bridged = VariableBridge.bridge({}, target)

    assert bridged == []
    assert "pre_existing" in target.get_variable_names()


def test_bridge_description_prefix_is_applied():
    """bridge() stores variables with the given description prefix."""
    from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.variable_bridge import VariableBridge

    target = VariablesManager()
    VariableBridge.bridge({"result": 7}, target, description_prefix="from customer_agent")
    meta = target.variables.get("result")
    assert meta is not None
    assert "from customer_agent" in meta.description


# ── InvokeResult.variables field ──────────────────────────────────────────


def test_invoke_result_has_variables_field_defaulting_to_empty():
    """InvokeResult.variables exists and defaults to an empty dict."""
    from cuga.sdk import InvokeResult

    r = InvokeResult(answer="done")
    assert hasattr(r, "variables")
    assert r.variables == {}


def test_invoke_result_variables_accepts_name_value_dict():
    """InvokeResult.variables can be set to a {name: value} dict."""
    from cuga.sdk import InvokeResult

    r = InvokeResult(answer="ok", variables={"amount": 100, "name": "Alice"})
    assert r.variables["amount"] == 100
    assert r.variables["name"] == "Alice"


# ── Mechanism: shared VM ref bridges variables from sub-agent ─────────────


def test_shared_vm_ref_allows_delegate_to_write_into_supervisor_vm():
    """VariableBridge.bridge called with a ref populated at execution time writes to the target VM.

    This is the mechanism test for the _shared_vm_ref[0] pattern:
    - execute_agent_tool sets _shared_vm_ref[0] = state.supervisor_variables_manager
    - delegate_to_agent reads _shared_vm_ref[0] and calls VariableBridge.bridge(...)
    - The target VM accumulates the bridged variables
    """
    from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.variable_bridge import VariableBridge

    _shared_vm_ref: List[Any] = [None]

    supervisor_vm = VariablesManager()
    _shared_vm_ref[0] = supervisor_vm  # simulate execute_agent_tool setting the ref

    # simulate delegate_to_agent bridging after sub-agent returns variables
    sub_agent_vars = {"order_id": "ORD-42", "total": 199.99}
    if _shared_vm_ref[0] is not None:
        VariableBridge.bridge(sub_agent_vars, _shared_vm_ref[0], description_prefix="from order_agent")

    assert "order_id" in supervisor_vm.get_variable_names()
    assert supervisor_vm.get_variable("order_id") == "ORD-42"
    assert "total" in supervisor_vm.get_variable_names()


def test_shared_vm_ref_none_skips_bridge_gracefully():
    """When _shared_vm_ref[0] is None (before first execute), bridge is skipped."""
    from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.variable_bridge import VariableBridge

    _shared_vm_ref: List[Any] = [None]
    sub_agent_vars = {"result": "ok"}

    # Guard: only bridge when ref is set (not None)
    if _shared_vm_ref[0] is not None:
        VariableBridge.bridge(sub_agent_vars, _shared_vm_ref[0])

    # No exception; nothing to assert (VM was never written)
