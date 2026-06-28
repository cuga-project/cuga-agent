"""OpenSandbox executor — provides a persistent sandbox for shell command execution.

OpenSandbox is a remote sandbox service (https://github.com/alibaba/OpenSandbox).
Requires an OpenSandbox server running at OPEN_SANDBOX_DOMAIN (default: localhost:8080).

The executor does NOT run agent Python code — that runs locally with full tool access.
It provides sandbox tools injected into the agent's local execution context:

  - run_command(cmd)        — run a shell command inside the sandbox
  - download_file(path)     — copy a sandbox file into cuga_workspace/
  - upload_file(local, dest) — write a local file into the sandbox
  - list_files(path)        — list files/dirs at a sandbox path
  - read_file(path, …)      — read a text file (optional line range + regex filter)

Sandboxes are cached per thread_id so package installs and files persist across steps.

Enable via settings:
    [advanced_features]
    opensandbox_sandbox = true

    [skills]
    enabled = true   # required for uploading skill files into the sandbox workspace
    opensandbox_image = "opensandbox/code-interpreter:v1.0.2"
    opensandbox_entrypoint = "/opt/opensandbox/code-interpreter.sh"
    opensandbox_python_version = "3.11"
    opensandbox_domain = "localhost:8080"
    opensandbox_timeout_seconds = 600
"""

from __future__ import annotations

import asyncio
import os
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Optional

from langchain_core.tools import StructuredTool
from loguru import logger

from cuga.config import settings
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common.run_output import (
    format_run_command_output,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.filesystem.paths import (
    normalize_shell_command_paths,
)
from ..base_executor import RemoteExecutor


VIRTUAL_WORKSPACE_ROOT = "/workspace"
LEGACY_SANDBOX_ROOT = "/tmp"
VENV_PATH = "/tmp/.venv"


def _cfg():
    return settings.skills


def _normalize_sandbox_path(path: str) -> str:
    """Map legacy /tmp paths to the agent-facing /workspace root."""
    raw = (path or "").strip().replace("\\", "/")
    if not raw:
        raise ValueError("empty sandbox path")
    if raw == VIRTUAL_WORKSPACE_ROOT or raw.startswith(VIRTUAL_WORKSPACE_ROOT + "/"):
        return raw
    if raw == LEGACY_SANDBOX_ROOT or raw.startswith(LEGACY_SANDBOX_ROOT + "/"):
        suffix = raw[len(LEGACY_SANDBOX_ROOT) :].lstrip("/")
        return VIRTUAL_WORKSPACE_ROOT if not suffix else f"{VIRTUAL_WORKSPACE_ROOT}/{suffix}"
    if raw.startswith("/"):
        raise ValueError("sandbox path must be under /workspace")
    return f"{VIRTUAL_WORKSPACE_ROOT}/{raw.lstrip('/')}"


def _workspace_dir() -> Path:
    """Resolve the cuga_workspace directory (mirrors server.main logic)."""
    p = Path(os.getcwd()) / "cuga_workspace"
    p.mkdir(parents=True, exist_ok=True)
    return p


# Output schemas (FileEntry/ListFilesResult/ReadFileInput/Download/Upload)
# now live in the consolidated ``filesystem.models`` package. The remote
# storage adapter lives in ``filesystem.backends.RemoteSandboxBackend`` and
# reuses ``_normalize_sandbox_path`` / ``_get_or_create_interpreter`` below.


# ------------------------------------------------------------------ #
# Executor                                                             #
# ------------------------------------------------------------------ #


class OpenSandboxExecutor(RemoteExecutor):
    """Provides a persistent sandbox with shell + filesystem tools."""

    # Interpreter cache: thread_id -> CodeInterpreter instance
    _sandboxes: dict[str, Any] = {}
    # Per-thread skills config requested on the current invocation
    _skills_config: dict[str, tuple[Optional[str], Optional[bool]]] = {}
    # Skills config that was actually applied when the sandbox was created
    _active_skills_config: dict[str, tuple[Optional[str], Optional[bool]]] = {}
    # Per-key asyncio locks to prevent concurrent sandbox creation for the same thread
    _locks: dict[str, asyncio.Lock] = {}

    def _get_key_lock(self, key: str) -> asyncio.Lock:
        """Return (creating if necessary) the asyncio.Lock for key.

        Safe to call without holding any other lock: in asyncio's single-threaded
        model there is no preemption between the dict check and the assignment.
        """
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    # ------------------------------------------------------------------ #
    # Sandbox lifecycle                                                    #
    # ------------------------------------------------------------------ #

    def _get_connection_config(self):
        from opensandbox.config import ConnectionConfig  # type: ignore[import]

        domain = getattr(_cfg(), "opensandbox_domain", None) or "localhost:8080"
        return ConnectionConfig(domain=domain)

    async def _get_or_create_interpreter(self, thread_id: Optional[str] = None) -> Any:
        """Return a cached CodeInterpreter for thread_id, creating one if needed.

        A per-key asyncio.Lock prevents concurrent coroutines from creating
        duplicate sandboxes for the same thread_id.
        """
        from opensandbox import Sandbox  # type: ignore[import]
        from code_interpreter import CodeInterpreter  # type: ignore[import]

        key = thread_id or "_default"

        async with self._get_key_lock(key):
            existing = self._sandboxes.get(key)
            if existing is not None:
                try:
                    await existing.sandbox.commands.run("true")
                    return existing
                except Exception:
                    logger.debug(f"[OpenSandboxExecutor] Interpreter for thread={key} is dead, recreating")
                    self._sandboxes.pop(key, None)

            cfg = _cfg()
            image = getattr(cfg, "opensandbox_image", "opensandbox/code-interpreter:v1.0.2")
            timeout_s = int(getattr(cfg, "opensandbox_timeout_seconds", 600))
            python_version = getattr(cfg, "opensandbox_python_version", "3.11")
            entrypoint = getattr(cfg, "opensandbox_entrypoint", "/opt/opensandbox/code-interpreter.sh")
            conn = self._get_connection_config()

            sandbox = await Sandbox.create(
                image,
                entrypoint=[entrypoint],
                env={"PYTHON_VERSION": python_version},
                timeout=timedelta(seconds=timeout_s),
                connection_config=conn,
            )
            interpreter = await CodeInterpreter.create(sandbox)

            # Ensure the agent-facing workspace and shared Python venv exist.
            # Agents see /workspace; /tmp/.venv remains an internal implementation detail.
            await interpreter.sandbox.commands.run(
                f"mkdir -p {VIRTUAL_WORKSPACE_ROOT} && cd {VIRTUAL_WORKSPACE_ROOT} && "
                "(command -v uv >/dev/null 2>&1 || python -m pip install --quiet uv) && "
                f"uv venv {VENV_PATH}"
            )

            skill_cuga_folder, skill_enabled = self._skills_config.get(key, (None, None))
            enabled = skill_enabled if skill_enabled is not None else getattr(_cfg(), "enabled", False)
            if enabled:
                try:
                    await self._upload_skills_to_sandbox(interpreter, cuga_folder=skill_cuga_folder)
                except Exception as exc:
                    # Log and continue — the sandbox is still usable; skills companion
                    # files will not be available, but the agent can still run commands.
                    logger.warning(
                        f"[OpenSandboxExecutor] Skills upload failed for thread={key}: {exc}. "
                        "Sandbox is cached but skill companion files may be unavailable."
                    )

            # Cache the interpreter regardless of upload outcome to avoid leaking
            # orphaned remote containers on repeated failures.
            self._sandboxes[key] = interpreter
            self._active_skills_config[key] = (skill_cuga_folder, skill_enabled)
            logger.info(f"[OpenSandboxExecutor] Created interpreter for thread={key}")
            return interpreter

    async def get_interpreter_for_thread(self, thread_id: Optional[str] = None) -> Any:
        """Return the CodeInterpreter for thread_id, creating the sandbox if needed (workspace API, tools)."""
        return await self._get_or_create_interpreter(thread_id)

    async def _upload_skills_to_sandbox(self, interpreter: Any, cuga_folder: Optional[str] = None) -> None:
        """Upload discovered skill folders into /workspace/skills/ in the sandbox.

        Uses the same single root as ``discover_skills`` (``settings.skills.root``).
        """
        from opensandbox.models import WriteEntry  # type: ignore[import]
        from cuga.backend.skills.loader import discover_skills

        resolved_folder = (
            cuga_folder
            or (os.getenv("CUGA_FOLDER") or "").strip()
            or (settings.policy.cuga_folder or "").strip()
        )
        skill_entries = discover_skills(resolved_folder or None)

        entries_by_path: dict[str, WriteEntry] = {}
        for skill_entry in skill_entries:
            root = Path(skill_entry.source).parent
            if not root.is_dir():
                continue
            upload_root = root.parent
            for local_path in sorted(root.rglob("*")):
                if not local_path.is_file():
                    continue
                # Skip binary schema files — only upload text/code files
                suffix = local_path.suffix.lower()
                if suffix in {".xsd", ".pyc"}:
                    continue
                rel = local_path.relative_to(upload_root)
                sandbox_path = f"{VIRTUAL_WORKSPACE_ROOT}/skills/{rel}"
                try:
                    data = local_path.read_bytes()
                    entries_by_path[sandbox_path] = WriteEntry(path=sandbox_path, data=data)
                except Exception as exc:
                    logger.warning(f"[OpenSandbox] Skipping skill file {local_path}: {exc}")

        if entries_by_path:
            entries = list(entries_by_path.values())
            try:
                await interpreter.sandbox.files.write_files(entries)
                logger.info(
                    f"[OpenSandboxExecutor] Uploaded {len(entries)} skill files to "
                    f"{VIRTUAL_WORKSPACE_ROOT}/skills/"
                )
            except Exception as exc:
                logger.warning(f"[OpenSandbox] write_files failed ({len(entries)} skill files): {exc}")
                raise

    async def release_sandbox(self, thread_id: Optional[str] = None) -> None:
        """Kill and remove the cached interpreter/sandbox for a thread."""
        key = thread_id or "_default"
        interpreter = self._sandboxes.pop(key, None)
        self._active_skills_config.pop(key, None)
        self._skills_config.pop(key, None)
        self._locks.pop(key, None)
        if interpreter:
            try:
                await interpreter.sandbox.kill()
                await interpreter.sandbox.close()
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # Tool factories                                                       #
    # ------------------------------------------------------------------ #

    def create_run_command_tool(self, thread_id: Optional[str] = None) -> Callable:
        executor = self

        async def run_command(cmd: str) -> str:
            """Run a shell command inside the sandbox and return its output.

            Args:
                cmd: Shell command (e.g. "uv pip install python-pptx", "node script.js")
            """
            try:
                from code_interpreter import SupportedLanguage  # type: ignore[import]

                interpreter = await executor._get_or_create_interpreter(thread_id)
                cmd = normalize_shell_command_paths(cmd)
                sandbox_cmd = f"cd {VIRTUAL_WORKSPACE_ROOT} && source {VENV_PATH}/bin/activate && {cmd}"
                result = await interpreter.codes.run(sandbox_cmd, language=SupportedLanguage.BASH)
                stdout = "".join(line.text for line in result.logs.stdout)
                stderr = "".join(line.text for line in result.logs.stderr)
                exit_code = getattr(result, "exit_code", getattr(result, "return_code", None))
                failed = bool(getattr(result, "error", None)) or (exit_code is not None and exit_code != 0)
                return format_run_command_output(stdout, stderr, failed=failed)
            except Exception as exc:
                return f"[run_command error] {exc}"

        return run_command

    def create_sandbox_tools(
        self,
        thread_id: Optional[str] = None,
        cuga_folder: Optional[str] = None,
        skills_enabled: Optional[bool] = None,
    ) -> list[StructuredTool]:
        """Return the run_command StructuredTool bound to thread_id.

        Filesystem tools (read/write/list/edit/...) now come from the
        consolidated ``filesystem`` package via a ``RemoteSandboxBackend``
        (wired in ``cuga_lite_graph``); they are no longer produced here.
        """
        key = thread_id or "_default"
        requested = (cuga_folder, skills_enabled)
        active = self._active_skills_config.get(key)
        if key in self._sandboxes and active is not None and requested != active:
            logger.warning(
                f"[OpenSandboxExecutor] Skills config changed for a live sandbox (thread={key}); "
                "the new cuga_folder/skills_enabled will not take effect until release_sandbox() is called."
            )
        self._skills_config[key] = requested
        return [
            StructuredTool.from_function(
                coroutine=self.create_run_command_tool(thread_id),
                name="run_command",
                description=(
                    "Run a shell command inside the sandbox and return its output. "
                    "Commands run from /workspace with the sandbox virtual environment activated. "
                    "Install with `uv pip install ...`. Verify with `python -c \"import pkg; print('ok')\"` "
                    "or `uv pip show pkg` — not `python -m pip` or `pip show`. "
                    "Run with `python ./script.py` first; retry with `uv run --no-project ...` if that fails. "
                    "Uploaded files live under `./uploads/` (manifest `shell_path`); `/workspace/...` paths "
                    "from `read_file`/`list_files` are rewritten for shell automatically. "
                    "Node commands must start with plain `node ...`; npm commands must start with plain `npm ...`. "
                    "Never use `uv npm`, `uv run node`, or `uv run npm`."
                ),
            ),
        ]

    # ------------------------------------------------------------------ #
    # RemoteExecutor stubs (not used — Python runs locally)               #
    # ------------------------------------------------------------------ #

    async def execute_for_cuga_lite(
        self, wrapped_code, context_locals, state, thread_id=None, apps_list=None
    ):
        raise NotImplementedError(
            "OpenSandboxExecutor runs Python locally — only sandbox tools go to the sandbox"
        )

    async def execute_for_code_agent(self, wrapped_code, state, thread_id=None):
        raise NotImplementedError(
            "OpenSandboxExecutor runs Python locally — only sandbox tools go to the sandbox"
        )
