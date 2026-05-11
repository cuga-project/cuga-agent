"""Local (unsandboxed) shell executor — runs commands directly on the host.

Provides the same tool interface as NativeSandboxExecutor but without macOS
sandbox-exec restrictions. Works on Linux, Windows, and macOS.

Each thread workspace gets its own ``.venv`` and ``UV_NO_CONFIG=1`` so
``uv pip install`` does not merge packages into the Cuga repo's ``pyproject.toml``
/ lockfile (avoids unsatisfiable resolution when skills request extra deps).

Enable via settings (automatic on non-macOS when sandbox_mode = "native"):
    [advanced_features]
    sandbox_mode = "local"
    enable_shell_tool = true

Commands run directly on the host with no filesystem isolation. Always pair
with a tool approval policy for run_command so the user can approve before
anything executes.
"""

from __future__ import annotations

import asyncio
import os
import re
import shlex
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Callable, Optional

from langchain_core.tools import StructuredTool
from loguru import logger

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.opensandbox.opensandbox_executor import (
    FileEntry,
    ListFilesResult,
    ReadFileInput,
)

VIRTUAL_WORKSPACE_ROOT = "/workspace"


def _local_base_dir() -> Path:
    return Path(tempfile.gettempdir()) / "cuga"


def _safe_thread_id(thread_id: Optional[str]) -> str:
    raw = (thread_id or "_default").strip() or "_default"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", raw)


def local_thread_workspace_root(thread_id: Optional[str]) -> Path:
    return _local_base_dir() / _safe_thread_id(thread_id) / "workspace"


def _resolve_workspace_path(
    sandbox_path: str,
    *,
    thread_id: Optional[str],
    operation: str = "access",
) -> Path:
    raw = (sandbox_path or "").strip()
    if not raw:
        raise ValueError("empty sandbox_path")
    normalized = os.path.normpath(raw.replace("\\", "/"))
    workspace_root = local_thread_workspace_root(thread_id).resolve()
    if normalized == VIRTUAL_WORKSPACE_ROOT:
        dest = workspace_root
    elif normalized.startswith(VIRTUAL_WORKSPACE_ROOT + "/"):
        dest = workspace_root / normalized[len(VIRTUAL_WORKSPACE_ROOT) :].lstrip("/")
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
    workspace_root = local_thread_workspace_root(thread_id).resolve()
    try:
        rel = host_path.resolve().relative_to(workspace_root)
    except ValueError:
        return str(host_path)
    rel_str = str(rel)
    return "." if rel_str == "." else f"./{rel_str}"


def _venv_python_or_none(venv: Path) -> Optional[Path]:
    win = venv / "Scripts" / "python.exe"
    if win.is_file():
        return win
    u = venv / "bin" / "python"
    if u.is_file():
        return u
    return None


class LocalSandboxExecutor:
    """Shell executor that runs commands directly on the host without sandbox isolation."""

    async def _ensure_workspace_venv(self, workspace_root: Path) -> Path:
        """Create ``<workspace>/.venv`` if missing (uv first, then ``python -m venv``).

        Uses ``UV_SUBPROJECT``-style isolation: callers set ``UV_NO_CONFIG`` when running
        commands so uv does not discover the parent Cuga ``pyproject.toml``.
        """
        venv = workspace_root / ".venv"
        if _venv_python_or_none(venv) is not None:
            return venv
        workspace_root.mkdir(parents=True, exist_ok=True)
        no_cfg = {**os.environ, "UV_NO_CONFIG": "1"}
        no_cfg.pop("UV_OFFLINE", None)
        proc = await asyncio.create_subprocess_exec(
            "uv",
            "venv",
            str(venv),
            cwd=str(workspace_root),
            env=no_cfg,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.warning(
                "[LocalSandbox] uv venv failed: %r, falling back to python -m venv",
                stderr.decode(errors="replace"),
            )
            proc2 = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "venv",
                str(venv),
                cwd=str(workspace_root),
                env=no_cfg,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc2.communicate()
            if proc2.returncode != 0:
                logger.error("[LocalSandbox] python -m venv failed for %s", venv)
        return venv

    def _command_env(self, workspace_root: Path, venv: Path) -> dict[str, str]:
        """Environment for subprocesses: isolated uv + npm caches + venv on PATH."""
        env: dict[str, str] = dict(os.environ)
        env.pop("UV_OFFLINE", None)
        wr = str(workspace_root.resolve())
        env["UV_NO_CONFIG"] = "1"
        env["HOME"] = wr
        env["TMPDIR"] = wr
        env["VIRTUAL_ENV"] = str(venv.resolve())
        env["XDG_CACHE_HOME"] = str((workspace_root / ".cache").resolve())
        env["UV_CACHE_DIR"] = str((workspace_root / ".uv-cache").resolve())
        env["NPM_CONFIG_CACHE"] = str((workspace_root / ".npm").resolve())
        env["npm_config_cache"] = env["NPM_CONFIG_CACHE"]
        env["npm_config_prefix"] = wr
        env["NPM_CONFIG_PREFIX"] = wr
        if sys.platform == "win32":
            bindir = str((venv / "Scripts").resolve())
        else:
            bindir = str((venv / "bin").resolve())
        env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
        return env

    def _copy_skills_to_workspace(self, thread_id: Optional[str] = None) -> None:
        """Copy discovered skill folders into the per-thread /workspace/skills directory."""
        from cuga.config import settings

        if not getattr(settings.skills, "enabled", False):
            return
        try:
            from cuga.backend.skills.loader import discover_skills
        except Exception:
            return

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
                dest = local_thread_workspace_root(thread_id) / "skills" / rel
                try:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(local_path, dest)
                    copied += 1
                except Exception as exc:
                    logger.warning(f"[LocalSandbox] Skipping skill file {local_path}: {exc}")

        if copied:
            logger.info(
                f"[LocalSandbox] Copied {copied} skill files to "
                f"{local_thread_workspace_root(thread_id) / 'skills'}"
            )

    async def _run_command(
        self, cmd: str, *, thread_id: Optional[str] = None, timeout: int = 120
    ) -> tuple[str, str]:
        workspace_root = local_thread_workspace_root(thread_id)
        workspace_root.mkdir(parents=True, exist_ok=True)
        venv = await self._ensure_workspace_venv(workspace_root)
        env = self._command_env(workspace_root, venv)

        if sys.platform == "win32":
            proc = await asyncio.create_subprocess_shell(
                cmd,
                cwd=str(workspace_root.resolve()),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        else:
            shell_cmd = f"cd {shlex.quote(str(workspace_root.resolve()))} && {cmd}"
            proc = await asyncio.create_subprocess_exec(
                "/bin/sh",
                "-c",
                shell_cmd,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            stdout_text = stdout.decode(errors="replace")
            stderr_text = stderr.decode(errors="replace")
            if proc.returncode != 0:
                stderr_text = (stderr_text + "\n" if stderr_text else "") + f"(exit code {proc.returncode})"
            return stdout_text, stderr_text
        except asyncio.TimeoutError:
            proc.kill()
            raise TimeoutError(f"Command timed out after {timeout}s")

    def create_run_command_tool(self, thread_id: Optional[str] = None) -> Callable:
        executor = self

        async def run_command(cmd: str) -> str:
            """Run a shell command directly on the host and return its output.

            Args:
                cmd: Shell command (e.g. "uv pip install pandas", "node script.js")
            """
            try:
                stdout, stderr = await executor._run_command(cmd, thread_id=thread_id)
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
            """Write text content into a file inside the workspace (/workspace).

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
            """Read a text file from the workspace (/workspace).

            Args:
                sandbox_path: Absolute path of the file inside the workspace.
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
            """List files and directories inside the workspace.

            Args:
                sandbox_path: Directory path relative to the workspace (default: ".").
                pattern: Glob pattern to filter results (default: "*").
            """
            try:
                p = _resolve_workspace_path(sandbox_path, thread_id=thread_id, operation="list_files")
                if p == local_thread_workspace_root(thread_id).resolve():
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
        """Return all sandbox StructuredTools for local (unsandboxed) execution."""
        self._copy_skills_to_workspace(thread_id)
        return [
            StructuredTool.from_function(
                coroutine=self.create_run_command_tool(thread_id),
                name="run_command",
                description=(
                    "Run a shell command directly on the host and return its output. "
                    "The working directory is the sandbox workspace — use relative paths for all files "
                    "(e.g. `node ./script.js`, `uv run ./script.py`). "
                    "A per-thread `.venv` is on PATH; `UV_NO_CONFIG=1` ensures `uv pip install` targets "
                    "that venv and not the Cuga project. "
                    "Use uv only for Python packages (`uv pip install ...`); never `python -m ...` — use `uv run python -m ...`. "
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
