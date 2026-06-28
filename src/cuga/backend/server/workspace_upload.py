"""Thread-scoped workspace file uploads for the chat UI."""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.filesystem.paths import (
    VIRTUAL_WORKSPACE_ROOT,
    child_path_under,
    ensure_thread_workspace_seeded,
    local_base_dir,
    relative_workspace_path,
    remove_file_under,
    resolve_workspace_path,
    safe_thread_id,
    shell_workspace_path,
    thread_workspace_root,
    write_bytes_under,
)
from cuga.backend.server.workspace_sandbox import workspace_tree_is_sandbox_backed

UPLOADS_SUBDIR = "uploads"
MANIFEST_NAME = ".manifest.json"
ALLOWED_SUFFIXES = {".json", ".jsonl", ".ndjson"}
MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # ponytail: matches knowledge max_upload_size_mb default


def sanitize_upload_filename(filename: str) -> str:
    name = Path((filename or "").strip()).name
    if not name or name.startswith("."):
        raise ValueError("Invalid filename")
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError(f"Unsupported file type {suffix!r}; allowed: {', '.join(sorted(ALLOWED_SUFFIXES))}")
    stem = Path(name).stem
    safe_stem = re.sub(r"[^\w.-]", "_", stem, flags=re.ASCII)
    safe_stem = re.sub(r"_+", "_", safe_stem).strip("_")
    if not safe_stem:
        raise ValueError("Invalid filename")
    return f"{safe_stem}{suffix}"


def validate_upload_content(data: bytes, filename: str) -> None:
    """Reject non-UTF-8 or invalid JSON/JSONL/NDJSON payloads before persistence."""
    suffix = Path(sanitize_upload_filename(filename)).suffix.lower()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Upload must be valid UTF-8") from exc
    if suffix == ".json":
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON: {exc}") from exc
        return
    if suffix in {".jsonl", ".ndjson"}:
        for line in text.splitlines():
            if line.strip():
                try:
                    json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid JSONL/NDJSON: {exc}") from exc
        return
    raise ValueError(f"Unsupported file type {suffix!r}")


def _unique_upload_name(safe_name: str, manifest: dict[str, Any]) -> str:
    existing = {f.get("name") for f in manifest.get("files", [])}
    if safe_name not in existing:
        return safe_name
    stem, suffix = Path(safe_name).stem, Path(safe_name).suffix
    return f"{stem}_{secrets.token_hex(4)}{suffix}"


def relative_upload_path(filename: str) -> str:
    return relative_workspace_path(f"{UPLOADS_SUBDIR}/{filename}")


def sandbox_upload_path(filename: str) -> str:
    return f"{VIRTUAL_WORKSPACE_ROOT}/{UPLOADS_SUBDIR}/{filename}"


def display_upload_path(filename: str) -> str:
    return f"workspace/{UPLOADS_SUBDIR}/{filename}"


def manifest_sandbox_path() -> str:
    return f"{VIRTUAL_WORKSPACE_ROOT}/{UPLOADS_SUBDIR}/{MANIFEST_NAME}"


def _validated_thread_id(thread_id: Optional[str]) -> str:
    """Sanitize thread_id before any filesystem path construction."""
    return safe_thread_id(thread_id)


def _uploads_root_host(tid: str) -> Path:
    """Host ``uploads/`` dir for a sanitized thread id (static path segments only)."""
    return child_path_under(thread_workspace_root(tid), UPLOADS_SUBDIR)


def _manifest_host_path(tid: str) -> Path:
    return child_path_under(_uploads_root_host(tid), MANIFEST_NAME)


def _upload_file_host_path(tid: str, safe_name: str) -> Path:
    if Path(safe_name).name != safe_name or safe_name in (".", ".."):
        raise ValueError("Invalid filename")
    return child_path_under(_uploads_root_host(tid), safe_name)


def read_upload_manifest(thread_id: Optional[str]) -> dict[str, Any]:
    if not (thread_id or "").strip():
        return {"thread_id": "", "files": []}
    tid = _validated_thread_id(thread_id)
    path = _manifest_host_path(tid)
    if not path.is_file():
        return {"thread_id": thread_id or "", "files": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("files"), list):
            return data
    except Exception as exc:
        logger.warning(f"[workspace_upload] corrupt manifest for thread={thread_id}: {exc}")
    return {"thread_id": thread_id or "", "files": []}


def _write_manifest_host(thread_id: Optional[str], manifest: dict[str, Any]) -> None:
    tid = _validated_thread_id(thread_id)
    path = _manifest_host_path(tid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


async def _write_manifest_remote(thread_id: Optional[str], manifest: dict[str, Any]) -> None:
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.filesystem.backends import RemoteSandboxBackend
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.code_executor import CodeExecutor

    backend = RemoteSandboxBackend(CodeExecutor._get_opensandbox_executor(), thread_id)
    await backend.write_text(
        manifest_sandbox_path(), json.dumps(manifest, indent=2), operation="write_manifest"
    )


def _agent_path_from_manifest_entry(entry: dict[str, Any]) -> str:
    """Normalize legacy manifest entries to a single workspace-relative path."""
    legacy_shell = entry.get("shell_path")
    if legacy_shell:
        return str(legacy_shell)
    raw = str(entry.get("path") or entry.get("name") or "")
    if raw.startswith(VIRTUAL_WORKSPACE_ROOT + "/") or raw == VIRTUAL_WORKSPACE_ROOT:
        return shell_workspace_path(raw)
    return relative_workspace_path(raw)


def _merge_manifest_entry(
    manifest: dict[str, Any], *, thread_id: Optional[str], filename: str, size_bytes: int
) -> dict[str, Any]:
    entry = {
        "name": filename,
        "path": relative_upload_path(filename),
        "size_bytes": size_bytes,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }
    files = [f for f in manifest.get("files", []) if f.get("name") != filename]
    files.append(entry)
    return {"thread_id": thread_id or "", "files": files}


async def upload_workspace_bytes(
    thread_id: Optional[str],
    filename: str,
    data: bytes,
) -> dict[str, Any]:
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(f"File too large ({len(data)} bytes; max {MAX_UPLOAD_BYTES})")

    if not (thread_id or "").strip():
        raise ValueError("thread_id is required for uploads")
    tid = _validated_thread_id(thread_id)
    ensure_thread_workspace_seeded(tid)
    safe_name = sanitize_upload_filename(filename)
    manifest = read_upload_manifest(tid)
    safe_name = _unique_upload_name(safe_name, manifest)
    validate_upload_content(data, safe_name)
    sp = sandbox_upload_path(safe_name)
    manifest = _merge_manifest_entry(manifest, thread_id=tid, filename=safe_name, size_bytes=len(data))
    workspace_base = local_base_dir()

    if workspace_tree_is_sandbox_backed():
        from cuga.backend.cuga_graph.nodes.cuga_lite.executors.filesystem.backends import RemoteSandboxBackend
        from cuga.backend.cuga_graph.nodes.cuga_lite.executors.code_executor import CodeExecutor

        backend = RemoteSandboxBackend(CodeExecutor._get_opensandbox_executor(), tid)
        tmp_name = f".upload-{secrets.token_hex(8)}.tmp"
        tmp = write_bytes_under(workspace_base, data, tid, UPLOADS_SUBDIR, tmp_name)
        try:
            await backend.upload(tmp, sp)
        finally:
            remove_file_under(tmp, workspace_base)
        _write_manifest_host(tid, manifest)
        await _write_manifest_remote(tid, manifest)
    else:
        write_bytes_under(workspace_base, data, tid, UPLOADS_SUBDIR, safe_name)
        _write_manifest_host(tid, manifest)

    return {
        "path": display_upload_path(safe_name),
        "sandbox_path": sp,
        "size_bytes": len(data),
        "manifest": manifest,
    }


def format_upload_context(thread_id: Optional[str]) -> str | None:
    manifest = read_upload_manifest(thread_id)
    files = manifest.get("files") or []
    if not files:
        return None
    lines = ["## Session uploads", "", "JSON files uploaded for this conversation:"]
    for f in files:
        size_mb = (f.get("size_bytes") or 0) / (1024 * 1024)
        agent_path = _agent_path_from_manifest_entry(f)
        lines.append(f"- `{agent_path}` ({size_mb:.1f} MB)")
    lines.append("")
    lines.append(
        "Use each path as-is in `read_file`, `write_file`, `list_files`, and `run_command` "
        "(e.g. `head -c 2048 ./uploads/foo.json`, `python ./scripts/parse.py` with "
        "`open('uploads/foo.json')` inside the script). Absolute `/workspace/...` is also accepted."
    )
    return "\n".join(lines)


async def delete_thread_uploads(thread_id: Optional[str]) -> None:
    tid = (thread_id or "").strip()
    if not tid:
        return
    safe_tid = _validated_thread_id(tid)
    uploads_dir = _uploads_root_host(safe_tid)
    if uploads_dir.exists():
        shutil.rmtree(uploads_dir)
        logger.info(f"[workspace_upload] removed host uploads for thread={safe_tid}")

    if workspace_tree_is_sandbox_backed():
        from cuga.backend.cuga_graph.nodes.cuga_lite.executors.code_executor import CodeExecutor

        executor = CodeExecutor._get_opensandbox_executor()
        interpreter = await executor.get_interpreter_for_thread(safe_tid)
        uploads_sp = f"{VIRTUAL_WORKSPACE_ROOT}/{UPLOADS_SUBDIR}"
        try:
            await interpreter.sandbox.commands.run(f"rm -rf {uploads_sp}")
        except Exception as exc:
            logger.warning(f"[workspace_upload] sandbox remove uploads for thread={safe_tid}: {exc}")


def resolve_host_workspace_path(user_path: str, thread_id: Optional[str]) -> Path:
    """Map UI/API path to host filesystem path under the thread workspace."""
    tid = _validated_thread_id(thread_id)
    raw = (user_path or "").strip().replace("\\", "/").lstrip("/")
    parts = raw.split("/") if raw else []
    if parts and parts[0] in {"workspace", "cuga_workspace", UPLOADS_SUBDIR}:
        if parts[0] in {"workspace", "cuga_workspace"}:
            parts = parts[1:]
    suffix = "/".join(parts)
    sandbox_path = VIRTUAL_WORKSPACE_ROOT if not suffix else f"{VIRTUAL_WORKSPACE_ROOT}/{suffix}"
    return resolve_workspace_path(sandbox_path, thread_id=tid, operation="access")


def fetch_host_workspace_tree(thread_id: Optional[str]) -> list[dict[str, Any]]:
    """Build workspace tree from host disk (non-sandbox modes)."""
    tid = _validated_thread_id(thread_id)
    ensure_thread_workspace_seeded(tid)
    root = thread_workspace_root(tid)
    if not root.exists():
        return []

    sandbox_root = VIRTUAL_WORKSPACE_ROOT
    dir_lines: list[str] = []
    file_lines: list[str] = []

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        rel = Path(dirpath).relative_to(root)
        if rel.parts:
            dir_lines.append(  # codeql[py/path-injection] paths from os.walk under validated root
                str(Path(sandbox_root, *rel.parts)).replace("\\", "/")
            )
        for name in filenames:
            if name.startswith("."):
                continue
            file_lines.append(  # codeql[py/path-injection] paths from os.walk under validated root
                str(Path(sandbox_root, *rel.parts, name)).replace("\\", "/")
            )

    from cuga.backend.server.workspace_sandbox import sandbox_paths_to_tree

    return sandbox_paths_to_tree(dir_lines, file_lines, sandbox_root=sandbox_root, display_root="workspace")
