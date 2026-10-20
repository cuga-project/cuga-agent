"""Filesystem backend that puts the agent's files in the execd sandbox.

Without this the execd mode splits the workspace in two: generated Python runs
in the sandbox and writes to ``/workspace/<thread>`` there, while the
filesystem tools fall back to the host and look in the CUGA container. The
agent writes a file with code and then cannot read it back.

Operations travel over ``POST /command`` rather than the code context on
purpose. The context is the agent's own Jupyter kernel: it executes one cell at
a time, so a workspace listing issued while the agent is running code would
queue behind it, and helper names would land in the namespace the agent's
variables live in. A command is a fresh process — no queueing, no pollution.

Neither the operation nor its payload is interpolated into the shell command.
A fixed one-liner reads both from the environment, so file contents, paths and
patterns never pass through shell quoting, and binary content survives intact.
"""

from __future__ import annotations

import base64
import json
import posixpath
from pathlib import Path
from typing import Any, List, Optional

from loguru import logger

from ..filesystem.backends import FilesystemBackend
from ..filesystem.models import DownloadResult, FileEntry, ListFilesResult, UploadResult
from ..filesystem.paths import read_bytes_under
from ..filesystem.paths import VIRTUAL_WORKSPACE_ROOT, local_base_dir

LEGACY_SANDBOX_ROOT = "/tmp"

# Runs inside the sandbox. Mirrors HostWorkspaceBackend so the agent sees the
# same semantics — same glob rules, same stat fields — whichever backend is
# wired underneath it.
_HELPER = r'''
import base64, fnmatch, glob, json, os, shutil, sys
from datetime import datetime
from pathlib import Path

spec = json.loads(base64.b64decode(os.environ["CUGA_FS_OP"]).decode("utf-8"))
root = Path(spec["root"])
op = spec["op"]
args = spec.get("args", {})


def resolve(rel):
    """Join a workspace-relative path onto the root, refusing to leave it."""
    target = (root / rel.lstrip("/")).resolve() if rel else root.resolve()
    base = root.resolve()
    if target != base and base not in target.parents:
        raise ValueError("path escapes the workspace: %s" % rel)
    return target


def public(p):
    rel = os.path.relpath(str(p), str(root.resolve()))
    return "/workspace" if rel == "." else "/workspace/" + rel.replace(os.sep, "/")


def run():
    if op == "read_text":
        p = resolve(args["path"])
        if not p.is_file():
            raise FileNotFoundError("File not found in workspace: %s" % args["path"])
        return {"text": p.read_text(encoding="utf-8", errors="replace")}

    if op == "write_text":
        p = resolve(args["path"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(args["content"], encoding="utf-8")
        return {"path": public(p)}

    if op == "exists":
        try:
            return {"exists": resolve(args["path"]).exists()}
        except ValueError:
            return {"exists": False}

    if op == "mkdir":
        p = resolve(args["path"])
        p.mkdir(parents=True, exist_ok=True)
        return {"path": public(p)}

    if op == "move":
        src, dst = resolve(args["source"]), resolve(args["destination"])
        if dst.exists():
            raise ValueError("Destination already exists: %s" % args["destination"])
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.rename(str(src), str(dst))
        return {"source": public(src), "destination": public(dst)}

    if op == "list_dir":
        p = resolve(args["path"])
        if p == root.resolve():
            p.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            raise FileNotFoundError("Path not found: %s" % args["path"])
        entries = []
        for child in sorted(p.glob(args["pattern"])):
            entries.append({
                "name": child.name,
                "path": public(child),
                "is_dir": child.is_dir(),
                "size_bytes": child.stat().st_size if child.is_file() else 0,
            })
        return {"entries": entries}

    if op == "search":
        base = resolve(args["path"])
        pattern, exclude = args["pattern"], args.get("exclude") or []
        results = []
        if "**" in pattern:
            for match in glob.glob(str(base / pattern), recursive=True):
                rel = os.path.relpath(match, str(base))
                skip = any(
                    fnmatch.fnmatch(rel, ex)
                    or fnmatch.fnmatch(rel, "**/" + ex)
                    or fnmatch.fnmatch(rel, "**/" + ex + "/**")
                    for ex in exclude
                )
                if not skip:
                    results.append(public(Path(match)))
        else:
            for item in sorted(os.listdir(str(base))):
                if fnmatch.fnmatch(item, pattern) and not any(fnmatch.fnmatch(item, ex) for ex in exclude):
                    results.append(public(base / item))
        return {"paths": results}

    if op == "stat":
        p = resolve(args["path"])
        st = p.stat()
        return {"info": {
            "path": public(p),
            "size": st.st_size,
            "created": datetime.fromtimestamp(st.st_ctime).isoformat(),
            "modified": datetime.fromtimestamp(st.st_mtime).isoformat(),
            "accessed": datetime.fromtimestamp(st.st_atime).isoformat(),
            "isDirectory": p.is_dir(),
            "isFile": p.is_file(),
            "permissions": oct(st.st_mode)[-3:],
        }}

    if op == "walk":
        base = resolve(args.get("path", ""))
        dirs, files = [], []
        if base.exists():
            for dirpath, dirnames, filenames in os.walk(str(base)):
                for name in sorted(dirnames):
                    dirs.append(public(Path(dirpath) / name))
                for name in sorted(filenames):
                    files.append(public(Path(dirpath) / name))
        return {"dirs": sorted(dirs), "files": sorted(files)}

    if op == "rmtree":
        p = resolve(args["path"])
        if p.exists():
            shutil.rmtree(str(p), ignore_errors=True)
        return {"path": public(p)}

    if op == "read_bytes":
        p = resolve(args["path"])
        if not p.is_file():
            raise FileNotFoundError("File not found in sandbox: %s" % args["path"])
        return {"data": base64.b64encode(p.read_bytes()).decode("ascii")}

    if op == "write_bytes":
        p = resolve(args["path"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(base64.b64decode(args["data"]))
        return {"path": public(p)}

    raise ValueError("unknown op: %s" % op)


try:
    payload = {"ok": True, "result": run()}
except Exception as exc:
    payload = {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}

# One framed line: the command's own stdout may carry unrelated noise.
sys.stdout.write("\nCUGA_FS_RESULT:" + base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii") + "\n")
'''

# Fixed string — nothing is interpolated, so there is nothing to quote.
_INVOKE = 'python3 -c \'import os; exec(os.environ["CUGA_FS_SRC"])\''
_RESULT_MARKER = "CUGA_FS_RESULT:"


class ExecdFilesystemBackend(FilesystemBackend):
    """Adapter putting the consolidated filesystem tools onto execd."""

    def __init__(self, executor: Any, thread_id: Optional[str] = None) -> None:
        self.executor = executor
        self.thread_id = thread_id

    # ------------------------------------------------------------------ #
    # Transport                                                            #
    # ------------------------------------------------------------------ #

    def _rel(self, path: str) -> str:
        """Map an agent-facing path to one relative to the thread workspace.

        The agent always sees ``/workspace``. Under execd that is not the
        container's ``/workspace`` but ``/workspace/<thread>`` inside it — one
        container holds every thread, so collapsing the two would put unrelated
        sessions in one directory.
        """
        raw = (path or "").strip().replace("\\", "/")
        if not raw:
            raise ValueError("empty sandbox path")
        for prefix in (VIRTUAL_WORKSPACE_ROOT, LEGACY_SANDBOX_ROOT):
            if raw == prefix:
                return ""
            if raw.startswith(prefix + "/"):
                raw = raw[len(prefix) :]
                break
        else:
            if raw.startswith("/"):
                raise ValueError("sandbox path must be under /workspace")
        rel = posixpath.normpath(raw.lstrip("/"))
        if rel == ".":
            return ""
        if rel == ".." or rel.startswith("../"):
            raise ValueError(f"path escapes the workspace: {path}")
        return rel

    async def _call(self, op: str, **args: Any) -> dict:
        safe_args = {k: v for k, v in args.items() if k != "data" and k != "content"}
        logger.debug(f"[execd:fs] _call op={op} args={safe_args} thread={self.thread_id}")
        spec = {"root": self.executor._workspace_path(self.thread_id), "op": op, "args": args}
        envs = {
            "CUGA_FS_SRC": _HELPER,
            "CUGA_FS_OP": base64.b64encode(json.dumps(spec).encode("utf-8")).decode("ascii"),
        }
        stdout, stderr, failed = await self.executor._run_shell_command(
            _INVOKE, self.thread_id, extra_env=envs
        )

        marker_line = next(
            (ln for ln in reversed(stdout.splitlines()) if ln.startswith(_RESULT_MARKER)), None
        )
        if marker_line is None:
            detail = (stderr or stdout).strip() or "no result from the sandbox"
            raise RuntimeError(f"execd filesystem op {op!r} produced no result: {detail}")

        payload = json.loads(base64.b64decode(marker_line[len(_RESULT_MARKER) :]).decode("utf-8"))
        if not payload.get("ok"):
            message = payload.get("error", "unknown error")
            # Re-raise as the type the tools already handle, so error text
            # reads the same as it does on the host backend.
            if message.startswith("FileNotFoundError"):
                raise FileNotFoundError(message.split(": ", 1)[-1])
            if message.startswith("ValueError"):
                raise ValueError(message.split(": ", 1)[-1])
            raise RuntimeError(message)
        if failed:
            logger.debug(f"[ExecdFilesystemBackend] {op} reported a non-zero exit but returned a result")
        return payload["result"]

    # ------------------------------------------------------------------ #
    # FilesystemBackend                                                    #
    # ------------------------------------------------------------------ #

    async def read_text(self, path: str, *, operation: str) -> str:
        return (await self._call("read_text", path=self._rel(path)))["text"]

    async def write_text(self, path: str, content: str, *, operation: str) -> str:
        return (await self._call("write_text", path=self._rel(path), content=content))["path"]

    async def exists(self, path: str, *, operation: str) -> bool:
        try:
            return bool((await self._call("exists", path=self._rel(path)))["exists"])
        except ValueError:
            return False

    async def mkdir(self, path: str) -> str:
        return (await self._call("mkdir", path=self._rel(path)))["path"]

    async def move(self, source: str, destination: str) -> tuple[str, str]:
        result = await self._call("move", source=self._rel(source), destination=self._rel(destination))
        return result["source"], result["destination"]

    async def list_dir(self, path: str, pattern: str) -> ListFilesResult:
        result = await self._call("list_dir", path=self._rel(path), pattern=pattern)
        return ListFilesResult(sandbox_path=path, entries=[FileEntry(**entry) for entry in result["entries"]])

    async def search(self, path: str, pattern: str, exclude: List[str]) -> List[str]:
        result = await self._call("search", path=self._rel(path), pattern=pattern, exclude=exclude)
        return result["paths"]

    async def stat(self, path: str) -> dict:
        return (await self._call("stat", path=self._rel(path)))["info"]

    async def download(self, sandbox_path: str, filename: Optional[str]) -> DownloadResult:
        result = await self._call("read_bytes", path=self._rel(sandbox_path))
        data = base64.b64decode(result["data"])
        dest_dir = local_base_dir()
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / (filename or Path(self._rel(sandbox_path) or "workspace").name)
        dest.write_bytes(data)
        logger.info(f"[ExecdFilesystemBackend] Downloaded {sandbox_path} → {dest} ({len(data)} bytes)")
        return DownloadResult(sandbox_path=sandbox_path, local_path=str(dest), size_bytes=len(data))

    async def upload(self, local_path: Path | str, sandbox_path: str) -> UploadResult:
        base = local_base_dir()
        try:
            payload = read_bytes_under(Path(local_path), base)
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"Local file not found: {local_path}") from exc
        await self._call(
            "write_bytes",
            path=self._rel(sandbox_path),
            data=base64.b64encode(payload).decode("ascii"),
        )
        logger.info(f"[ExecdFilesystemBackend] Uploaded {local_path} → {sandbox_path}")
        return UploadResult(local_path=str(local_path), sandbox_path=sandbox_path)

    # ------------------------------------------------------------------ #
    # Beyond the ABC — what the workspace API needs                        #
    # ------------------------------------------------------------------ #

    async def walk(self, path: str = VIRTUAL_WORKSPACE_ROOT) -> tuple[List[str], List[str]]:
        """Return (directories, files) under ``path`` as agent-facing paths.

        The workspace tree endpoint wants both lists at once; building them from
        ``list_dir`` would be one round trip per directory.

        A thread without a kernel has an empty workspace. Resolving one here
        would create it: the UI polls the tree every few seconds, so a fresh
        or just-deleted conversation would get a kernel nothing ever releases.
        """
        if not self.executor.has_context(self.thread_id):
            return [], []
        result = await self._call("walk", path=self._rel(path))
        return result["dirs"], result["files"]

    async def read_bytes(self, path: str) -> bytes:
        """Read a file without writing a copy to the host, unlike ``download``."""
        return base64.b64decode((await self._call("read_bytes", path=self._rel(path)))["data"])

    async def remove_tree(self, path: str) -> None:
        # Nothing to remove without a kernel; creating one just to delete from it
        # leaves a kernel behind when a conversation is deleted.
        if not self.executor.has_context(self.thread_id):
            return
        await self._call("rmtree", path=self._rel(path))

    async def preview_text(self, path: str, *, max_size: int) -> str:
        """Read a file for the UI, refusing directories and oversized files."""
        meta = await self.stat(path)
        if meta.get("isDirectory"):
            raise IsADirectoryError(path)
        if int(meta.get("size", 0)) > max_size:
            raise OSError("file too large")
        return await self.read_text(path, operation="sandbox_text_preview")


__all__ = ["ExecdFilesystemBackend"]
