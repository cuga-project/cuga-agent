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
from pathlib import Path, PurePosixPath
from typing import Optional

VIRTUAL_WORKSPACE_ROOT = "/workspace"
CUGA_WORKSPACE_DIRNAME = "cuga_workspace"
# CRM demo + manager CI fixtures at the shared workspace root, copied into empty per-thread workspaces.
_SHARED_SEED_FILES = frozenset(
    {
        "contacts.txt",
        "cuga_knowledge.md",
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


def _normpath_under(base_path: str, *segments: str) -> str:
    """Join segments under ``base_path``; return validated path string (CodeQL-safe)."""
    fullpath = base_path
    for name in segments:
        clean = (name or "").replace("\\", "/")
        if not clean or clean in (".", "..") or "/" in clean:
            raise ValueError(f"Invalid path segment: {name!r}")
        fullpath = os.path.normpath(os.path.join(fullpath, clean))
    if not (fullpath == base_path or fullpath.startswith(base_path + os.sep)):
        raise ValueError(f"Path must stay under {base_path}")
    return fullpath


def child_path_under(base: Path, *names: str) -> Path:
    """Join single-name segments under base; reject traversal and escapes."""
    base_path = os.path.normpath(os.path.realpath(str(base)))
    if not names:
        return Path(base_path)
    return Path(_normpath_under(base_path, *names))


def assert_resolved_path_under(path: Path, base: Path) -> Path:
    """Return ``path`` after realpath + prefix verification under ``base``."""
    base_path = os.path.normpath(os.path.realpath(str(base)))
    fullpath = os.path.normpath(os.path.realpath(str(path)))
    if not (fullpath == base_path or fullpath.startswith(base_path + os.sep)):
        raise ValueError(f"Path must stay under {base_path}")
    return Path(fullpath)


def write_bytes_under(base: Path, data: bytes, *segments: str) -> Path:
    """Write ``data`` under ``base``/segments using normpath+startswith then ``open``."""
    base_path = os.path.normpath(os.path.realpath(str(base)))
    fullpath = _normpath_under(base_path, *segments) if segments else base_path
    parent = os.path.dirname(fullpath)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(fullpath, "wb") as handle:
        handle.write(data)
    return Path(fullpath)


def read_bytes_under(path: Path, base: Path) -> bytes:
    """Read a file after verifying its resolved path stays under ``base``."""
    base_path = os.path.normpath(os.path.realpath(str(base)))
    fullpath = os.path.normpath(os.path.realpath(str(path)))
    if not (fullpath == base_path or fullpath.startswith(base_path + os.sep)):
        raise ValueError(f"Path must stay under {base_path}")
    if not os.path.isfile(fullpath):
        raise FileNotFoundError(f"File not found: {fullpath}")
    with open(fullpath, "rb") as handle:
        return handle.read()


def remove_file_under(path: Path, base: Path) -> None:
    """Remove a file after verifying its resolved path stays under ``base``."""
    base_path = os.path.normpath(os.path.realpath(str(base)))
    fullpath = os.path.normpath(os.path.realpath(str(path)))
    if not (fullpath == base_path or fullpath.startswith(base_path + os.sep)):
        raise ValueError(f"Path must stay under {base_path}")
    os.remove(fullpath)


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
        return child_path_under(base, safe_thread_id(thread_id))
    return base


def _posix_path(raw: str) -> str:
    """Normalize agent paths with POSIX rules so ``/workspace`` is never OS-absolutized."""
    return str(PurePosixPath(raw.replace("\\", "/")))


def _host_path_from_posix_tail(workspace_root: Path, tail: str) -> Path:
    """Join a POSIX-relative tail onto ``workspace_root`` using local path segments."""
    if not tail or tail == ".":
        return workspace_root
    _reject_traversal(tail)
    dest = workspace_root
    for part in PurePosixPath(tail).parts:
        dest = child_path_under(dest, part)
    return dest


def ensure_thread_workspace_seeded(thread_id: Optional[str]) -> None:
    """Copy shared CRM/CI fixtures into an empty per-thread workspace when opted in via env."""
    modes = _seed_modes()
    if not modes:
        return
    if not (thread_id or "").strip():
        return
    safe_tid = safe_thread_id(thread_id)
    cache_key = (str(local_base_dir().resolve()), safe_tid)
    if cache_key in _seeded_threads:
        return

    shared = local_base_dir()
    dest = child_path_under(shared, safe_tid)
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
            shutil.copy2(item, child_path_under(dest, item.name))
        elif item.is_dir() and "ci" in modes and item.name in _SHARED_SEED_DIRS:
            shutil.copytree(item, child_path_under(dest, item.name), dirs_exist_ok=True)

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

    safe_tid: Optional[str] = None
    if (thread_id or "").strip():
        safe_tid = safe_thread_id(thread_id)
        ensure_thread_workspace_seeded(safe_tid)

    posix = _posix_path(raw)
    workspace_root = thread_workspace_root(safe_tid).resolve()

    if posix == VIRTUAL_WORKSPACE_ROOT:
        dest = workspace_root
    elif posix.startswith(VIRTUAL_WORKSPACE_ROOT + "/"):
        dest = _host_path_from_posix_tail(workspace_root, posix[len(VIRTUAL_WORKSPACE_ROOT) + 1 :])
    else:
        matched_legacy = False
        for legacy in _LEGACY_ROOTS:
            if posix == legacy:
                dest = workspace_root
                matched_legacy = True
                break
            if posix.startswith(legacy + "/"):
                dest = _host_path_from_posix_tail(workspace_root, posix[len(legacy) + 1 :])
                matched_legacy = True
                break
        if not matched_legacy:
            dest = _host_path_from_posix_tail(workspace_root, posix.lstrip("/"))

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


def relative_workspace_path(tail: str) -> str:
    """Workspace-relative path for agent-facing use in tools, shell, and scripts."""
    raw = (tail or "").strip().replace("\\", "/").lstrip("./")
    return "." if not raw else f"./{raw}"


def shell_workspace_path(virtual_path: str) -> str:
    """Path for ``run_command`` when cwd is the per-thread workspace root.

    Maps agent-facing ``/workspace/...`` (and legacy ``/tmp/cuga_workspace/<thread>/...``)
    to ``./...`` so shell tools work in local/native sandboxes where ``/workspace`` is not
    a real directory. OpenSandbox runs ``cd /workspace`` first; ``./`` paths still work.
    """
    raw = (virtual_path or "").strip().replace("\\", "/")
    if raw == VIRTUAL_WORKSPACE_ROOT:
        return "."
    prefix = VIRTUAL_WORKSPACE_ROOT + "/"
    if raw.startswith(prefix):
        tail = raw[len(prefix) :]
        return "." if not tail else f"./{tail}"
    legacy = re.match(r"^/tmp/cuga_workspace/[^/]+/(.*)$", raw)
    if legacy:
        tail = legacy.group(1)
        return "." if not tail else f"./{tail}"
    return raw


def normalize_shell_command_paths(cmd: str) -> str:
    """Rewrite virtual workspace path prefixes in a shell command string.

    ``read_file`` / ``list_files`` accept ``/workspace/...``; ``run_command`` cwd is the
    physical workspace (local/native) or ``/workspace`` (OpenSandbox). Normalizing to ``./``
    keeps manifest and skill paths working in all sandbox modes.
    """
    if not cmd:
        return cmd
    out = cmd.replace("\\", "/")
    out = re.sub(r"/tmp/cuga_workspace/[^/]+/", "./", out)
    return out.replace(f"{VIRTUAL_WORKSPACE_ROOT}/", "./")
