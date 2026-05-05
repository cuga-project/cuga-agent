"""OpenSandbox-backed workspace tree/file access for /api/workspace when skills + OpenSandbox are on."""

from __future__ import annotations

import shlex
from typing import Any, Optional

from loguru import logger

from cuga.config import settings

SANDBOX_WORKSPACE_ROOT = "/tmp"
DISPLAY_ROOT = "tmp"
LEGACY_DISPLAY_ROOT = "cuga_workspace"
LEGACY_SANDBOX_WORKSPACE_ROOT = "/tmp/cuga_workspace"


def workspace_tree_is_sandbox_backed() -> bool:
    return bool(
        getattr(settings.skills, "enabled", False)
        and getattr(settings.advanced_features, "opensandbox_sandbox", False)
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
        pub_path = f"{DISPLAY_ROOT}/{'/'.join(path_parts)}" if path_parts else DISPLAY_ROOT
        if names[name] == "file":
            items.append({"name": name, "path": pub_path, "type": "file"})
        else:
            ch = _children_nodes(path_parts, dir_rels, file_rels)
            items.append({"name": name, "path": pub_path, "type": "directory", "children": ch})
    return items


def sandbox_paths_to_tree(dir_lines: list[str], file_lines: list[str]) -> list[dict[str, Any]]:
    dir_rels, file_rels = _collect_dir_and_file_sets(dir_lines, file_lines, SANDBOX_WORKSPACE_ROOT)
    return _children_nodes(tuple(), dir_rels, file_rels)


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
    if parts[0] == DISPLAY_ROOT:
        tail = parts[1:]
    elif parts[0] == LEGACY_DISPLAY_ROOT:
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
