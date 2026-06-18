"""Tests for supervisor per-execution context resolution."""

from __future__ import annotations

from types import SimpleNamespace

from cuga.backend.cuga_graph.nodes.cuga_supervisor.execution_context import (
    SUPERVISOR_EXEC_KEY,
    SupervisorExecutionContext,
    resolve_supervisor_execution_context,
)


def test_resolve_supervisor_execution_context_from_locals():
    # The local name below must stay in sync with the runtime key the resolver scans for.
    assert SUPERVISOR_EXEC_KEY == "__supervisor_exec__"
    state = SimpleNamespace(thread_id="t1")
    exec_ctx = SupervisorExecutionContext(state=state, variable_manager="vm")

    def inner():
        __supervisor_exec__ = exec_ctx  # noqa: F841
        return resolve_supervisor_execution_context()

    resolved = inner()
    assert resolved is exec_ctx
    assert resolved.state is state
    assert resolved.variable_manager == "vm"
