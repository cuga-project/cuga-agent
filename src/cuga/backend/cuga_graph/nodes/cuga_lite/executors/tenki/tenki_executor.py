"""Tenki-backed shell executor with one bounded microVM per conversation."""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
from typing import Any, Callable, Optional

from langchain_core.tools import StructuredTool
from loguru import logger

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common.run_output import (
    format_run_command_output,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.filesystem.paths import (
    normalize_shell_command_paths,
)
from cuga.config import settings

REMOTE_WORKSPACE_ROOT = "/home/tenki/workspace"


class TenkiSandboxExecutor:
    """Provide CUGA shell tools through the Tenki Python SDK."""

    _sandboxes: dict[str, Any] = {}
    _clients: dict[str, Any] = {}
    _locks: dict[str, asyncio.Lock] = {}
    _released: set[str] = set()

    def _key_lock(self, key: str) -> asyncio.Lock:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    def _new_client(self):
        from tenki_sandbox import AsyncClient

        return AsyncClient()

    async def _project_id(self, client: Any) -> str:
        configured = os.getenv("TENKI_PROJECT_ID", "").strip()
        if configured:
            return configured
        identity = await client.who_am_i()
        try:
            return next(project.id for workspace in identity.workspaces for project in workspace.projects)
        except StopIteration as exc:
            raise RuntimeError("Tenki account has no project; set TENKI_PROJECT_ID") from exc

    async def _finish_cleanup(self, awaitable: Any) -> bool:
        """Finish cleanup even if this task receives cancellation."""
        task = asyncio.create_task(awaitable)
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
        await task
        return cancelled

    async def _close_resources(self, sandbox: Any, client: Any) -> None:
        cancelled = False
        errors: list[Exception] = []
        if sandbox is not None:
            try:
                cancelled = await self._finish_cleanup(sandbox.close_if_open()) or cancelled
            except Exception as exc:
                logger.warning(f"Tenki sandbox termination failed: {exc}")
                errors.append(exc)
        if client is not None:
            try:
                cancelled = await self._finish_cleanup(client.close()) or cancelled
            except Exception as exc:
                logger.warning(f"Tenki client close failed: {exc}")
                errors.append(exc)
        if cancelled:
            raise asyncio.CancelledError
        if errors:
            raise RuntimeError("Tenki resource cleanup failed") from errors[0]

    async def _upload_skills(self, sandbox: Any, cuga_folder: Optional[str]) -> None:
        if not bool(getattr(settings.skills, "enabled", False)):
            return
        from cuga.backend.skills.loader import discover_skills

        resolved_folder = cuga_folder or (os.getenv("CUGA_FOLDER") or "").strip() or None
        for skill in discover_skills(resolved_folder):
            root = Path(skill.source).parent
            if not root.is_dir():
                continue
            upload_root = root.parent
            for local_path in sorted(root.rglob("*")):
                if not local_path.is_file() or local_path.suffix.lower() in {".pyc", ".xsd"}:
                    continue
                relative = local_path.relative_to(upload_root).as_posix()
                remote_path = f"{REMOTE_WORKSPACE_ROOT}/skills/{relative}"
                await sandbox.fs.mkdir(str(Path(remote_path).parent), recursive=True)
                await sandbox.fs.write_bytes(remote_path, local_path.read_bytes())

    async def _create_sandbox(self, key: str, cuga_folder: Optional[str] = None):
        client = self._new_client()
        digest = hashlib.sha256(key.encode()).hexdigest()[:12]
        sandbox = None
        try:
            # SDK 0.4.0 wait=True can lose the handle when wait_ready fails.
            # Keep the handle first, then make readiness failure-atomic here.
            sandbox = await client.create(
                name=f"cuga-{digest}",
                project_id=await self._project_id(client),
                wait=False,
                max_duration=max(1, int(settings.advanced_features.tenki_sandbox_max_duration)),
                idle_timeout_minutes=max(
                    1, int(settings.advanced_features.tenki_sandbox_idle_timeout_minutes)
                ),
                tags=["cuga-agent"],
            )
            await sandbox.wait_ready(timeout=180)
            await sandbox.fs.mkdir(REMOTE_WORKSPACE_ROOT, recursive=True)
            await self._upload_skills(sandbox, cuga_folder)
        except BaseException:
            try:
                await self._close_resources(sandbox, client)
            except asyncio.CancelledError:
                raise
            except Exception as cleanup_error:
                logger.warning(f"Tenki cleanup after setup failure failed: {cleanup_error}")
            raise
        self._clients[key] = client
        logger.info(f"[TenkiSandbox] Created session {sandbox.id} for thread={key}")
        return sandbox

    async def _get_or_create_sandbox(
        self, thread_id: Optional[str] = None, cuga_folder: Optional[str] = None
    ):
        key = thread_id or "_default"
        async with self._key_lock(key):
            if key in self._released:
                raise RuntimeError("Tenki sandbox for this conversation has been released")
            existing = self._sandboxes.get(key)
            if existing is not None:
                try:
                    await existing.exec("true", timeout=15, check=True)
                    return existing
                except Exception:
                    self._sandboxes.pop(key, None)
                    client = self._clients.pop(key, None)
                    try:
                        await self._close_resources(existing, client)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.warning(f"Tenki stale sandbox cleanup failed: {exc}")
                        raise
            sandbox = await self._create_sandbox(key, cuga_folder)
            self._sandboxes[key] = sandbox
            return sandbox

    async def release_sandbox(self, thread_id: Optional[str] = None) -> None:
        key = thread_id or "_default"
        self._released.add(key)
        async with self._key_lock(key):
            sandbox = self._sandboxes.pop(key, None)
            client = self._clients.pop(key, None)
            await self._close_resources(sandbox, client)

    async def release_all(self) -> None:
        """Terminate every sandbox retained by this process, including on cancellation."""
        cancelled = False
        for key in list(self._sandboxes.keys() | self._clients.keys()):
            try:
                await self.release_sandbox(key)
            except asyncio.CancelledError:
                cancelled = True
            except Exception as exc:
                logger.warning(f"Tenki shutdown cleanup failed for thread={key}: {exc}")
        if cancelled:
            raise asyncio.CancelledError

    def create_run_command_tool(self, thread_id: Optional[str] = None) -> Callable:
        async def run_command(cmd: str) -> str:
            try:
                sandbox = await self._get_or_create_sandbox(thread_id)
                result = await sandbox.exec(
                    "bash",
                    "-c",
                    normalize_shell_command_paths(cmd),
                    cwd=REMOTE_WORKSPACE_ROOT,
                    timeout=settings.advanced_features.tool_call_timeout,
                )
                failed = not result.ok
                output = format_run_command_output(result.stdout_text, result.stderr_text, failed=failed)
                if failed:
                    output += f"\n[exit code {result.exit_code}; reason: {result.reason or 'unknown'}]"
                return output
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
                    raise
                return f"[run_command error] {exc}"

        return run_command

    def create_sandbox_tools(
        self,
        thread_id: Optional[str] = None,
        cuga_folder: Optional[str] = None,
        skills_enabled: Optional[bool] = None,
    ) -> list[StructuredTool]:
        del cuga_folder, skills_enabled
        return [
            StructuredTool.from_function(
                coroutine=self.create_run_command_tool(thread_id),
                name="run_command",
                description=(
                    "Run a shell command inside a Tenki microVM. CUGA's /workspace paths and "
                    "filesystem tools share the same per-conversation workspace."
                ),
            )
        ]
