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

import os
import re
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, List, Optional

from langchain_core.tools import StructuredTool
from loguru import logger
from pydantic import BaseModel, Field

from cuga.config import settings
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


# ------------------------------------------------------------------ #
# Output schemas                                                       #
# ------------------------------------------------------------------ #


class DownloadResult(BaseModel):
    sandbox_path: str = Field(description="Original path inside the sandbox")
    local_path: str = Field(description="Absolute path of the downloaded file in cuga_workspace")
    size_bytes: int = Field(description="File size in bytes")


class UploadResult(BaseModel):
    local_path: str = Field(description="Source file path that was uploaded")
    sandbox_path: str = Field(description="Destination path inside the sandbox")


class FileEntry(BaseModel):
    name: str
    path: str
    is_dir: bool
    size_bytes: Optional[int] = None


class ListFilesResult(BaseModel):
    sandbox_path: str
    entries: List[FileEntry]


class ReadFileInput(BaseModel):
    sandbox_path: str = Field(description="Absolute path of the file inside the sandbox.")
    start_line: Optional[int] = Field(
        default=None,
        description="1-based first line to include (inclusive). Omit to start from line 1.",
    )
    end_line: Optional[int] = Field(
        default=None,
        description="1-based last line to include (inclusive). Omit to read through end of file.",
    )
    grep_pattern: Optional[str] = Field(
        default=None,
        description=(
            "Optional Python regex (re.search per line). Only lines that match are returned, "
            "e.g. 'error|warning' or 'TODO|FIXME'."
        ),
    )


# ------------------------------------------------------------------ #
# Executor                                                             #
# ------------------------------------------------------------------ #


class OpenSandboxExecutor(RemoteExecutor):
    """Provides a persistent sandbox with shell + filesystem tools."""

    # Interpreter cache: thread_id -> CodeInterpreter instance
    _sandboxes: dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    # Sandbox lifecycle                                                    #
    # ------------------------------------------------------------------ #

    def _get_connection_config(self):
        from opensandbox.config import ConnectionConfig  # type: ignore[import]

        domain = getattr(_cfg(), "opensandbox_domain", None) or "localhost:8080"
        return ConnectionConfig(domain=domain)

    async def _get_or_create_interpreter(self, thread_id: Optional[str] = None) -> Any:
        """Return a cached CodeInterpreter for thread_id, creating one if needed."""
        from opensandbox import Sandbox  # type: ignore[import]
        from code_interpreter import CodeInterpreter  # type: ignore[import]

        key = thread_id or "_default"
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

        if getattr(_cfg(), "enabled", False):
            await self._upload_skills_to_sandbox(interpreter)

        self._sandboxes[key] = interpreter
        logger.info(f"[OpenSandboxExecutor] Created interpreter for thread={key}")
        return interpreter

    async def get_interpreter_for_thread(self, thread_id: Optional[str] = None) -> Any:
        """Return the CodeInterpreter for thread_id, creating the sandbox if needed (workspace API, tools)."""
        return await self._get_or_create_interpreter(thread_id)

    async def _upload_skills_to_sandbox(self, interpreter: Any) -> None:
        """Upload discovered skill folders into /workspace/skills/ in the sandbox.

        Mirrors ``cuga.backend.skills.loader.discover_skills`` precedence:
        global legacy ``~/.config/cuga/skills`` → global ``~/.config/agents/skills`` →
        project legacy ``.cuga/skills`` / ``.cuga/.skills`` → project ``.agents/skills``.
        """
        from opensandbox.models import WriteEntry  # type: ignore[import]
        from cuga.backend.skills.loader import discover_skills

        cuga_folder = (os.getenv("CUGA_FOLDER") or "").strip() or (settings.policy.cuga_folder or "").strip()
        skill_entries = discover_skills(cuga_folder or None)

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
            await interpreter.sandbox.files.write_files(entries)
            logger.info(
                f"[OpenSandboxExecutor] Uploaded {len(entries)} skill files to "
                f"{VIRTUAL_WORKSPACE_ROOT}/skills/"
            )

    async def release_sandbox(self, thread_id: Optional[str] = None) -> None:
        """Kill and remove the cached interpreter/sandbox for a thread."""
        key = thread_id or "_default"
        interpreter = self._sandboxes.pop(key, None)
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
                sandbox_cmd = f"cd {VIRTUAL_WORKSPACE_ROOT} && source {VENV_PATH}/bin/activate && {cmd}"
                result = await interpreter.codes.run(sandbox_cmd, language=SupportedLanguage.BASH)
                stdout = "".join(line.text for line in result.logs.stdout)
                stderr = "".join(line.text for line in result.logs.stderr)
                output = stdout
                if stderr.strip():
                    output += f"\n[stderr]\n{stderr}"
                return output or "(command completed with no output)"
            except Exception as exc:
                return f"[run_command error] {exc}"

        return run_command

    def create_download_file_tool(self, thread_id: Optional[str] = None) -> Callable:
        executor = self

        async def download_file(sandbox_path: str, filename: Optional[str] = None) -> str:
            """Download a file from the sandbox into cuga_workspace/.

            Before calling this, ensure the file exists by checking run_command output or
            running: await run_command("ls -la <sandbox_path>")

            Args:
                sandbox_path: Absolute path of the file inside the sandbox (e.g. "/workspace/output.pptx")
                filename: Optional override for the saved filename. Defaults to the basename of sandbox_path.

            Returns:
                A message with the local path on success, or an error description if the file is not found.
            """
            try:
                interpreter = await executor._get_or_create_interpreter(thread_id)

                sandbox_path = _normalize_sandbox_path(sandbox_path)
                # Verify the file exists first to give a clear error
                check = await interpreter.sandbox.files.get_file_info([sandbox_path])
                if not check or sandbox_path not in check:
                    return (
                        f"[download_file error] File not found in sandbox: {sandbox_path}\n"
                        "Tip: use run_command('ls /workspace') to list available files."
                    )

                data: bytes = await interpreter.sandbox.files.read_bytes(sandbox_path)
                dest_name = filename or Path(sandbox_path).name
                local_path = _workspace_dir() / dest_name
                local_path.write_bytes(data)
                logger.info(f"[OpenSandbox] Downloaded {sandbox_path} → {local_path} ({len(data)} bytes)")
                result = DownloadResult(
                    sandbox_path=sandbox_path,
                    local_path=str(local_path),
                    size_bytes=len(data),
                )
                return f"File downloaded successfully: {result.model_dump_json()}"
            except Exception as exc:
                return (
                    f"[download_file error] {exc}\n"
                    "Tip: verify the file was created with run_command('ls -la /workspace/')."
                )

        return download_file

    def create_upload_file_tool(self, thread_id: Optional[str] = None) -> Callable:
        executor = self

        async def upload_file(local_path: str, sandbox_path: str) -> str:
            """Upload a local file from cuga_workspace into the sandbox.

            Args:
                local_path: Path to the local file (absolute, or relative to cuga_workspace).
                sandbox_path: Destination path inside the sandbox (e.g. "/workspace/data.csv").

            Returns:
                Confirmation message or error description.
            """
            try:
                from opensandbox.models import WriteEntry  # type: ignore[import]

                p = Path(local_path)
                if not p.is_absolute():
                    p = _workspace_dir() / p
                if not p.exists():
                    return f"[upload_file error] Local file not found: {p}"
                data = p.read_bytes()
                sandbox_path = _normalize_sandbox_path(sandbox_path)
                interpreter = await executor._get_or_create_interpreter(thread_id)
                await interpreter.sandbox.files.write_files([WriteEntry(path=sandbox_path, data=data)])
                logger.info(f"[OpenSandbox] Uploaded {p} → {sandbox_path}")
                result = UploadResult(local_path=str(p), sandbox_path=sandbox_path)
                return f"File uploaded successfully: {result.model_dump_json()}"
            except Exception as exc:
                return f"[upload_file error] {exc}"

        return upload_file

    def create_list_files_tool(self, thread_id: Optional[str] = None) -> Callable:
        executor = self

        async def list_files(sandbox_path: str = VIRTUAL_WORKSPACE_ROOT, pattern: str = "*") -> str:
            """List files and directories at a path inside the sandbox.

            Args:
                sandbox_path: Directory path inside the sandbox (default: /workspace).
                pattern: Glob pattern to filter results (default: "*" = all files).

            Returns:
                JSON list of files with name, path, size_bytes, or an error message.
            """
            try:
                from opensandbox.models.filesystem import SearchEntry  # type: ignore[import]

                sandbox_path = _normalize_sandbox_path(sandbox_path)
                interpreter = await executor._get_or_create_interpreter(thread_id)
                entries_raw = await interpreter.sandbox.files.search(
                    SearchEntry(path=sandbox_path, pattern=pattern)
                )
                entries = [
                    FileEntry(
                        name=Path(e.path).name,
                        path=e.path,
                        is_dir=(e.size == 0 and str(oct(e.mode)).startswith("0o7")),
                        size_bytes=e.size,
                    )
                    for e in entries_raw
                ]
                result = ListFilesResult(sandbox_path=sandbox_path, entries=entries)
                return result.model_dump_json()
            except Exception as exc:
                return f"[list_files error] {exc}"

        return list_files

    def create_read_file_tool(self, thread_id: Optional[str] = None) -> Callable:
        executor = self

        async def read_file(
            sandbox_path: str,
            start_line: Optional[int] = None,
            end_line: Optional[int] = None,
            grep_pattern: Optional[str] = None,
        ) -> str:
            """Read a text file from the sandbox and return its contents.

            Args:
                sandbox_path: Absolute path of the file inside the sandbox.
                start_line: 1-based first line (inclusive); omit for line 1.
                end_line: 1-based last line (inclusive); omit for end of file.
                grep_pattern: Optional regex; only lines matching re.search(pattern, line) are kept.

            Returns:
                File contents as a string, or an error message if not found.
            """
            try:
                sandbox_path = _normalize_sandbox_path(sandbox_path)
                interpreter = await executor._get_or_create_interpreter(thread_id)
                content = await interpreter.sandbox.files.read_file(sandbox_path)
            except Exception as exc:
                return f"[read_file error] {exc}"

            use_slice_or_grep = start_line is not None or end_line is not None or grep_pattern is not None
            if not use_slice_or_grep:
                return content

            lines = content.splitlines()
            n = len(lines)
            if n == 0:
                return "(empty file)"

            s = 1 if start_line is None else max(1, start_line)
            e = n if end_line is None else end_line
            if s > n:
                return f"[read_file] start_line {s} is past end of file ({n} lines)"
            e = max(s, min(e, n))

            try:
                rx = re.compile(grep_pattern) if grep_pattern else None
            except re.error as exc:
                return f"[read_file error] invalid grep_pattern regex: {exc}"

            out: list[str] = []
            for i in range(s - 1, e):
                lineno = i + 1
                line = lines[i]
                if rx is not None and not rx.search(line):
                    continue
                out.append(f"{lineno}|{line}" if rx is not None else line)

            if rx is not None and not out:
                return f"(no lines matched grep_pattern in lines {s}-{e})"

            return "\n".join(out) if out else ""

        return read_file

    def create_write_file_tool(self, thread_id: Optional[str] = None) -> Callable:
        executor = self

        async def write_file(sandbox_path: str, content: str) -> str:
            """Write a text string directly into a file inside the sandbox.

            Use this instead of printf/heredoc shell tricks when you need to place
            a script, config, or data file inside the sandbox. Parent directories
            are created automatically.

            Args:
                sandbox_path: Destination path inside the sandbox (e.g. "/workspace/script.js").
                content: Text content to write.

            Returns:
                Confirmation message or error description.
            """
            try:
                sandbox_path = _normalize_sandbox_path(sandbox_path)
                interpreter = await executor._get_or_create_interpreter(thread_id)
                parent = str(Path(sandbox_path).parent)
                await interpreter.sandbox.commands.run(f"mkdir -p {parent}")
                await interpreter.sandbox.files.write_file(sandbox_path, content)
                return f"File written: {sandbox_path} ({len(content)} chars)"
            except Exception as exc:
                return f"[write_file error] {exc}"

        return write_file

    def create_sandbox_tools(self, thread_id: Optional[str] = None) -> list[StructuredTool]:
        """Return all sandbox StructuredTools bound to thread_id.

        Returns run_command, write_file, download_file, upload_file, list_files, read_file.
        """
        run_command = self.create_run_command_tool(thread_id)
        write_file = self.create_write_file_tool(thread_id)
        download_file = self.create_download_file_tool(thread_id)
        upload_file = self.create_upload_file_tool(thread_id)
        list_files = self.create_list_files_tool(thread_id)
        read_file = self.create_read_file_tool(thread_id)

        return [
            StructuredTool.from_function(
                coroutine=run_command,
                name="run_command",
                description=(
                    "Run a shell command inside the sandbox and return its output. "
                    "Commands run from /workspace with the sandbox virtual environment activated. "
                    "Use uv only for Python package installs and inspection (`uv pip install ...`, `uv pip list`, `uv pip show ...`). "
                    "Never run `python -m ...` directly; use `uv run python -m ...`. Python scripts should run as `uv run /workspace/file.py`. "
                    "Node commands must start with plain `node ...`; npm commands must start with plain `npm ...`. "
                    "Never use `uv npm`, `uv run node`, or `uv run npm`."
                ),
            ),
            StructuredTool.from_function(
                coroutine=write_file,
                name="write_file",
                description=(
                    "Write text content directly into a file inside the sandbox. "
                    "Use this to create scripts, configs, or data files — much cleaner than printf/heredoc shell tricks. "
                    "Parent directories are created automatically."
                ),
            ),
            StructuredTool.from_function(
                coroutine=download_file,
                name="download_file",
                description=(
                    "Download a file created inside the sandbox to cuga_workspace/ so the user can access it. "
                    "Call after generating any output file (pptx, pdf, zip, csv, etc.)."
                ),
            ),
            StructuredTool.from_function(
                coroutine=upload_file,
                name="upload_file",
                description=(
                    "Upload a local file from cuga_workspace into the sandbox. "
                    "Use to make user-provided files available for processing inside the sandbox."
                ),
            ),
            StructuredTool.from_function(
                coroutine=list_files,
                name="list_files",
                description="List files and directories at a path inside the sandbox (/workspace by default).",
            ),
            StructuredTool.from_function(
                coroutine=read_file,
                name="read_file",
                description=(
                    "Read a text file from the sandbox. Optionally pass start_line and end_line (1-based, inclusive) "
                    "to read a slice, and/or grep_pattern (Python regex per line, e.g. 'word1|word2') to filter lines. "
                    "When grep_pattern is set, matching lines are prefixed with 'LINE|'. "
                    "Omit all options to read the full file."
                ),
                args_schema=ReadFileInput,
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
