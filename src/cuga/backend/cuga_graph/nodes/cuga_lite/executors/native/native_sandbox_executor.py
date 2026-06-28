"""Native macOS sandbox-exec executor — no Docker required.

Uses Apple's Seatbelt (sandbox-exec) to run shell commands in a restricted
environment with write access confined to /private/tmp (Seatbelt-internal venv,
caches, etc.). The agent-facing workspace lives at ``<cwd>/cuga_workspace/``
— shared across threads when ``settings.skills.enabled`` is false, per-thread
``<cwd>/cuga_workspace/<safe_thread_id>/`` when true. This co-locates the
workspace with the filesystem MCP server's allowed root so MCP serves every
thread without a per-thread restart.

Enable via settings:
    [advanced_features]
    sandbox_mode = "native"
    enable_shell_tool = true

Requires macOS. Raises RuntimeError on other platforms.
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

# Canonical workspace path logic lives in the consolidated filesystem
# package. ``native_thread_workspace_root`` is re-exported under its
# historical name for back-compat (workspace_sandbox.py imports it).
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common.run_output import (
    format_run_command_output,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.filesystem.paths import (
    CUGA_WORKSPACE_DIRNAME,
    normalize_shell_command_paths,
    thread_workspace_root as native_thread_workspace_root,
)

VENV_PATH = "/tmp/.venv"
POLICY_PATH = "/tmp/.cuga_sandbox.sb"


def _build_policy() -> str:
    """Generate a Seatbelt policy that allows reads broadly but writes only to
    /private/tmp (internal venv/caches) and <cwd>/cuga_workspace (the
    user-facing per-thread workspace tree)."""
    workspace_parent = (Path(os.getcwd()) / CUGA_WORKSPACE_DIRNAME).resolve()
    return f"""(version 1)
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
    (subpath "{workspace_parent}")
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
        """Create /tmp/.venv if missing OR invalid, then mark ready.

        The shell template prepended to every sandboxed command sources
        ``/tmp/.venv/bin/activate`` — so the readiness flag must reflect
        the activate script's actual presence on disk, not just the dir.

        Failure modes this guards against:
          1. A stale ``/tmp/.venv/`` directory from a prior crashed run
             that has no ``bin/activate`` inside.
          2. ``uv venv`` and ``python -m venv`` both failing silently and
             the readiness flag being flipped True anyway, freezing the
             executor into a broken state for the rest of the process.
        """
        if self._venv_ready:
            return
        activate = Path(VENV_PATH) / "bin" / "activate"
        if activate.is_file():
            self._venv_ready = True
            return

        # Stale directory without an activate script — wipe and recreate.
        if Path(VENV_PATH).exists():
            logger.warning("[NativeSandbox] /tmp/.venv exists but is missing bin/activate; recreating")
            shutil.rmtree(VENV_PATH, ignore_errors=True)

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
            _, stderr2 = await proc2.communicate()
            if proc2.returncode != 0:
                logger.error(
                    f"[NativeSandbox] python -m venv ALSO failed: {stderr2.decode()!r}. "
                    "run_command will fail until /tmp/.venv is fixed."
                )

        # Only flip the flag once we've verified the file we actually
        # source in the sandbox prelude is present.
        if activate.is_file():
            self._venv_ready = True
            logger.info("[NativeSandbox] /tmp/.venv ready")
        else:
            logger.error(
                "[NativeSandbox] /tmp/.venv/bin/activate not found after creation; "
                "subsequent run_command calls will report `activate: No such file or directory`"
            )

    def _copy_skills_to_workspace(
        self,
        thread_id: Optional[str] = None,
        cuga_folder: Optional[str] = None,
        skills_enabled: Optional[bool] = None,
    ) -> None:
        """Copy discovered skill folders into the hidden per-thread /workspace/skills directory."""
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
    ) -> tuple[str, str, int]:
        self._check_platform()
        self._ensure_policy()
        cmd = normalize_shell_command_paths(cmd)
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
            f"UV_NO_CONFIG=1 "
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
            returncode = proc.returncode or 0
            if returncode != 0:
                stderr_text = (stderr_text + "\n" if stderr_text else "") + (
                    f"sandbox-exec exited with status {returncode}"
                )
            return stdout_text, stderr_text, returncode
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
                stdout, stderr, returncode = await executor._run_sandboxed(cmd, thread_id=thread_id)
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
        """Return the run_command StructuredTool for native macOS sandbox-exec.

        Filesystem tools (read/write/list/edit/...) now come from the
        consolidated ``filesystem`` package via ``create_filesystem_tools``
        (see ``cuga_lite_graph``); they are no longer produced here.
        """
        self._copy_skills_to_workspace(thread_id, cuga_folder=cuga_folder, skills_enabled=skills_enabled)
        return [
            StructuredTool.from_function(
                coroutine=self.create_run_command_tool(thread_id),
                name="run_command",
                description=(
                    "Run a shell command inside the native macOS sandbox and return stdout as a plain string "
                    "(appends `\\n[stderr]\\n...` on failure) — not a dict. "
                    "The working directory is the sandbox workspace — use relative paths for all files "
                    "(e.g. `node ./script.js`, `python ./script.py`, or `uv run --no-project ./script.py`). "
                    "The sandbox virtual environment is pre-activated. "
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
