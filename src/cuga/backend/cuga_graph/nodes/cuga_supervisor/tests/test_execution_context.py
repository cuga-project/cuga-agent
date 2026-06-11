"""Tests for supervisor per-execution context resolution."""

from __future__ import annotations

from types import SimpleNamespace

from cuga.backend.cuga_graph.nodes.cuga_supervisor.execution_context import (
    SupervisorExecutionContext,
    resolve_supervisor_execution_context,
)


def test_resolve_supervisor_execution_context_from_locals():
    state = SimpleNamespace(thread_id="t1")
    exec_ctx = SupervisorExecutionContext(state=state, variable_manager="vm")

    def inner():
        __supervisor_exec__ = exec_ctx  # noqa: F841
        return resolve_supervisor_execution_context()

    resolved = inner()
    assert resolved is exec_ctx
    assert resolved.state is state
    assert resolved.variable_manager == "vm"
