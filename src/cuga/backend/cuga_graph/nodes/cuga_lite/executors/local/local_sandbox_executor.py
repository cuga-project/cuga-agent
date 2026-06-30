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
import shlex
import shutil
import sys
from pathlib import Path
from typing import Callable, Optional

from langchain_core.tools import StructuredTool
from loguru import logger

# Canonical workspace path logic now lives in the consolidated filesystem
# package. Re-exported here under the historical names for back-compat
# (external imports of ``local_thread_workspace_root`` / ``_resolve_workspace_path``).
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common.run_output import (
    format_run_command_output,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.filesystem.paths import (
    CUGA_WORKSPACE_DIRNAME,
    VIRTUAL_WORKSPACE_ROOT,
    local_base_dir as _local_base_dir,
    normalize_shell_command_paths,
    public_workspace_path as _public_workspace_path,
    resolve_workspace_path as _resolve_workspace_path,
    safe_thread_id as _safe_thread_id,
    skills_enabled as _skills_enabled,
    thread_workspace_root as local_thread_workspace_root,
)

__all__ = [
    "LocalSandboxExecutor",
    "local_thread_workspace_root",
    "_resolve_workspace_path",
    "_public_workspace_path",
    "_safe_thread_id",
    "_skills_enabled",
    "_local_base_dir",
    "CUGA_WORKSPACE_DIRNAME",
    "VIRTUAL_WORKSPACE_ROOT",
]


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
                logger.error(f"[LocalSandbox] python -m venv failed for {venv}")
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

    def _copy_skills_to_workspace(
        self,
        thread_id: Optional[str] = None,
        cuga_folder: Optional[str] = None,
        skills_enabled: Optional[bool] = None,
    ) -> None:
        """Copy discovered skill folders into the per-thread /workspace/skills directory."""
        from cuga.config import settings

        enabled = skills_enabled if skills_enabled is not None else getattr(settings.skills, "enabled", False)
        if not enabled:
            return
        try:
            from cuga.backend.skills.loader import discover_skills
        except Exception:
            return

        resolved_folder = (
            cuga_folder
            or (os.getenv("CUGA_FOLDER") or "").strip()
            or (getattr(settings.policy, "cuga_folder", None) or "").strip()
        )
        skill_entries = discover_skills(resolved_folder or None)

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
    ) -> tuple[str, str, int]:
        cmd = normalize_shell_command_paths(cmd)
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
            returncode = proc.returncode or 0
            if returncode != 0:
                stderr_text = (stderr_text + "\n" if stderr_text else "") + f"(exit code {returncode})"
            return stdout_text, stderr_text, returncode
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
                stdout, stderr, returncode = await executor._run_command(cmd, thread_id=thread_id)
                return format_run_command_output(stdout, stderr, failed=returncode != 0)
            except TimeoutError as exc:
                return f"[run_command error] {exc}"
            except Exception as exc:
                return f"[run_command error] {exc}"

        return run_command

    def create_sandbox_tools(
        self,
        thread_id: Optional[str] = None,
        cuga_folder: Optional[str] = None,
        skills_enabled: Optional[bool] = None,
    ) -> list[StructuredTool]:
        """Return the run_command StructuredTool for local (unsandboxed) execution.

        Filesystem tools (read/write/list/edit/...) are no longer produced
        here — they come from the consolidated ``filesystem`` package via
        ``create_filesystem_tools`` (see ``cuga_lite_graph``).
        """
        self._copy_skills_to_workspace(thread_id, cuga_folder=cuga_folder, skills_enabled=skills_enabled)
        return [
            StructuredTool.from_function(
                coroutine=self.create_run_command_tool(thread_id),
                name="run_command",
                description=(
                    "Run a shell command directly on the host and return stdout as a plain string "
                    "(appends `\\n[stderr]\\n...` on failure) — not a dict. "
                    "The working directory is the sandbox workspace — use relative paths for all files "
                    "(e.g. `node ./script.js`, `python ./script.py`, or `uv run --no-project ./script.py`). "
                    "Per-thread `.venv` is on PATH with `UV_NO_CONFIG=1`. "
                    "Install with `uv pip install ...`. Verify with `python -c \"import pkg; print('ok')\"` "
                    "or `uv pip show pkg` — not `python -m pip` or `pip show`. "
                    "Run with `python ./script.py` first; retry with `uv run --no-project ...` if that fails. "
                    "Write scripts with write_file before running them. "
                    "For uploaded/session files use `./uploads/...` in shell commands (or `/workspace/...` — "
                    "rewritten automatically); `read_file` accepts both. "
                    "Node commands: plain `node ...`; npm commands: plain `npm ...`. "
                    "Never use `uv npm`, `uv run node`, or `uv run npm`. "
                    "Skills are available at `./skills/<skill_name>/`."
                ),
            ),
        ]
