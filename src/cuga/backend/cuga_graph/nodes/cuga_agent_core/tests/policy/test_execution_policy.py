"""Unit tests for the three-backend ExecutionPlan / ExecutionRouter.

These pin the *behavior-preserving* legacy-settings mapping required by
phase 2 of the sandbox refactor: the resolved ``python_backend`` must
match today's implicit decision in ``CodeExecutor.eval_with_tools_async``
(``e2b`` iff ``advanced_features.e2b_sandbox``, else ``local``), and the
shell/filesystem axes must reflect the existing flags without any graph
consuming them yet.
"""

from __future__ import annotations

from types import SimpleNamespace

from cuga.backend.cuga_graph.nodes.cuga_agent_core.policy.execution_policy import (
    ExecutionPlan,
    ExecutionRouter,
    split_execution_note,
)


def _settings(**advanced) -> SimpleNamespace:
    defaults = dict(
        e2b_sandbox=False,
        opensandbox_sandbox=True,
        enable_shell_tool=False,
        sandbox_mode="native",
        enable_filesystem_tools=False,
    )
    defaults.update(advanced)
    return SimpleNamespace(advanced_features=SimpleNamespace(**defaults))


def _settings_with_execution(
    *,
    python_backend=None,
    shell_backend=None,
    filesystem_backend=None,
    workspace_root=None,
    **advanced,
) -> SimpleNamespace:
    """Build settings that also carry an ``execution`` sub-section (Phase 6)."""
    base = _settings(**advanced)
    base.execution = SimpleNamespace(
        python_backend=python_backend,
        shell_backend=shell_backend,
        filesystem_backend=filesystem_backend,
        workspace_root=workspace_root,
    )
    return base


# ─── python_backend (the behavior-preserving core) ──────────────────────────


def test_default_settings_resolve_python_local() -> None:
    plan = ExecutionRouter.resolve(_settings())
    assert plan.python_backend == "local"


def test_e2b_sandbox_flag_resolves_python_e2b() -> None:
    plan = ExecutionRouter.resolve(_settings(e2b_sandbox=True))
    assert plan.python_backend == "e2b"


def test_explicit_mode_overrides_settings() -> None:
    plan = ExecutionRouter.resolve(_settings(e2b_sandbox=True), mode="local")
    assert plan.python_backend == "local"
    assert any("mode" in f.lower() for f in plan.fallbacks)


def test_opensandbox_flag_does_not_change_python_backend() -> None:
    """Today opensandbox only affects shell tools, never the Python path."""
    plan = ExecutionRouter.resolve(_settings(opensandbox_sandbox=True))
    assert plan.python_backend == "local"


# ─── shell_backend ──────────────────────────────────────────────────────────


def test_shell_backend_none_when_shell_disabled() -> None:
    plan = ExecutionRouter.resolve(_settings(enable_shell_tool=False))
    assert plan.shell_backend == "none"


def test_shell_backend_from_sandbox_mode_when_enabled() -> None:
    plan = ExecutionRouter.resolve(_settings(enable_shell_tool=True, sandbox_mode="opensandbox"))
    assert plan.shell_backend == "opensandbox"


def test_shell_backend_native_when_enabled_default_mode() -> None:
    plan = ExecutionRouter.resolve(_settings(enable_shell_tool=True, sandbox_mode="native"))
    assert plan.shell_backend == "native"


# ─── filesystem_backend ─────────────────────────────────────────────────────


def test_filesystem_backend_none_when_disabled() -> None:
    plan = ExecutionRouter.resolve(_settings(enable_filesystem_tools=False))
    assert plan.filesystem_backend == "none"


def test_filesystem_backend_host_by_default_when_enabled() -> None:
    plan = ExecutionRouter.resolve(_settings(enable_filesystem_tools=True))
    assert plan.filesystem_backend == "host"


def test_filesystem_backend_sandbox_remote_when_shell_is_opensandbox() -> None:
    plan = ExecutionRouter.resolve(
        _settings(
            enable_filesystem_tools=True,
            enable_shell_tool=True,
            sandbox_mode="opensandbox",
        )
    )
    assert plan.filesystem_backend == "sandbox_remote"


# ─── plan shape ─────────────────────────────────────────────────────────────


def test_plan_carries_local_control_tools_and_is_a_model() -> None:
    plan = ExecutionRouter.resolve(_settings())
    assert isinstance(plan, ExecutionPlan)
    assert "find_tools" in plan.local_control_tools
    assert "load_skill" in plan.local_control_tools
    assert plan.requested_backend  # non-empty
    assert isinstance(plan.fallbacks, list)


# ─── Phase 6: execution.* section overrides advanced_features ───────────────


def test_execution_python_backend_e2b_overrides_advanced_features() -> None:
    """execution.python_backend='e2b' → e2b even without advanced_features.e2b_sandbox."""
    plan = ExecutionRouter.resolve(_settings_with_execution(python_backend="e2b"))
    assert plan.python_backend == "e2b"


def test_execution_python_backend_local_wins_over_advanced_e2b() -> None:
    """execution.python_backend='local' overrides advanced_features.e2b_sandbox=True."""
    plan = ExecutionRouter.resolve(_settings_with_execution(python_backend="local", e2b_sandbox=True))
    assert plan.python_backend == "local"


def test_mode_still_wins_over_execution_python_backend() -> None:
    """Explicit mode= param takes precedence over both execution and advanced_features."""
    plan = ExecutionRouter.resolve(_settings_with_execution(python_backend="e2b"), mode="local")
    assert plan.python_backend == "local"


def test_execution_shell_backend_overrides_advanced_features() -> None:
    """execution.shell_backend='native' enables shell without enable_shell_tool flag."""
    plan = ExecutionRouter.resolve(_settings_with_execution(shell_backend="native"))
    assert plan.shell_backend == "native"


def test_execution_shell_backend_none_overrides_enabled_flag() -> None:
    """execution.shell_backend='none' disables shell even if enable_shell_tool=True."""
    plan = ExecutionRouter.resolve(_settings_with_execution(shell_backend="none", enable_shell_tool=True))
    assert plan.shell_backend == "none"


def test_execution_filesystem_backend_overrides_advanced_features() -> None:
    """execution.filesystem_backend='host' enables FS without enable_filesystem_tools."""
    plan = ExecutionRouter.resolve(_settings_with_execution(filesystem_backend="host"))
    assert plan.filesystem_backend == "host"


def test_execution_workspace_root_propagates_to_plan() -> None:
    """execution.workspace_root is surfaced on the plan."""
    plan = ExecutionRouter.resolve(_settings_with_execution(workspace_root="/custom"))
    assert plan.workspace_root == "/custom"


def test_advanced_features_fallback_unchanged_without_execution_section() -> None:
    """When no execution section, behavior is byte-identical to original."""
    plan_legacy = ExecutionRouter.resolve(
        _settings(enable_shell_tool=True, sandbox_mode="opensandbox", enable_filesystem_tools=True)
    )
    plan_no_exec = ExecutionRouter.resolve(
        _settings_with_execution(
            enable_shell_tool=True, sandbox_mode="opensandbox", enable_filesystem_tools=True
        )
    )
    assert plan_legacy.python_backend == plan_no_exec.python_backend
    assert plan_legacy.shell_backend == plan_no_exec.shell_backend
    assert plan_legacy.filesystem_backend == plan_no_exec.filesystem_backend


# ─── split_execution_note (Phase 10: prompt/log visibility) ─────────────────


def _plan(python_backend, shell_backend, filesystem_backend) -> ExecutionPlan:
    return ExecutionPlan(
        requested_backend=python_backend,
        python_backend=python_backend,
        shell_backend=shell_backend,
        filesystem_backend=filesystem_backend,
    )


def test_split_execution_note_empty_when_fully_local() -> None:
    plan = _plan("local", "none", "none")
    assert split_execution_note(plan) == ""


def test_split_execution_note_empty_when_python_is_e2b() -> None:
    """When Python itself runs remotely, there is no split — note is silent."""
    plan = _plan("e2b", "opensandbox", "sandbox_remote")
    assert split_execution_note(plan) == ""


def test_split_execution_note_nonempty_when_local_python_remote_shell() -> None:
    plan = _plan("local", "opensandbox", "none")
    note = split_execution_note(plan)
    assert note != ""
    assert "run_command" in note.lower() or "shell" in note.lower()


def test_split_execution_note_nonempty_when_local_python_remote_fs() -> None:
    plan = _plan("local", "none", "sandbox_remote")
    note = split_execution_note(plan)
    assert note != ""


def test_split_execution_note_nonempty_when_both_remote() -> None:
    plan = _plan("local", "native", "sandbox_remote")
    note = split_execution_note(plan)
    assert note != ""
    assert "local" in note.lower() or "sandbox" in note.lower()


# ─── Phase 11: legacy deprecation warnings ──────────────────────────────────
# When execution.* settings override advanced_features.* flags that disagree,
# ExecutionRouter should record a deprecation notice in plan.fallbacks so
# operators can migrate away from the old flags.


def test_legacy_e2b_sandbox_deprecated_when_execution_python_backend_differs() -> None:
    """execution.python_backend='local' + advanced_features.e2b_sandbox=True → deprecation.

    The new setting wins (behavior-preserving), but the operator should be
    told that e2b_sandbox is now superseded and should be removed.
    """
    plan = ExecutionRouter.resolve(_settings_with_execution(python_backend="local", e2b_sandbox=True))
    assert plan.python_backend == "local"
    assert any("e2b_sandbox" in f.lower() or "deprecated" in f.lower() for f in plan.fallbacks)


def test_no_deprecation_when_execution_python_backend_agrees_with_e2b_flag() -> None:
    """No deprecation when execution.python_backend='e2b' and e2b_sandbox=True (they agree)."""
    plan = ExecutionRouter.resolve(_settings_with_execution(python_backend="e2b", e2b_sandbox=True))
    assert plan.python_backend == "e2b"
    # No e2b_sandbox deprecation when the flags agree
    assert not any("e2b_sandbox" in f.lower() for f in plan.fallbacks)


def test_legacy_shell_flag_deprecated_when_execution_shell_backend_disagrees() -> None:
    """execution.shell_backend='none' + advanced_features.enable_shell_tool=True → deprecation."""
    plan = ExecutionRouter.resolve(_settings_with_execution(shell_backend="none", enable_shell_tool=True))
    assert plan.shell_backend == "none"
    assert any("enable_shell_tool" in f.lower() or "deprecated" in f.lower() for f in plan.fallbacks)


def test_no_deprecation_when_no_execution_section_and_legacy_flags_used() -> None:
    """Pure advanced_features path (no execution.*) never emits deprecation warnings."""
    plan = ExecutionRouter.resolve(
        _settings(e2b_sandbox=True, enable_shell_tool=True, sandbox_mode="opensandbox")
    )
    assert plan.python_backend == "e2b"
    # No deprecation without an execution.* override
    assert not any("deprecated" in f.lower() for f in plan.fallbacks)
