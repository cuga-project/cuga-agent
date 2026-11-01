"""One place that decides which sandbox holds the workspace.

Every consumer — the agent's filesystem tools, the workspace tree endpoint, the
upload path — needs the same answer, and answering it separately at each call
site is how the two halves drift apart: the mode that puts generated Python in
a sandbox has to put its files there too, or the agent writes a file it cannot
read back.

Deliberately not a registry. There are exactly two remote backends, they are
selected by one setting, and a lookup table would hide that behind indirection
without removing a single decision.
"""

from __future__ import annotations

from typing import Any, Optional

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.code_executor import CodeExecutor
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.execd.filesystem_backend import ExecdFilesystemBackend
from cuga.config import settings

from ..execd.connection import effective_sandbox_mode
from .backends import FilesystemBackend, HostWorkspaceBackend, RemoteSandboxBackend


def sandbox_mode() -> str:
    return effective_sandbox_mode(
        str(getattr(settings.advanced_features, "sandbox_mode", "opensandbox") or "opensandbox")
    )


def workspace_is_execd_backed() -> bool:
    """True when the workspace lives in the execd sandbox.

    Not gated on ``opensandbox_sandbox``: that flag describes a different
    daemon and says nothing about execd.
    """
    return sandbox_mode() == "execd"


def workspace_is_sandbox_backed() -> bool:
    """True when the workspace lives in a sandbox rather than on the host."""
    if workspace_is_execd_backed():
        return True
    if not bool(getattr(settings.advanced_features, "opensandbox_sandbox", False)):
        return False
    return sandbox_mode() not in ("native", "local")


def sandbox_workspace_backend(thread_id: Optional[str]) -> Any:
    """Return the backend for the sandbox holding this thread's workspace.

    Callers must have established that there *is* one — see
    ``workspace_is_sandbox_backed``. Both returned types implement
    ``FilesystemBackend`` plus the ``walk`` / ``read_bytes`` / ``remove_tree``
    trio the workspace API needs.
    """
    if workspace_is_execd_backed():
        return ExecdFilesystemBackend(CodeExecutor._get_execd_executor(), thread_id)
    return RemoteSandboxBackend(CodeExecutor._get_opensandbox_executor(), thread_id)


def workspace_backend(thread_id: Optional[str]) -> FilesystemBackend:
    """The backend for this thread's workspace, sandbox or host."""
    if workspace_is_sandbox_backed():
        return sandbox_workspace_backend(thread_id)
    return HostWorkspaceBackend(thread_id)


__all__ = [
    "sandbox_mode",
    "sandbox_workspace_backend",
    "workspace_backend",
    "workspace_is_execd_backed",
    "workspace_is_sandbox_backed",
]
