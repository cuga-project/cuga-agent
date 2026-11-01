"""Three-backend execution routing.

``ExecutionRouter.resolve`` turns the current (legacy) ``advanced_features``
settings into an explicit :class:`ExecutionPlan` with three independent
axes: ``python_backend``, ``shell_backend`` and ``filesystem_backend``.

``python_backend``: an explicit ``execution.python_backend`` wins. ``"auto"`` (the
shipped default) picks ``"e2b"`` when ``advanced_features.e2b_sandbox`` is set, else
``"subprocess"`` when the host has an OS sandbox (Seatbelt / bubblewrap, and
``execution.subprocess_sandbox`` is not off), else ``"local"``. Unset (``None``)
keeps the legacy rule: ``"e2b"`` iff ``advanced_features.e2b_sandbox``, else
``"local"``. The shell/filesystem axes describe the existing flags.
"""

from __future__ import annotations

import shutil
import sys
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field

#: ``subprocess`` runs each block in a fresh sandboxed interpreter (``executors/isolated``).
PythonBackend = Literal["local", "e2b", "subprocess"]
ShellBackend = Literal["none", "local", "native", "opensandbox", "e2b"]
FilesystemBackend = Literal["none", "host", "sandbox_remote"]


def os_sandbox_available(settings: Any) -> bool:
    """True when a ``subprocess`` block would run under an OS sandbox on this host.

    Mirrors ``isolated_executor._sandbox_kind`` without importing the executor:
    Seatbelt (``sandbox-exec``) on macOS, bubblewrap (``bwrap``) on Linux, unless
    ``execution.subprocess_sandbox`` is off.
    """
    wanted = str(getattr(getattr(settings, "execution", None), "subprocess_sandbox", None) or "auto").lower()
    if wanted in ("off", "none", "false"):
        return False
    if sys.platform == "darwin":
        return shutil.which("sandbox-exec") is not None
    if sys.platform.startswith("linux"):
        return shutil.which("bwrap") is not None
    return False


class ExecutionPlan(BaseModel):
    """Explicit, prompt-/log-visible description of where execution happens."""

    requested_backend: str
    python_backend: PythonBackend
    shell_backend: ShellBackend
    filesystem_backend: FilesystemBackend
    local_control_tools: List[str] = Field(default_factory=lambda: ["find_tools", "load_skill"])
    fallbacks: List[str] = Field(default_factory=list)
    workspace_root: str = "/workspace"
    variable_transfer_policy: str = "namespace"

    @property
    def split_execution_active(self) -> bool:
        """True when generated Python runs on the host while shell/FS run elsewhere.

        A ``subprocess`` block starts in the native/local shell's workspace
        (``isolated_executor._shared_workspace``), so only a remote shell splits it.
        """
        remote_fs = self.filesystem_backend == "sandbox_remote"
        if self.python_backend == "subprocess":
            return self.shell_backend in ("opensandbox", "e2b") or remote_fs
        remote_shell = self.shell_backend in ("native", "opensandbox", "e2b")
        return self.python_backend == "local" and (remote_shell or remote_fs)


def split_execution_note(plan: "ExecutionPlan") -> str:
    """Return a prompt-visible note when Python and shell/FS run in different environments.

    Returns an empty string when split execution is not active so callers can
    safely append it without extra conditionals.
    """
    if not plan.split_execution_active:
        return ""
    return (
        "**Split-execution mode is active**: your Python code runs in the local environment, "
        "but shell commands (`run_command`) and filesystem operations (`read_file`, `write_file`, "
        "`list_files`, etc.) execute inside the remote sandbox. "
        "Use `await run_command(...)` for anything that must happen inside the sandbox; "
        "avoid `os.path`, `open()`, or other local-filesystem calls when targeting sandbox paths."
    )


class ExecutionRouter:
    """Resolves settings (+ optional explicit overrides) into an ExecutionPlan."""

    @staticmethod
    def resolve(
        settings: Any,
        *,
        mode: Optional[PythonBackend] = None,
        workspace_root: Optional[str] = None,
    ) -> ExecutionPlan:
        adv = settings.advanced_features
        exec_cfg = getattr(settings, "execution", None)
        fallbacks: List[str] = []

        # Phase 6: explicit execution.* settings take priority over advanced_features.
        # None means "not set" — fall through to the legacy flag logic below.
        explicit_python: Optional[str] = getattr(exec_cfg, "python_backend", None)
        explicit_shell: Optional[ShellBackend] = getattr(exec_cfg, "shell_backend", None)
        explicit_fs: Optional[FilesystemBackend] = getattr(exec_cfg, "filesystem_backend", None)
        explicit_root: Optional[str] = getattr(exec_cfg, "workspace_root", None)

        # ── python_backend ──────────────────────────────────────────────────
        e2b = bool(getattr(adv, "e2b_sandbox", False))
        settings_choice: PythonBackend
        if explicit_python == "auto":
            if e2b:
                settings_choice = "e2b"
            elif os_sandbox_available(settings):
                settings_choice = "subprocess"
            else:
                settings_choice = "local"
            explicit_python = None  # "auto" honors advanced_features.e2b_sandbox — nothing to warn about
        elif explicit_python is not None:
            settings_choice = explicit_python  # type: ignore[assignment]
        else:
            settings_choice = "e2b" if e2b else "local"
        if mode is not None:
            python_backend: PythonBackend = mode
            if mode != settings_choice:
                fallbacks.append(
                    f"python_backend forced to '{mode}' by explicit mode "
                    f"(settings would select '{settings_choice}')"
                )
        else:
            python_backend = settings_choice

        # ── legacy deprecation: warn when execution.* overrides disagree with advanced_features ──
        if explicit_python is not None and e2b and explicit_python != "e2b":
            fallbacks.append(
                "advanced_features.e2b_sandbox=True is deprecated when execution.python_backend "
                f"is set; e2b_sandbox is ignored (execution.python_backend='{explicit_python}' wins). "
                "Remove advanced_features.e2b_sandbox from your settings."
            )

        # ── shell_backend ───────────────────────────────────────────────────
        _legacy_shell_on = bool(getattr(adv, "enable_shell_tool", False))
        if explicit_shell is not None:
            shell_backend: ShellBackend = explicit_shell
            if _legacy_shell_on:
                fallbacks.append(
                    "advanced_features.enable_shell_tool=True is deprecated when execution.shell_backend "
                    f"is set; enable_shell_tool is ignored (execution.shell_backend='{explicit_shell}' wins). "
                    "Remove advanced_features.enable_shell_tool from your settings."
                )
        elif _legacy_shell_on:
            sandbox_mode = str(getattr(adv, "sandbox_mode", "native") or "native")
            shell_backend = (
                sandbox_mode if sandbox_mode in ("local", "native", "opensandbox", "e2b") else "native"
            )
        else:
            shell_backend = "none"

        # ── filesystem_backend ──────────────────────────────────────────────
        if explicit_fs is not None:
            filesystem_backend: FilesystemBackend = explicit_fs
        elif bool(getattr(adv, "enable_filesystem_tools", False)):
            filesystem_backend = "sandbox_remote" if shell_backend == "opensandbox" else "host"
        else:
            filesystem_backend = "none"

        requested_backend = mode if mode is not None else python_backend

        return ExecutionPlan(
            requested_backend=requested_backend,
            python_backend=python_backend,
            shell_backend=shell_backend,
            filesystem_backend=filesystem_backend,
            fallbacks=fallbacks,
            workspace_root=workspace_root or explicit_root or "/workspace",
        )
