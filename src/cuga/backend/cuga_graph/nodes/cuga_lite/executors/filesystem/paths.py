"""Canonical workspace path resolution — single source of truth.

Moved here from ``local_sandbox_executor.py`` so the chat agent, cuga-lite,
and every sandbox executor resolve workspace paths identically.

Two layouts, selected by ``thread_id``:

* ``thread_id`` present → per-thread ``<cwd>/cuga_workspace/<safe_thread_id>/``
* no ``thread_id``    → shared   ``<cwd>/cuga_workspace/`` (legacy/SDK)

``resolve_workspace_path`` maps the agent-facing ``/workspace`` virtual root
(and the legacy ``/tmp`` / ``/private/tmp`` aliases) onto that physical root
and rejects any path that escapes it (``..`` traversal or absolute escapes),
which is what keeps per-thread isolation intact.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Optional

VIRTUAL_WORKSPACE_ROOT = "/workspace"
CUGA_WORKSPACE_DIRNAME = "cuga_workspace"
# CRM demo + manager CI fixtures at the shared workspace root, copied into empty per-thread workspaces.
_SHARED_SEED_FILES = frozenset(
    {
        "contacts.txt",
        "cuga_playbook.md",
        "email_template.md",
        "cities.txt",
        "company.txt",
    }
)
_SHARED_SEED_DIRS = frozenset({"test_workspace"})
# Legacy roots older prompts/tools may still emit; mapped onto the workspace.
_LEGACY_ROOTS = ("/tmp", "/private/tmp")
_SEED_ENV = "CUGA_THREAD_WORKSPACE_SEED"
_seeded_threads: set[tuple[str, str]] = set()


def _seed_modes() -> frozenset[str]:
    raw = os.environ.get(_SEED_ENV, "").strip().lower()
    if not raw:
        return frozenset()
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def safe_thread_id(thread_id: Optional[str]) -> str:
    raw = (thread_id or "_default").strip() or "_default"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", raw)


def skills_enabled() -> bool:
    try:
        from cuga.config import settings

        return bool(getattr(getattr(settings, "skills", None), "enabled", False))
    except Exception:
        return False


def local_base_dir() -> Path:
    """Workspace parent: ``<cwd>/cuga_workspace``."""
    return Path(os.getcwd()) / CUGA_WORKSPACE_DIRNAME


def thread_workspace_root(thread_id: Optional[str]) -> Path:
    """Per-thread workspace when ``thread_id`` is set, else the shared workspace.

    thread_id set   → ``<cwd>/cuga_workspace/<safe_thread_id>/``
    thread_id empty → ``<cwd>/cuga_workspace/``
    """
    base = local_base_dir()
    if (thread_id or "").strip():
        return base / safe_thread_id(thread_id)
    return base


def ensure_thread_workspace_seeded(thread_id: Optional[str]) -> None:
    """Copy shared CRM/CI fixtures into an empty per-thread workspace when opted in via env."""
    modes = _seed_modes()
    if not modes:
        return
    tid = (thread_id or "").strip()
    if not tid:
        return
    safe_tid = safe_thread_id(tid)
    cache_key = (str(local_base_dir().resolve()), safe_tid)
    if cache_key in _seeded_threads:
        return

    shared = local_base_dir()
    dest = shared / safe_tid
    if dest.exists() and any(dest.iterdir()):
        _seeded_threads.add(cache_key)
        return

    dest.mkdir(parents=True, exist_ok=True)
    if not shared.is_dir():
        _seeded_threads.add(cache_key)
        return

    for item in shared.iterdir():
        if item.name == safe_tid:
            continue
        if item.is_file() and "crm" in modes and item.name in _SHARED_SEED_FILES:
            shutil.copy2(item, dest / item.name)
        elif item.is_dir() and "ci" in modes and item.name in _SHARED_SEED_DIRS:
            shutil.copytree(item, dest / item.name, dirs_exist_ok=True)

    _seeded_threads.add(cache_key)


def _reject_traversal(raw: str) -> None:
    """Refuse parent-directory traversal outright.

    ``resolve_workspace_path``'s ``relative_to`` check already blocks escapes,
    but an explicit ``..`` refusal preserves the loud, early failure the
    per-thread MCP wrapper used to provide and keeps the error message clear.
    """
    for segment in raw.replace("\\", "/").split("/"):
        if segment == "..":
            raise ValueError(f"Path traversal ('..') is not allowed in workspace paths: {raw!r}")


def resolve_workspace_path(
    sandbox_path: str,
    *,
    thread_id: Optional[str],
    operation: str = "access",
) -> Path:
    """Map an agent-facing path onto the physical per-thread/shared workspace.

    Accepts relative paths, the ``/workspace`` virtual root, and the legacy
    ``/tmp`` / ``/private/tmp`` aliases. Raises ``ValueError`` if the result
    would escape the workspace root.
    """
    raw = (sandbox_path or "").strip()
    if not raw:
        raise ValueError("empty path")
    _reject_traversal(raw)
    if (thread_id or "").strip():
        ensure_thread_workspace_seeded(thread_id)
    normalized = os.path.normpath(raw.replace("\\", "/"))
    workspace_root = thread_workspace_root(thread_id).resolve()

    if normalized == VIRTUAL_WORKSPACE_ROOT:
        dest = workspace_root
    elif normalized.startswith(VIRTUAL_WORKSPACE_ROOT + "/"):
        dest = workspace_root / normalized[len(VIRTUAL_WORKSPACE_ROOT) :].lstrip("/")
    else:
        matched_legacy = False
        for legacy in _LEGACY_ROOTS:
            if normalized == legacy:
                dest = workspace_root
                matched_legacy = True
                break
            if normalized.startswith(legacy + "/"):
                dest = workspace_root / normalized[len(legacy) :].lstrip("/")
                matched_legacy = True
                break
        if not matched_legacy:
            dest = workspace_root / normalized.lstrip("/")

    resolved = dest.resolve()
    try:
        resolved.relative_to(workspace_root)
    except ValueError as e:
        raise ValueError(f"{operation} path must stay under /workspace") from e
    return resolved


def public_workspace_path(host_path: Path, *, thread_id: Optional[str]) -> str:
    """Relative display path for a host path inside the workspace (e.g. ``./x``)."""
    workspace_root = thread_workspace_root(thread_id).resolve()
    try:
        rel = host_path.resolve().relative_to(workspace_root)
    except ValueError:
        return str(host_path)
    rel_str = str(rel)
    return "." if rel_str == "." else f"./{rel_str}"
