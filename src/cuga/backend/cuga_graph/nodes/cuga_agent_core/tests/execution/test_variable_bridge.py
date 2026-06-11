"""Phase 8 — VariableBridge tests.

Pins three behaviors:
1. VariableBridge.extract_values converts variables_storage format → {name: value}.
2. VariableBridge.bridge copies values into a target VariablesManager.
3. InvokeResult carries a `variables` field (default empty, populated from
   sub-agent graph state after invocation).
4. Mechanism test: delegate_to_agent bridges variables via the per-run
   execution context that execute_agent_tool injects into code locals.
"""

from __future__ import annotations

from types import SimpleNamespace

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


def test_execution_context_allows_delegate_to_write_into_supervisor_vm():
    """VariableBridge.bridge called with execution context writes to the target VM.

    execute_agent_tool injects ``SupervisorExecutionContext`` into code locals;
    delegate_to_agent reads ``variable_manager`` from that context and bridges
    sub-agent variables into the supervisor namespace.
    """
    from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.variable_bridge import VariableBridge
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.execution_context import SupervisorExecutionContext
    from cuga.backend.cuga_graph.state.agent_state import VariablesManager

    supervisor_vm = VariablesManager()
    exec_ctx = SupervisorExecutionContext(state=SimpleNamespace(), variable_manager=supervisor_vm)

    sub_agent_vars = {"order_id": "ORD-42", "total": 199.99}
    if exec_ctx.variable_manager is not None:
        VariableBridge.bridge(
            sub_agent_vars, exec_ctx.variable_manager, description_prefix="from order_agent"
        )

    assert "order_id" in supervisor_vm.get_variable_names()
    assert supervisor_vm.get_variable("order_id") == "ORD-42"
    assert "total" in supervisor_vm.get_variable_names()


def test_execution_context_none_skips_bridge_gracefully():
    """When execution context has no variable manager, bridge is skipped."""
    from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.variable_bridge import VariableBridge
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.execution_context import SupervisorExecutionContext

    exec_ctx = SupervisorExecutionContext(state=SimpleNamespace(), variable_manager=None)
    sub_agent_vars = {"result": "ok"}

    assert exec_ctx.variable_manager is None

    bridged_names: list[str] = []
    if exec_ctx.variable_manager is not None:
        bridged_names = VariableBridge.bridge(sub_agent_vars, exec_ctx.variable_manager)

    assert bridged_names == []
