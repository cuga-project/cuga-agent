"""Sandbox-backed workspace tree/file access for /api/workspace."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from cuga.config import settings

SANDBOX_WORKSPACE_ROOT = "/workspace"
DISPLAY_ROOT = "workspace"
NATIVE_WORKSPACE_ROOT = "/workspace"
NATIVE_DISPLAY_ROOT = "workspace"
LEGACY_DISPLAY_ROOT = "cuga_workspace"
LEGACY_SANDBOX_WORKSPACE_ROOT = "/tmp/cuga_workspace"
_LEGACY_DISPLAY_ROOTS = {"tmp", "cuga_workspace"}  # kept for backward-compat path resolution


def workspace_tree_is_sandbox_backed() -> bool:
    return bool(
        getattr(settings.skills, "enabled", False)
        and getattr(settings.advanced_features, "opensandbox_sandbox", False)
    )


def workspace_tree_is_native_backed() -> bool:
    """True for both 'native' (macOS sandbox-exec) and 'local' (plain host subprocess) modes."""
    mode = getattr(settings.advanced_features, "sandbox_mode", "opensandbox")
    return bool(
        getattr(settings.skills, "enabled", False)
        and getattr(settings.advanced_features, "enable_shell_tool", False)
        and mode in ("native", "local")
    )


def _hidden_parts(parts: tuple[str, ...]) -> bool:
    return any(p.startswith(".") for p in parts)


def _rel_parts(sandbox_root: str, abs_path: str) -> tuple[str, ...]:
    root = sandbox_root.rstrip("/")
    ap = abs_path.strip().rstrip("/")
    if ap == root:
        return tuple()
    prefix = root + "/"
    if not ap.startswith(prefix):
        raise ValueError(abs_path)
    rel = ap[len(prefix) :]
    return tuple(rel.split("/")) if rel else tuple()


def _collect_dir_and_file_sets(
    dir_lines: list[str], file_lines: list[str], sandbox_root: str
) -> tuple[set[tuple[str, ...]], set[tuple[str, ...]]]:
    dir_rels: set[tuple[str, ...]] = set()
    file_rels: set[tuple[str, ...]] = set()
    for raw in dir_lines:
        try:
            parts = _rel_parts(sandbox_root, raw)
        except ValueError:
            continue
        if _hidden_parts(parts):
            continue
        dir_rels.add(parts)
        for i in range(1, len(parts)):
            dir_rels.add(parts[:i])
    for raw in file_lines:
        try:
            parts = _rel_parts(sandbox_root, raw)
        except ValueError:
            continue
        if _hidden_parts(parts):
            continue
        file_rels.add(parts)
        for i in range(1, len(parts)):
            dir_rels.add(parts[:i])
    return dir_rels, file_rels


def _children_nodes(
    parent: tuple[str, ...],
    dir_rels: set[tuple[str, ...]],
    file_rels: set[tuple[str, ...]],
    *,
    display_root: str = DISPLAY_ROOT,
) -> list[dict[str, Any]]:
    pl = len(parent)
    names: dict[str, str] = {}
    for p in file_rels:
        if len(p) == pl + 1 and p[:pl] == parent:
            names[p[pl]] = "file"
    for p in dir_rels:
        if len(p) == pl + 1 and p[:pl] == parent:
            nm = p[pl]
            names[nm] = "dir"
    items: list[dict[str, Any]] = []
    for name in sorted(names.keys(), key=lambda n: (names[n] == "file", n.lower())):
        path_parts = parent + (name,)
        pub_path = f"{display_root}/{'/'.join(path_parts)}" if path_parts else display_root
        if names[name] == "file":
            items.append({"name": name, "path": pub_path, "type": "file"})
        else:
            ch = _children_nodes(path_parts, dir_rels, file_rels, display_root=display_root)
            items.append({"name": name, "path": pub_path, "type": "directory", "children": ch})
    return items


def sandbox_paths_to_tree(
    dir_lines: list[str],
    file_lines: list[str],
    *,
    sandbox_root: str = SANDBOX_WORKSPACE_ROOT,
    display_root: str = DISPLAY_ROOT,
) -> list[dict[str, Any]]:
    dir_rels, file_rels = _collect_dir_and_file_sets(dir_lines, file_lines, sandbox_root)
    return _children_nodes(tuple(), dir_rels, file_rels, display_root=display_root)


async def _find_paths(commands: Any, type_flag: str) -> list[str]:
    q = shlex.quote(SANDBOX_WORKSPACE_ROOT)
    cmd = f"find {q} -type {type_flag} 2>/dev/null | sort"
    ex = await commands.run(cmd)
    text = ex.text if hasattr(ex, "text") else ""
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


async def fetch_sandbox_workspace_tree(thread_id: Optional[str]) -> list[dict[str, Any]]:
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.code_executor import CodeExecutor

    executor = CodeExecutor._get_opensandbox_executor()
    interpreter = await executor.get_interpreter_for_thread(thread_id)
    sandbox = interpreter.sandbox
    commands = sandbox.commands
    dir_lines = await _find_paths(commands, "d")
    file_lines = await _find_paths(commands, "f")
    tree = sandbox_paths_to_tree(dir_lines, file_lines)
    if not tree and (dir_lines or file_lines):
        logger.warning(
            "sandbox workspace tree: find returned paths but tree is empty — sample file_lines={} dir_lines={}",
            file_lines[:8],
            dir_lines[:8],
        )
    return tree


def _host_workspace_root(thread_id: Optional[str]) -> Path:
    """Return the correct host-filesystem workspace root for the active sandbox mode."""
    mode = getattr(settings.advanced_features, "sandbox_mode", "opensandbox")
    if mode == "local":
        from cuga.backend.cuga_graph.nodes.cuga_lite.executors.local.local_sandbox_executor import (
            local_thread_workspace_root,
        )

        return local_thread_workspace_root(thread_id).resolve()
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.native.native_sandbox_executor import (
        native_thread_workspace_root,
    )

    return native_thread_workspace_root(thread_id).resolve()


def fetch_native_workspace_tree(thread_id: Optional[str]) -> list[dict[str, Any]]:
    """Build a tree from the current native/local thread workspace and expose it as workspace/."""
    root = _host_workspace_root(thread_id)
    if not root.exists():
        return []

    dir_lines: list[str] = []
    file_lines: list[str] = []
    try:
        for p in root.rglob("*"):
            rel_parts = p.relative_to(root).parts
            if any(part.startswith(".") for part in rel_parts):
                continue
            virtual_path = str(Path(NATIVE_WORKSPACE_ROOT, *rel_parts)).replace("\\", "/")
            (dir_lines if p.is_dir() else file_lines).append(virtual_path)
    except Exception as exc:
        logger.warning(f"[NativeSandbox] workspace tree scan failed: {exc}")

    return sandbox_paths_to_tree(
        dir_lines,
        file_lines,
        sandbox_root=NATIVE_WORKSPACE_ROOT,
        display_root=NATIVE_DISPLAY_ROOT,
    )


def public_path_to_sandbox_abs(path: str) -> str:
    """Map API path to absolute sandbox path under /tmp.

    Accepts ``/tmp/...`` and UI paths like ``tmp/...``. Legacy
    ``/tmp/cuga_workspace/...`` and ``cuga_workspace/...`` inputs are also accepted.
    """
    raw = (path or "").strip().replace("\\", "/")
    if not raw:
        raise ValueError("empty path")
    norm = raw.rstrip("/")
    if norm == LEGACY_SANDBOX_WORKSPACE_ROOT or norm.startswith(LEGACY_SANDBOX_WORKSPACE_ROOT + "/"):
        tail = norm[len(LEGACY_SANDBOX_WORKSPACE_ROOT) :].lstrip("/")
        parts = tail.split("/") if tail else []
        if any(p in ("", ".", "..") or p.startswith(".") for p in parts):
            raise ValueError("invalid path segment")
        return SANDBOX_WORKSPACE_ROOT if not tail else f"{SANDBOX_WORKSPACE_ROOT}/{tail}"
    if norm == SANDBOX_WORKSPACE_ROOT or norm.startswith(SANDBOX_WORKSPACE_ROOT + "/"):
        tail = norm[len(SANDBOX_WORKSPACE_ROOT) :].lstrip("/")
        parts = tail.split("/") if tail else []
        if any(p in ("", ".", "..") or p.startswith(".") for p in parts):
            raise ValueError("invalid path segment")
        return norm if tail else SANDBOX_WORKSPACE_ROOT
    raw_rel = raw.lstrip("/")
    parts = raw_rel.split("/")
    if parts[0] == DISPLAY_ROOT or parts[0] in _LEGACY_DISPLAY_ROOTS:
        tail = parts[1:]
    else:
        raise ValueError("path must be under workspace root")
    if any(p in ("", ".", "..") or p.startswith(".") for p in tail):
        raise ValueError("invalid path segment")
    suffix = "/".join(tail)
    abs_path = SANDBOX_WORKSPACE_ROOT if not suffix else f"{SANDBOX_WORKSPACE_ROOT}/{suffix}"
    if ".." in abs_path.split("/"):
        raise ValueError("path traversal")
    return abs_path


def _native_workspace_resolved(thread_id: Optional[str], path: str) -> Path:
    """Resolve an API path to the per-thread workspace root (native or local mode)."""
    raw = (path or "").strip().replace("\\", "/")
    if not raw:
        raise ValueError("empty path")
    norm = raw.rstrip("/")
    root = _host_workspace_root(thread_id)

    if norm == NATIVE_WORKSPACE_ROOT or norm.startswith(NATIVE_WORKSPACE_ROOT + "/"):
        tail = norm[len(NATIVE_WORKSPACE_ROOT) :].lstrip("/")
    else:
        raw_rel = raw.lstrip("/")
        parts = raw_rel.split("/")
        if parts[0] in {NATIVE_DISPLAY_ROOT, DISPLAY_ROOT, LEGACY_DISPLAY_ROOT} | _LEGACY_DISPLAY_ROOTS:
            tail = "/".join(parts[1:])
        elif norm == SANDBOX_WORKSPACE_ROOT or norm.startswith(SANDBOX_WORKSPACE_ROOT + "/"):
            # Backward-compatible UI/API handling for older /tmp paths: treat /tmp/foo as /workspace/foo.
            tail = norm[len(SANDBOX_WORKSPACE_ROOT) :].lstrip("/")
        elif norm == LEGACY_SANDBOX_WORKSPACE_ROOT or norm.startswith(LEGACY_SANDBOX_WORKSPACE_ROOT + "/"):
            tail = norm[len(LEGACY_SANDBOX_WORKSPACE_ROOT) :].lstrip("/")
        else:
            raise ValueError("path must be under workspace root")

    tail_parts = tail.split("/") if tail else []
    if any(p in ("", ".", "..") or p.startswith(".") for p in tail_parts):
        raise ValueError("invalid path segment")
    resolved = (root / tail).resolve() if tail else root
    try:
        resolved.relative_to(root)
    except ValueError as e:
        raise ValueError(f"path outside sandbox workspace: {path}") from e
    return resolved


def read_native_workspace_bytes(thread_id: Optional[str], path: str) -> tuple[bytes, str]:
    """Read a file from the current native thread workspace."""
    p = _native_workspace_resolved(thread_id, path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    if p.is_dir():
        raise IsADirectoryError(str(p))
    return p.read_bytes(), p.name


def native_workspace_text_preview(
    thread_id: Optional[str], path: str, *, max_size: int = 10 * 1024 * 1024
) -> str:
    """Return UTF-8 text preview for a native-sandbox file; enforce size limit."""
    p = _native_workspace_resolved(thread_id, path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    if p.is_dir():
        raise IsADirectoryError(str(p))
    if p.stat().st_size > max_size:
        raise OSError("file too large")
    return p.read_text(encoding="utf-8")


async def read_sandbox_workspace_bytes(thread_id: Optional[str], path: str) -> tuple[bytes, str]:
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.code_executor import CodeExecutor

    sandbox_path = public_path_to_sandbox_abs(path)
    executor = CodeExecutor._get_opensandbox_executor()
    interpreter = await executor.get_interpreter_for_thread(thread_id)
    data = await interpreter.sandbox.files.read_bytes(sandbox_path)
    name = sandbox_path.rsplit("/", 1)[-1]
    return data, name


async def sandbox_text_preview(
    thread_id: Optional[str], api_path: str, *, max_size: int = 10 * 1024 * 1024
) -> str:
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.code_executor import CodeExecutor

    sandbox_path = public_path_to_sandbox_abs(api_path)
    executor = CodeExecutor._get_opensandbox_executor()
    interpreter = await executor.get_interpreter_for_thread(thread_id)
    infos = await interpreter.sandbox.files.get_file_info([sandbox_path])
    info = infos.get(sandbox_path) if infos else None
    if not info:
        raise FileNotFoundError(sandbox_path)
    if int(info.size) > max_size:
        raise OSError("file too large")
    try:
        data = await interpreter.sandbox.files.read_bytes(sandbox_path)
    except Exception as exc:
        low = str(exc).lower()
        if "is a directory" in low or "is a dir" in low or "eisdir" in low:
            raise IsADirectoryError(sandbox_path) from exc
        raise
    return data.decode("utf-8")
