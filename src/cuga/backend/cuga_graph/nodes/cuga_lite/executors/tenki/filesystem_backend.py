"""Filesystem adapter for a Tenki conversation sandbox."""

from __future__ import annotations

import fnmatch
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import List, Optional

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.filesystem.backends import (
    FilesystemBackend,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.filesystem.models import (
    DownloadResult,
    FileEntry,
    ListFilesResult,
    UploadResult,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.filesystem.paths import (
    VIRTUAL_WORKSPACE_ROOT,
    local_base_dir,
    read_bytes_under,
)

from .tenki_executor import REMOTE_WORKSPACE_ROOT, TenkiSandboxExecutor


class TenkiRemoteSandboxBackend(FilesystemBackend):
    def __init__(self, executor: TenkiSandboxExecutor, thread_id: Optional[str] = None) -> None:
        self.executor = executor
        self.thread_id = thread_id

    def _remote(self, path: str) -> str:
        raw = (path or "").strip().replace("\\", "/")
        if not raw:
            raise ValueError("empty path")
        if ".." in PurePosixPath(raw).parts:
            raise ValueError("path must stay under /workspace")
        if raw == VIRTUAL_WORKSPACE_ROOT:
            tail = ""
        elif raw.startswith(VIRTUAL_WORKSPACE_ROOT + "/"):
            tail = raw[len(VIRTUAL_WORKSPACE_ROOT) + 1 :]
        elif raw.startswith("/"):
            raise ValueError("path must stay under /workspace")
        else:
            tail = raw.lstrip("./")
        return REMOTE_WORKSPACE_ROOT + (f"/{tail}" if tail else "")

    def _public(self, remote: str) -> str:
        tail = remote.removeprefix(REMOTE_WORKSPACE_ROOT).lstrip("/")
        return VIRTUAL_WORKSPACE_ROOT + (f"/{tail}" if tail else "")

    async def _sandbox(self):
        return await self.executor._get_or_create_sandbox(self.thread_id)

    async def read_text(self, path: str, *, operation: str) -> str:
        return await (await self._sandbox()).fs.read_text(self._remote(path))

    async def write_text(self, path: str, content: str, *, operation: str) -> str:
        remote = self._remote(path)
        sandbox = await self._sandbox()
        await sandbox.fs.mkdir(str(PurePosixPath(remote).parent), recursive=True)
        await sandbox.fs.write_text(remote, content)
        return self._public(remote)

    async def exists(self, path: str, *, operation: str) -> bool:
        try:
            await (await self._sandbox()).fs.stat(self._remote(path))
            return True
        except Exception as exc:
            if exc.__class__.__name__ == "FileNotFoundError":
                return False
            raise

    async def mkdir(self, path: str) -> str:
        remote = self._remote(path)
        await (await self._sandbox()).fs.mkdir(remote, recursive=True)
        return self._public(remote)

    async def move(self, source: str, destination: str) -> tuple[str, str]:
        src, dst = self._remote(source), self._remote(destination)
        if await self.exists(destination, operation="move_file"):
            raise ValueError(f"Destination already exists: {destination}")
        sandbox = await self._sandbox()
        await sandbox.fs.mkdir(str(PurePosixPath(dst).parent), recursive=True)
        await sandbox.exec("mv", "--", src, dst, timeout=30, check=True)
        return self._public(src), self._public(dst)

    async def list_dir(self, path: str, pattern: str) -> ListFilesResult:
        remote = self._remote(path)
        entries = await (await self._sandbox()).fs.list(remote, include_hidden=True)
        selected = [
            FileEntry(
                name=entry.path,
                path=self._public(f"{remote}/{entry.path}"),
                is_dir=entry.is_dir,
                size_bytes=entry.size,
            )
            for entry in entries
            if fnmatch.fnmatch(entry.path, pattern)
        ]
        return ListFilesResult(sandbox_path=self._public(remote), entries=selected)

    async def search(self, path: str, pattern: str, exclude: List[str]) -> List[str]:
        root = self._remote(path)
        sandbox = await self._sandbox()
        results: list[str] = []

        async def walk(directory: str, relative: str = "") -> None:
            for entry in await sandbox.fs.list(directory, include_hidden=True):
                rel = f"{relative}/{entry.path}".lstrip("/")
                if any(fnmatch.fnmatch(rel, item) for item in exclude):
                    continue
                child = f"{directory}/{entry.path}"
                if entry.is_dir:
                    await walk(child, rel)
                elif PurePosixPath(rel).match(pattern):
                    results.append(self._public(child))

        await walk(root)
        return results

    async def stat(self, path: str) -> dict:
        remote = self._remote(path)
        info = await (await self._sandbox()).fs.stat(remote)
        modified = datetime.fromtimestamp(info.modified_unix_ns / 1_000_000_000).isoformat()
        return {
            "path": self._public(remote),
            "size": info.size,
            "modified": modified,
            "isDirectory": info.is_dir,
            "isFile": not info.is_dir,
            "permissions": oct(info.mode)[-3:],
        }

    async def download(self, sandbox_path: str, filename: Optional[str]) -> DownloadResult:
        remote = self._remote(sandbox_path)
        data = await (await self._sandbox()).fs.read_bytes(remote)
        destination = local_base_dir() / (filename or PurePosixPath(remote).name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        return DownloadResult(
            sandbox_path=self._public(remote), local_path=str(destination), size_bytes=len(data)
        )

    async def upload(self, local_path: Path | str, sandbox_path: str) -> UploadResult:
        remote = self._remote(sandbox_path)
        data = read_bytes_under(Path(local_path), local_base_dir())
        sandbox = await self._sandbox()
        await sandbox.fs.mkdir(str(PurePosixPath(remote).parent), recursive=True)
        await sandbox.fs.write_bytes(remote, data)
        return UploadResult(local_path=str(local_path), sandbox_path=self._public(remote))
