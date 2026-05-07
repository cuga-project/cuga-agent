"""Native macOS sandbox-exec executor — no Docker required.

Uses Apple's Seatbelt (sandbox-exec) to run shell commands in a restricted
environment with write access confined to /private/tmp. Agent-facing filesystem
tools expose a clean /workspace root, mapped internally to a per-thread
/private/tmp/<thread_id>/workspace directory.

Enable via settings:
    [advanced_features]
    sandbox_mode = "native"
    enable_shell_tool = true

Requires macOS. Raises RuntimeError on other platforms.
"""

from __future__ import annotations

import asyncio
import os
import re
import shlex
import shutil
import sys
from pathlib import Path
from typing import Callable, Optional

from langchain_core.tools import StructuredTool
from loguru import logger

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.opensandbox.opensandbox_executor import (
    FileEntry,
    ListFilesResult,
    ReadFileInput,
)

SANDBOX_ROOT = "/tmp"
PRIVATE_TMP = "/private/tmp"
VIRTUAL_WORKSPACE_ROOT = "/workspace"
VENV_PATH = "/tmp/.venv"
POLICY_PATH = "/tmp/.cuga_sandbox.sb"


def _safe_thread_id(thread_id: Optional[str]) -> str:
    """Return a filesystem-safe thread id segment for native workspace isolation."""
    raw = (thread_id or "_default").strip() or "_default"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", raw)


def native_thread_workspace_root(thread_id: Optional[str]) -> Path:
    """Physical host root for a native conversation workspace.

    The agent-facing root is always ``/workspace``. Internally it maps to
    ``/tmp/<thread_id>/workspace`` (``/private/tmp/<thread_id>/workspace`` on macOS).
    """
    return Path(PRIVATE_TMP) / _safe_thread_id(thread_id) / "workspace"


def _resolve_workspace_path(
    sandbox_path: str,
    *,
    thread_id: Optional[str],
    operation: str = "access",
) -> Path:
    """Map agent-facing ``/workspace`` paths to the per-thread native workspace."""
    raw = (sandbox_path or "").strip()
    if not raw:
        raise ValueError("empty sandbox_path")
    normalized = os.path.normpath(raw.replace("\\", "/"))
    workspace_root = native_thread_workspace_root(thread_id).resolve()

    if normalized == VIRTUAL_WORKSPACE_ROOT:
        dest = workspace_root
    elif normalized.startswith(VIRTUAL_WORKSPACE_ROOT + "/"):
        dest = workspace_root / normalized[len(VIRTUAL_WORKSPACE_ROOT) :].lstrip("/")
    elif normalized.startswith(SANDBOX_ROOT + "/") or normalized == SANDBOX_ROOT:
        # Backward-compatible mapping for older prompts/tools that still pass /tmp paths.
        suffix = normalized[len(SANDBOX_ROOT) :].lstrip("/")
        dest = workspace_root / suffix if suffix else workspace_root
    elif normalized.startswith(PRIVATE_TMP + "/") or normalized == PRIVATE_TMP:
        # Backward-compatible mapping for physical /private/tmp paths.
        suffix = normalized[len(PRIVATE_TMP) :].lstrip("/")
        dest = workspace_root / suffix if suffix else workspace_root
    else:
        dest = workspace_root / normalized.lstrip("/")

    resolved = dest.resolve()
    try:
        resolved.relative_to(workspace_root)
    except ValueError as e:
        raise ValueError(f"{operation} path must stay under /workspace") from e
    return resolved


def _public_workspace_path(host_path: Path, *, thread_id: Optional[str]) -> str:
    """Return a relative path for a host path inside the thread workspace (e.g. './script.js')."""
    workspace_root = native_thread_workspace_root(thread_id).resolve()
    try:
        rel = host_path.resolve().relative_to(workspace_root)
    except ValueError:
        return str(host_path)
    rel_str = str(rel)
    return "." if rel_str == "." else f"./{rel_str}"


def _build_policy() -> str:
    """Generate a Seatbelt policy that allows reads broadly but writes only to /private/tmp."""
    return """(version 1)
(deny default)
(allow signal (target self))
(allow process*)
(allow mach*)
(allow ipc*)
(allow sysctl-read)
(allow system-socket)
(allow file-read*)
(allow file-write*
    (subpath "/private/tmp")
    (literal "/dev/null")
)
(allow network-outbound)
(allow network-inbound)
"""


class NativeSandboxExecutor:
    """Shell sandbox using macOS sandbox-exec; commands run from the per-thread workspace directory."""

    _venv_ready: bool = False
    _policy_written: bool = False

    def _check_platform(self) -> None:
        if sys.platform != "darwin":
            raise RuntimeError(
                f"NativeSandboxExecutor requires macOS (sandbox-exec); current platform is {sys.platform!r}. "
                "Use sandbox_mode = 'opensandbox' or 'e2b' instead."
            )

    def _ensure_policy(self) -> None:
        if not self._policy_written:
            Path(POLICY_PATH).write_text(_build_policy())
            self._policy_written = True

    async def _ensure_venv(self) -> None:
        if self._venv_ready:
            return
        if Path(VENV_PATH).exists():
            self._venv_ready = True
            return
        proc = await asyncio.create_subprocess_exec(
            "uv",
            "venv",
            VENV_PATH,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.warning(
                f"[NativeSandbox] uv venv failed: {stderr.decode()!r}, falling back to python -m venv"
            )
            proc2 = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "venv",
                VENV_PATH,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc2.communicate()
        self._venv_ready = True
        logger.info("[NativeSandbox] /tmp/.venv ready")

    def _copy_skills_to_workspace(self, thread_id: Optional[str] = None) -> None:
        """Copy discovered skill folders into the hidden per-thread /workspace/skills directory."""
        from cuga.config import settings

        if not getattr(settings.skills, "enabled", False):
            return
        try:
            from cuga.backend.skills.loader import discover_skills
        except Exception:
            return

        import os

        cuga_folder = (os.getenv("CUGA_FOLDER") or "").strip() or (
            getattr(settings.policy, "cuga_folder", None) or ""
        ).strip()
        skill_entries = discover_skills(cuga_folder or None)

        copied = 0
        for skill_entry in skill_entries:
            root = Path(skill_entry.source).parent
            if not root.is_dir():
                continue
            upload_root = root.parent
            for local_path in sorted(root.rglob("*")):
                if not local_path.is_file():
                    continue
                if local_path.suffix.lower() in {".xsd", ".pyc"}:
                    continue
                rel = local_path.relative_to(upload_root)
                dest = native_thread_workspace_root(thread_id) / "skills" / rel
                try:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(local_path, dest)
                    copied += 1
                except Exception as exc:
                    logger.warning(f"[NativeSandbox] Skipping skill file {local_path}: {exc}")

        if copied:
            logger.info(
                f"[NativeSandbox] Copied {copied} skill files to "
                f"{native_thread_workspace_root(thread_id) / 'skills'}"
            )

    async def _run_sandboxed(
        self, cmd: str, *, thread_id: Optional[str] = None, timeout: int = 120
    ) -> tuple[str, str]:
        self._check_platform()
        self._ensure_policy()
        # Native uses one shared /tmp/.venv. It is created lazily once, not per command.
        await self._ensure_venv()
        workspace_root = native_thread_workspace_root(thread_id)
        workspace_root.mkdir(parents=True, exist_ok=True)
        # /tmp is a symlink to /private/tmp on macOS; cd to the physical per-thread workspace so
        # relative paths like `./script.js` resolve correctly inside sandbox-exec.
        # npm under nvm stages installs in the global prefix (~/.nvm/...); redirect prefix here
        # so all arborist temp writes stay inside the Seatbelt-writable /private/tmp subtree.
        wr_q = shlex.quote(str(workspace_root))
        full_cmd = (
            f"cd {wr_q} && "
            f"export HOME={wr_q} "
            f"TMPDIR={wr_q} "
            f"XDG_CACHE_HOME={shlex.quote(str(workspace_root / '.cache'))} "
            f"UV_CACHE_DIR={shlex.quote(str(workspace_root / '.uv-cache'))} "
            f"NPM_CONFIG_CACHE={shlex.quote(str(workspace_root / '.npm'))} "
            f"npm_config_cache={shlex.quote(str(workspace_root / '.npm'))} "
            f"npm_config_prefix={wr_q} NPM_CONFIG_PREFIX={wr_q} && "
            "source /tmp/.venv/bin/activate && "
            f"{cmd}"
        )
        # CLI `cuga start` sets UV_OFFLINE=1; that would prevent uv from hitting PyPI when
        # UV_CACHE_DIR points at an empty per-thread cache.
        child_env = dict(os.environ)
        child_env.pop("UV_OFFLINE", None)
        proc = await asyncio.create_subprocess_exec(
            "sandbox-exec",
            "-f",
            POLICY_PATH,
            "/bin/sh",
            "-c",
            full_cmd,
            env=child_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            stdout_text = stdout.decode(errors="replace")
            stderr_text = stderr.decode(errors="replace")
            if proc.returncode != 0:
                stderr_text = (stderr_text + "\n" if stderr_text else "") + (
                    f"sandbox-exec exited with status {proc.returncode}"
                )
            return stdout_text, stderr_text
        except asyncio.TimeoutError:
            proc.kill()
            raise TimeoutError(f"Command timed out after {timeout}s")

    # ------------------------------------------------------------------ #
    # Tool factories (same interface as OpenSandboxExecutor)              #
    # ------------------------------------------------------------------ #

    def create_run_command_tool(self, thread_id: Optional[str] = None) -> Callable:
        executor = self

        async def run_command(cmd: str) -> str:
            """Run a shell command inside the native macOS sandbox and return its output.

            Args:
                cmd: Shell command (e.g. "uv pip install pandas", "node script.js", "npm install")
            """
            try:
                stdout, stderr = await executor._run_sandboxed(cmd, thread_id=thread_id)
                output = stdout
                if stderr.strip():
                    output += f"\n[stderr]\n{stderr}"
                return output or "(command completed with no output)"
            except TimeoutError as exc:
                return f"[run_command error] {exc}"
            except Exception as exc:
                return f"[run_command error] {exc}"

        return run_command

    def create_write_file_tool(self, thread_id: Optional[str] = None) -> Callable:
        async def write_file(sandbox_path: str, content: str) -> str:
            """Write text content into a file inside the sandbox workspace (/workspace).

            Args:
                sandbox_path: Destination path (e.g. "/workspace/script.js"). Must be under /workspace.
                content: Text content to write.
            """
            try:
                p = _resolve_workspace_path(sandbox_path, thread_id=thread_id, operation="write_file")
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")
                return (
                    f"File written: {_public_workspace_path(p, thread_id=thread_id)} ({len(content)} chars)"
                )
            except Exception as exc:
                return f"[write_file error] {exc}"

        return write_file

    def create_read_file_tool(self, thread_id: Optional[str] = None) -> Callable:
        async def read_file(
            sandbox_path: str,
            start_line: Optional[int] = None,
            end_line: Optional[int] = None,
            grep_pattern: Optional[str] = None,
        ) -> str:
            """Read a text file from the sandbox workspace (/workspace).

            Args:
                sandbox_path: Absolute path of the file inside the sandbox.
                start_line: 1-based first line (inclusive); omit for line 1.
                end_line: 1-based last line (inclusive); omit for end of file.
                grep_pattern: Optional regex; only matching lines are returned.
            """
            try:
                content = _resolve_workspace_path(
                    sandbox_path, thread_id=thread_id, operation="read_file"
                ).read_text(encoding="utf-8", errors="replace")
            except Exception as exc:
                return f"[read_file error] {exc}"

            if start_line is None and end_line is None and grep_pattern is None:
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
                return f"[read_file error] invalid grep_pattern: {exc}"
            out: list[str] = []
            for i in range(s - 1, e):
                line = lines[i]
                if rx is not None and not rx.search(line):
                    continue
                out.append(f"{i + 1}|{line}" if rx is not None else line)
            if rx is not None and not out:
                return f"(no lines matched grep_pattern in lines {s}-{e})"
            return "\n".join(out) if out else ""

        return read_file

    def create_list_files_tool(self, thread_id: Optional[str] = None) -> Callable:
        async def list_files(sandbox_path: str = ".", pattern: str = "*") -> str:
            """List files and directories inside the sandbox workspace.

            Args:
                sandbox_path: Directory path relative to the sandbox workspace (default: ".").
                pattern: Glob pattern to filter results (default: "*").
            """
            try:
                p = _resolve_workspace_path(sandbox_path, thread_id=thread_id, operation="list_files")
                if p == native_thread_workspace_root(thread_id).resolve():
                    p.mkdir(parents=True, exist_ok=True)
                if not p.exists():
                    return f"[list_files error] Path not found: {sandbox_path}"
                entries = []
                for child in sorted(p.glob(pattern)):
                    entries.append(
                        FileEntry(
                            name=child.name,
                            path=_public_workspace_path(child, thread_id=thread_id),
                            is_dir=child.is_dir(),
                            size_bytes=child.stat().st_size if child.is_file() else 0,
                        )
                    )
                return ListFilesResult(sandbox_path=sandbox_path, entries=entries).model_dump_json()
            except Exception as exc:
                return f"[list_files error] {exc}"

        return list_files

    def create_sandbox_tools(self, thread_id: Optional[str] = None) -> list[StructuredTool]:
        """Return all sandbox StructuredTools bound for native macOS sandbox-exec."""
        self._copy_skills_to_workspace(thread_id)
        return [
            StructuredTool.from_function(
                coroutine=self.create_run_command_tool(thread_id),
                name="run_command",
                description=(
                    "Run a shell command inside the native macOS sandbox and return its output. "
                    "The working directory is the sandbox workspace — use relative paths for all files "
                    "(e.g. `node ./script.js`, `uv run ./script.py`). "
                    "The sandbox virtual environment is pre-activated. "
                    "Use uv only for Python packages (`uv pip install ...`); never `python -m ...` directly — use `uv run python -m ...`. "
                    "Node commands: plain `node ...`; npm commands: plain `npm ...`. "
                    "Never use `uv npm`, `uv run node`, or `uv run npm`. "
                    "Skills are available at `./skills/<skill_name>/`."
                ),
            ),
            StructuredTool.from_function(
                coroutine=self.create_write_file_tool(thread_id),
                name="write_file",
                description=(
                    "Write text content into a file in the sandbox workspace. "
                    "Use relative paths (e.g. `./script.js`, `./output/report.pptx`). "
                    "Parent directories are created automatically."
                ),
            ),
            StructuredTool.from_function(
                coroutine=self.create_list_files_tool(thread_id),
                name="list_files",
                description=(
                    "List files and directories in the sandbox workspace. "
                    "Pass a relative path (default: `.` = workspace root)."
                ),
            ),
            StructuredTool.from_function(
                coroutine=self.create_read_file_tool(thread_id),
                name="read_file",
                description=(
                    "Read a text file from the sandbox workspace. "
                    "Pass a relative path (e.g. `./output.txt`). "
                    "Optionally pass start_line and end_line (1-based, inclusive) to read a slice, "
                    "and/or grep_pattern (Python regex per line) to filter lines. "
                    "When grep_pattern is set, matching lines are prefixed with 'LINE|'."
                ),
                args_schema=ReadFileInput,
            ),
        ]
