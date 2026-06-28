"""Storage backends for the consolidated filesystem tools.

``WorkspaceFilesystem`` holds the LLM-facing behavior (line slicing, grep,
git-style edit diffs, JSON contracts); a ``FilesystemBackend`` supplies the
raw storage primitives. Two backends:

* ``HostWorkspaceBackend`` — host filesystem under ``<cwd>/cuga_workspace``
  (per-thread when ``thread_id`` is set). Serves the chat
  agent and the ``local`` / ``native`` sandbox modes.
* ``RemoteSandboxBackend`` — a thin adapter over ``OpenSandboxExecutor``'s
  remote ``interpreter.sandbox.files.*`` API for the ``opensandbox`` mode.
"""

from __future__ import annotations

import fnmatch
import glob
import os
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

from loguru import logger

from .models import DownloadResult, FileEntry, ListFilesResult, UploadResult
from .paths import (
    VIRTUAL_WORKSPACE_ROOT,
    local_base_dir,
    public_workspace_path,
    resolve_workspace_path,
    thread_workspace_root,
)


class FilesystemBackend(ABC):
    """Storage primitives composed by ``WorkspaceFilesystem``."""

    @abstractmethod
    async def read_text(self, path: str, *, operation: str) -> str: ...

    @abstractmethod
    async def write_text(self, path: str, content: str, *, operation: str) -> str:
        """Write content (creating parents); return a display path."""

    @abstractmethod
    async def exists(self, path: str, *, operation: str) -> bool: ...

    @abstractmethod
    async def mkdir(self, path: str) -> str:
        """Create a directory (and parents); return a display path."""

    @abstractmethod
    async def move(self, source: str, destination: str) -> tuple[str, str]:
        """Move source→destination; raise ValueError if destination exists."""

    @abstractmethod
    async def list_dir(self, path: str, pattern: str) -> ListFilesResult: ...

    @abstractmethod
    async def search(self, path: str, pattern: str, exclude: List[str]) -> List[str]: ...

    @abstractmethod
    async def stat(self, path: str) -> dict: ...

    @abstractmethod
    async def download(self, sandbox_path: str, filename: Optional[str]) -> DownloadResult: ...

    @abstractmethod
    async def upload(self, local_path: Path | str, sandbox_path: str) -> UploadResult: ...


# --------------------------------------------------------------------------- #
# Host backend                                                                 #
# --------------------------------------------------------------------------- #


class HostWorkspaceBackend(FilesystemBackend):
    """Host filesystem rooted at the per-thread/shared cuga_workspace."""

    def __init__(self, thread_id: Optional[str] = None) -> None:
        self.thread_id = thread_id

    def _resolve(self, path: str, *, operation: str) -> Path:
        return resolve_workspace_path(path, thread_id=self.thread_id, operation=operation)

    def _public(self, p: Path) -> str:
        return public_workspace_path(p, thread_id=self.thread_id)

    async def read_text(self, path: str, *, operation: str) -> str:
        return self._resolve(path, operation=operation).read_text(encoding="utf-8", errors="replace")

    async def write_text(self, path: str, content: str, *, operation: str) -> str:
        p = self._resolve(path, operation=operation)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return self._public(p)

    async def exists(self, path: str, *, operation: str) -> bool:
        return self._resolve(path, operation=operation).exists()

    async def mkdir(self, path: str) -> str:
        p = self._resolve(path, operation="make_directory")
        p.mkdir(parents=True, exist_ok=True)
        return self._public(p)

    async def move(self, source: str, destination: str) -> tuple[str, str]:
        src = self._resolve(source, operation="move_file")
        dst = self._resolve(destination, operation="move_file")
        if dst.exists():
            raise ValueError(f"Destination already exists: {destination}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.rename(src, dst)
        return self._public(src), self._public(dst)

    async def list_dir(self, path: str, pattern: str) -> ListFilesResult:
        p = self._resolve(path, operation="list_files")
        if p == thread_workspace_root(self.thread_id).resolve():
            p.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            raise FileNotFoundError(f"Path not found: {path}")
        entries: list[FileEntry] = []
        for child in sorted(p.glob(pattern)):
            entries.append(
                FileEntry(
                    name=child.name,
                    path=self._public(child),
                    is_dir=child.is_dir(),
                    size_bytes=child.stat().st_size if child.is_file() else 0,
                )
            )
        return ListFilesResult(sandbox_path=path, entries=entries)

    async def search(self, path: str, pattern: str, exclude: List[str]) -> List[str]:
        root = self._resolve(path, operation="search_files")
        results: list[str] = []
        if "**" in pattern:
            for match in glob.glob(str(root / pattern), recursive=True):
                rel = os.path.relpath(match, root)
                if not any(
                    fnmatch.fnmatch(rel, ex)
                    or fnmatch.fnmatch(rel, f"**/{ex}")
                    or fnmatch.fnmatch(rel, f"**/{ex}/**")
                    for ex in exclude
                ):
                    results.append(self._public(Path(match)))
        else:
            for item in sorted(os.listdir(root)):
                if fnmatch.fnmatch(item, pattern) and not any(fnmatch.fnmatch(item, ex) for ex in exclude):
                    results.append(self._public(root / item))
        return results

    async def stat(self, path: str) -> dict:
        p = self._resolve(path, operation="get_file_info")
        st = p.stat()
        return {
            "path": self._public(p),
            "size": st.st_size,
            "created": datetime.fromtimestamp(st.st_ctime).isoformat(),
            "modified": datetime.fromtimestamp(st.st_mtime).isoformat(),
            "accessed": datetime.fromtimestamp(st.st_atime).isoformat(),
            "isDirectory": p.is_dir(),
            "isFile": p.is_file(),
            "permissions": oct(st.st_mode)[-3:],
        }

    async def download(self, sandbox_path: str, filename: Optional[str]) -> DownloadResult:
        src = self._resolve(sandbox_path, operation="download_file")
        if not src.is_file():
            raise FileNotFoundError(f"File not found in workspace: {sandbox_path}")
        data = src.read_bytes()
        dest_dir = local_base_dir()
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / (filename or src.name)
        if dest.resolve() != src.resolve():
            dest.write_bytes(data)
        return DownloadResult(sandbox_path=sandbox_path, local_path=str(dest.resolve()), size_bytes=len(data))

    async def upload(self, local_path: Path | str, sandbox_path: str) -> UploadResult:
        from .paths import assert_resolved_path_under

        p = Path(local_path)
        if not p.is_absolute():
            p = local_base_dir() / p
        p = assert_resolved_path_under(p, local_base_dir())
        if not p.exists():
            raise FileNotFoundError(f"Local file not found: {p}")
        dest = self._resolve(sandbox_path, operation="upload_file")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(p.read_bytes())
        return UploadResult(local_path=str(p), sandbox_path=self._public(dest))


# --------------------------------------------------------------------------- #
# Remote (opensandbox) backend                                                 #
# --------------------------------------------------------------------------- #


class RemoteSandboxBackend(FilesystemBackend):
    """Adapter over OpenSandboxExecutor's remote ``files.*`` API.

    The remote sandbox is already per-thread (one cached interpreter per
    thread_id), so no host subdir logic is applied — only the legacy
    ``/tmp`` → ``/workspace`` normalization the executor already used.
    """

    def __init__(self, executor: Any, thread_id: Optional[str] = None) -> None:
        self.executor = executor
        self.thread_id = thread_id

    def _norm(self, path: str) -> str:
        from cuga.backend.cuga_graph.nodes.cuga_lite.executors.opensandbox.opensandbox_executor import (
            _normalize_sandbox_path,
        )

        return _normalize_sandbox_path(path)

    async def _interp(self):
        return await self.executor._get_or_create_interpreter(self.thread_id)

    async def read_text(self, path: str, *, operation: str) -> str:
        interp = await self._interp()
        return await interp.sandbox.files.read_file(self._norm(path))

    async def write_text(self, path: str, content: str, *, operation: str) -> str:
        sp = self._norm(path)
        interp = await self._interp()
        await interp.sandbox.commands.run(f"mkdir -p {str(Path(sp).parent)}")
        await interp.sandbox.files.write_file(sp, content)
        return sp

    async def exists(self, path: str, *, operation: str) -> bool:
        sp = self._norm(path)
        interp = await self._interp()
        try:
            info = await interp.sandbox.files.get_file_info([sp])
            return bool(info) and sp in info
        except Exception:
            return False

    async def mkdir(self, path: str) -> str:
        sp = self._norm(path)
        interp = await self._interp()
        await interp.sandbox.commands.run(f"mkdir -p {sp}")
        return sp

    async def move(self, source: str, destination: str) -> tuple[str, str]:
        src, dst = self._norm(source), self._norm(destination)
        if await self.exists(dst, operation="move_file"):
            raise ValueError(f"Destination already exists: {destination}")
        interp = await self._interp()
        await interp.sandbox.commands.run(f"mkdir -p {str(Path(dst).parent)} && mv {src} {dst}")
        return src, dst

    async def list_dir(self, path: str, pattern: str) -> ListFilesResult:
        from opensandbox.models.filesystem import SearchEntry  # type: ignore[import]

        sp = self._norm(path)
        interp = await self._interp()
        entries_raw = await interp.sandbox.files.search(SearchEntry(path=sp, pattern=pattern))
        entries = [
            FileEntry(
                name=Path(e.path).name,
                path=e.path,
                is_dir=(e.size == 0 and str(oct(e.mode)).startswith("0o7")),
                size_bytes=e.size,
            )
            for e in entries_raw
        ]
        return ListFilesResult(sandbox_path=sp, entries=entries)

    async def search(self, path: str, pattern: str, exclude: List[str]) -> List[str]:
        result = await self.list_dir(path, pattern if pattern else "*")
        return [e.path for e in result.entries]

    async def stat(self, path: str) -> dict:
        sp = self._norm(path)
        interp = await self._interp()
        info = await interp.sandbox.files.get_file_info([sp])
        meta = (info or {}).get(sp, {})
        return {"path": sp, **(meta if isinstance(meta, dict) else {"info": str(meta)})}

    async def download(self, sandbox_path: str, filename: Optional[str]) -> DownloadResult:
        sp = self._norm(sandbox_path)
        interp = await self._interp()
        check = await interp.sandbox.files.get_file_info([sp])
        if not check or sp not in check:
            raise FileNotFoundError(f"File not found in sandbox: {sp}")
        data: bytes = await interp.sandbox.files.read_bytes(sp)
        dest_dir = local_base_dir()
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / (filename or Path(sp).name)
        dest.write_bytes(data)
        logger.info(f"[RemoteSandboxBackend] Downloaded {sp} → {dest} ({len(data)} bytes)")
        return DownloadResult(sandbox_path=sp, local_path=str(dest), size_bytes=len(data))

    async def upload(self, local_path: Path | str, sandbox_path: str) -> UploadResult:
        from opensandbox.models import WriteEntry  # type: ignore[import]

        from .paths import local_base_dir, read_bytes_under

        sp = self._norm(sandbox_path)
        base = local_base_dir()
        try:
            payload = read_bytes_under(Path(local_path), base)
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"Local file not found: {local_path}") from exc
        interp = await self._interp()
        await interp.sandbox.files.write_files([WriteEntry(path=sp, data=payload)])
        logger.info(f"[RemoteSandboxBackend] Uploaded {local_path} → {sp}")
        return UploadResult(local_path=str(local_path), sandbox_path=sp)


__all__ = [
    "FilesystemBackend",
    "HostWorkspaceBackend",
    "RemoteSandboxBackend",
    "VIRTUAL_WORKSPACE_ROOT",
]
