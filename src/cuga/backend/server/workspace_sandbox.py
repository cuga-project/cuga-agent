"""OpenSandbox-backed workspace tree/file access for /api/workspace when skills + OpenSandbox are on."""

from __future__ import annotations

import shlex
from typing import Any, Optional

from loguru import logger

from cuga.config import settings

SANDBOX_WORKSPACE_ROOT = "/tmp/cuga_workspace"
DISPLAY_ROOT = "cuga_workspace"


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
        pub_path = (
            f"{SANDBOX_WORKSPACE_ROOT}/{'/'.join(path_parts)}" if path_parts else SANDBOX_WORKSPACE_ROOT
        )
        if names[name] == "file":
            items.append({"name": name, "path": pub_path, "type": "file"})
        else:
            ch = _children_nodes(path_parts, dir_rels, file_rels)
            items.append({"name": name, "path": pub_path, "type": "directory", "children": ch})
    return items


def sandbox_paths_to_tree(dir_lines: list[str], file_lines: list[str]) -> list[dict[str, Any]]:
    dir_rels, file_rels = _collect_dir_and_file_sets(dir_lines, file_lines, SANDBOX_WORKSPACE_ROOT)
    return _children_nodes(tuple(), dir_rels, file_rels)


def _execution_debug_blob(ex: Any) -> str:
    parts: list[str] = []
    exit_code = getattr(ex, "exit_code", None)
    if exit_code is not None:
        parts.append(f"exit_code={exit_code}")
    err = getattr(ex, "error", None)
    if err is not None:
        parts.append(f"error={err!r}")
    logs = getattr(ex, "logs", None)
    if logs is not None and getattr(logs, "stderr", None):
        stderr = "".join(getattr(m, "text", "") for m in logs.stderr)
        if stderr.strip():
            parts.append(f"stderr={stderr.strip()[:400]!r}")
    stdout_preview = (ex.text if hasattr(ex, "text") else "")[:300].replace("\n", "\\n")
    if stdout_preview.strip():
        parts.append(f"stdout_preview={stdout_preview!r}")
    return " ".join(parts) if parts else "(no execution details)"


async def _find_paths(commands: Any, type_flag: str) -> list[str]:
    q = shlex.quote(SANDBOX_WORKSPACE_ROOT)
    cmd = f"find {q} -type {type_flag} 2>/dev/null | sort"
    ex = await commands.run(cmd)
    text = ex.text if hasattr(ex, "text") else ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    logger.debug(
        "sandbox find: type={} cmd={} lines={} {}",
        type_flag,
        cmd,
        len(lines),
        _execution_debug_blob(ex),
    )
    if not lines and type_flag == "d":
        logger.debug(
            "sandbox find returned no directories for {}; if unexpected, check OpenSandbox find output: {}",
            SANDBOX_WORKSPACE_ROOT,
            _execution_debug_blob(ex),
        )
    return lines


async def fetch_sandbox_workspace_tree(thread_id: Optional[str]) -> list[dict[str, Any]]:
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.code_executor import CodeExecutor

    executor = CodeExecutor._get_opensandbox_executor()
    interpreter = await executor.get_interpreter_for_thread(thread_id)
    sandbox = interpreter.sandbox
    sandbox_id = getattr(sandbox, "id", None)
    commands = sandbox.commands
    logger.debug(
        "sandbox workspace tree: thread_id={!r} sandbox_id={!r} root={}",
        thread_id,
        sandbox_id,
        SANDBOX_WORKSPACE_ROOT,
    )
    dir_lines = await _find_paths(commands, "d")
    file_lines = await _find_paths(commands, "f")
    tree = sandbox_paths_to_tree(dir_lines, file_lines)
    logger.debug(
        "sandbox workspace tree: thread_id={!r} sandbox_id={!r} find_dirs={} find_files={} top_level_nodes={} "
        "sample_dir_lines={}",
        thread_id,
        sandbox_id,
        len(dir_lines),
        len(file_lines),
        len(tree),
        dir_lines[:5],
    )
    if not tree and (dir_lines or file_lines):
        logger.warning(
            "sandbox workspace tree: find returned paths but tree is empty — sample file_lines={} dir_lines={}",
            file_lines[:8],
            dir_lines[:8],
        )
    return tree


def public_path_to_sandbox_abs(path: str) -> str:
    """Map API path to absolute sandbox path under /tmp/cuga_workspace.

    Accepts ``/tmp/cuga_workspace/...`` (sandbox UI paths) or legacy ``cuga_workspace/...``.
    """
    raw = (path or "").strip().replace("\\", "/")
    if not raw:
        raise ValueError("empty path")
    norm = raw.rstrip("/")
    if norm == SANDBOX_WORKSPACE_ROOT or norm.startswith(SANDBOX_WORKSPACE_ROOT + "/"):
        tail = norm[len(SANDBOX_WORKSPACE_ROOT) :].lstrip("/")
        parts = tail.split("/") if tail else []
        if any(p in ("", ".", "..") or p.startswith(".") for p in parts):
            raise ValueError("invalid path segment")
        return norm if tail else SANDBOX_WORKSPACE_ROOT
    raw_rel = raw.lstrip("/")
    parts = raw_rel.split("/")
    if parts[0] != DISPLAY_ROOT:
        raise ValueError("path must be under workspace root")
    tail = parts[1:]
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
