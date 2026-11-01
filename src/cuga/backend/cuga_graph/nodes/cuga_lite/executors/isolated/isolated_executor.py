"""``python_backend = "subprocess"`` — every code block runs in its own sandboxed interpreter.

The in-process ``LocalExecutor`` runs generated code inside the agent's own
interpreter, so its safety rests on what the code is *allowed to say*: an import
allowlist, an AST scan for dunders and ``open``, a restricted ``__builtins__``. That
is easy to get around and narrow for honest code (no ``csv``, ``statistics``,
``itertools.batched``…), and a CPU-bound loop cannot be stopped — ``wait_for`` only
cancels at an ``await``.

Here the isolation is the process (free-code's model, applied to our tools):

- a fresh ``python -I`` per block (``rpc_client/cuga_worker.py``) with a scrubbed
  environment — no API keys, no host ``PYTHONPATH``;
- under an OS sandbox when one is available — Seatbelt (``sandbox-exec``) on macOS,
  bubblewrap on Linux: reads anywhere, writes only to a per-thread scratch
  directory (and the ``run_command`` workspace, see below), **no network**;
- when ``run_command`` runs on this host (local / native shell, or host filesystem
  tools), a block starts in that same per-thread workspace, so a file the code
  writes is the file a shell command reads, and the other way round; HOME, TMPDIR
  and caches stay in the private scratch directory;
- tools reach the host over inherited pipes (``tool_rpc``), so every call is
  budgeted, tracked and write-checked by the host, exactly like an in-process call;
- variables cross as JSON — the same rule the in-process path already applies
  (non-serializable values were never kept);
- a timeout kills the process tree, so even ``while True: pass`` ends.

With an OS sandbox active the import allowlist and AST checks are skipped
(``os_sandboxed``): the code can use the whole standard library and every
installed package, and the sandbox is what bounds it.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common.rpc_client import cuga_tools as wire
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common.tool_rpc import WORKER_PATH, run_process

#: Extra seconds the host waits past the block's own timeout before killing the process.
#: The worker stops its own block at ``timeout`` and reports what it kept; the kill is
#: the backstop for code that never yields (a CPU-bound loop).
HARD_KILL_GRACE_S = 5.0

_SEATBELT_POLICY = """(version 1)
(deny default)
(allow process-fork)
(allow process-exec)
(allow signal (target self))
(allow sysctl-read)
(allow mach-lookup)
(allow ipc-posix-shm)
(allow file-read*)
(allow file-ioctl)
(allow file-write*
    (subpath "{workdir}"){extra_writable}
    (literal "/dev/null")
    (literal "/dev/dtracehelper")
)
(deny network*)
"""


def _sandbox_kind() -> str:
    """``seatbelt`` / ``bwrap`` / ``none`` — what this host can wrap the worker in."""
    from cuga.config import settings

    wanted = str(getattr(getattr(settings, "execution", None), "subprocess_sandbox", None) or "auto").lower()
    if wanted in ("off", "none", "false"):
        return "none"
    if sys.platform == "darwin" and shutil.which("sandbox-exec"):
        return "seatbelt"
    if sys.platform.startswith("linux") and shutil.which("bwrap"):
        return "bwrap"
    if wanted in ("on", "required", "true"):
        raise RuntimeError(
            "execution.subprocess_sandbox requires an OS sandbox (sandbox-exec on macOS, bwrap on Linux) "
            "and none was found"
        )
    return "none"


def _workdir(thread_id: Optional[str]) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (thread_id or "default"))[:80]
    path = Path(tempfile.gettempdir()).resolve() / "cuga-python" / safe
    path.mkdir(parents=True, exist_ok=True)
    return path


def _shared_workspace(thread_id: Optional[str]) -> Optional[Path]:
    """The ``run_command`` workspace when the shell runs on this host, else ``None``.

    Local and native shells run commands in ``thread_workspace_root(thread_id)``; so
    do the host filesystem tools. A remote shell (opensandbox, e2b) has its own
    filesystem, which a host process cannot share.
    """
    from cuga.backend.cuga_graph.nodes.cuga_agent_core.policy.execution_policy import ExecutionRouter
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.filesystem.paths import thread_workspace_root
    from cuga.config import settings

    try:
        plan = ExecutionRouter.resolve(settings)
    except Exception as exc:
        logger.debug(f"[IsolatedPython] no execution plan ({exc}); using the private scratch dir")
        return None
    if plan.shell_backend not in ("local", "native") and plan.filesystem_backend != "host":
        return None
    path = thread_workspace_root(thread_id).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _sandbox_argv(kind: str, workdir: Path, workspace: Optional[Path] = None) -> List[str]:
    """Launcher prefix. ``workdir`` is the private scratch dir; ``workspace``, when given,
    is also writable and is where the block starts."""
    start = workspace or workdir
    if kind == "seatbelt":
        extra = f'\n    (subpath "{workspace}")' if workspace else ""
        policy = workdir / ".sandbox.sb"
        policy.write_text(_SEATBELT_POLICY.format(workdir=str(workdir), extra_writable=extra))
        return ["sandbox-exec", "-f", str(policy)]
    if kind == "bwrap":
        shared = ["--bind", str(workspace), str(workspace)] if workspace else []
        return [
            "bwrap",
            "--ro-bind", "/", "/",
            "--dev", "/dev",
            "--proc", "/proc",
            "--tmpfs", "/tmp",
            "--bind", str(workdir), str(workdir),
            *shared,
            "--unshare-net",
            "--unshare-pid",
            "--die-with-parent",
            "--chdir", str(start),
        ]  # fmt: skip
    return []


def _child_env(workdir: Path) -> Dict[str, str]:
    """Only what an interpreter needs: no credentials, no host PYTHONPATH."""
    keep = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "SYSTEMROOT")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env.update(
        {
            "HOME": str(workdir),
            "TMPDIR": str(workdir),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "MPLCONFIGDIR": str(workdir / ".mpl"),
        }
    )
    return env


def _split_context(context_locals: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any], List[str]]:
    """``(tools, encoded_variables, dropped)`` from the namespace the executor built."""
    import types

    tools: Dict[str, Any] = {}
    variables: Dict[str, Any] = {}
    dropped: List[str] = []
    for name, value in context_locals.items():
        if name.startswith("_") or isinstance(value, types.ModuleType):
            continue
        if callable(value) and not isinstance(value, type):
            tools[name] = value
            continue
        try:
            encoded = wire.encode(value)
            json.dumps(encoded)
        except (TypeError, ValueError):
            dropped.append(name)
            continue
        variables[name] = encoded
    return tools, variables, dropped


class IsolatedPythonExecutor:
    """Runs a wrapped code block in a sandboxed child interpreter."""

    def __init__(self) -> None:
        self._kind: Optional[str] = None

    @property
    def os_sandboxed(self) -> bool:
        """True when blocks run under Seatbelt / bubblewrap (the static checks can be skipped)."""
        if self._kind is None:
            self._kind = _sandbox_kind()
            logger.info(f"[IsolatedPython] OS sandbox: {self._kind}")
        return self._kind != "none"

    async def execute_for_cuga_lite(
        self,
        wrapped_code: str,
        context_locals: Dict[str, Any],
        state: Any = None,
        thread_id: Optional[str] = None,
        apps_list: Optional[List[str]] = None,
        timeout: Optional[float] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """Run ``wrapped_code`` (which defines ``_async_main``); return ``(output, new_locals)``.

        Never raises for a failure *in the code*: errors, ``exit()`` and timeouts come
        back as output text in the in-process executor's format, with the variables
        computed before them.
        """
        from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import BlockToolCallCounter

        from cuga.config import settings

        block_timeout = float(timeout or settings.advanced_features.sandbox_execution_timeout or 30)
        tools, variables, dropped = _split_context(context_locals)
        if dropped:
            logger.debug(f"[IsolatedPython] variables not sent (not JSON data): {dropped}")
        workdir = _workdir(thread_id)
        workspace = _shared_workspace(thread_id)
        sandboxed = self.os_sandboxed
        argv = [
            *_sandbox_argv(self._kind or "none", workdir, workspace),
            sys.executable, "-I", str(WORKER_PATH),
        ]  # fmt: skip
        job = json.dumps(
            {"code": wrapped_code, "variables": variables, "tools": sorted(tools), "timeout": block_timeout},
            default=str,
        ).encode("utf-8")

        chunks: List[str] = []
        done: Dict[str, Any] = {}

        def on_message(message: Dict[str, Any]) -> None:
            if message.get("op") == "stdout":
                chunks.append(str(message.get("data") or ""))
            elif message.get("op") == "done":
                done.update(message)

        BlockToolCallCounter.reset()
        killed = False
        stderr = ""
        returncode = 0
        try:
            _, stderr, returncode = await run_process(
                argv,
                env=_child_env(workdir),
                cwd=str(workspace or workdir),
                timeout=block_timeout + HARD_KILL_GRACE_S,
                tools=tools,
                stdin_data=job,
                on_message=on_message,
            )
        except TimeoutError:
            killed = True
        except FileNotFoundError as exc:
            if sandboxed:
                logger.warning(f"[IsolatedPython] sandbox launcher missing ({exc}); running unsandboxed")
                self._kind = "none"
            raise

        printed = "".join(chunks) + str(done.get("stdout") or "")
        new_locals = {k: wire.decode(v) for k, v in (done.get("variables") or {}).items()}
        return self._output(
            done, printed, killed=killed, stderr=stderr, returncode=returncode, timeout=block_timeout,
            code=wrapped_code, tools=sorted(tools), calls=BlockToolCallCounter.current_count(),
        ), new_locals  # fmt: skip

    @staticmethod
    def _output(
        done: Dict[str, Any],
        printed: str,
        *,
        killed: bool,
        stderr: str,
        returncode: int,
        timeout: float,
        code: str,
        tools: List[str],
        calls: int,
    ) -> str:
        from cuga.backend.cuga_graph.nodes.cuga_lite.executors.local.local_executor import LocalExecutor

        status = done.get("status")
        kept = sorted((done.get("variables") or {}).keys())
        if killed or status == "timeout":
            kept_note = (
                f"kept variables from this block: {', '.join(kept)} (available in the next block).\n"
                if kept and not killed
                else "no variables from this block were saved.\n"
            )
            text = (
                f"Error during execution: Execution timed out after {timeout:g} seconds"
                f"{' and the process was killed' if killed else ''}.\n"
                f"This code block started {calls} tool call(s) before it was stopped; {kept_note}"
                "Do NOT rerun the same code — it will time out again. Restructure instead: process a "
                "smaller batch of items per block and store partial progress in a variable.\n"
            )
            return text + (
                f"Partial stdout before the timeout:\n{printed}"
                if printed.strip()
                else "(no stdout was printed before the timeout)"
            )
        if not status:
            tail = (stderr or "").strip()[-2000:]
            return (
                f"Error during execution: the code's process exited unexpectedly (exit code {returncode}) "
                "before it finished, so no variables from this block were saved."
                + (f"\n{tail}" if tail else "")
                + (f"\nOutput before the exit:\n{printed}" if printed.strip() else "")
            )
        if status == "exit":
            reason = str((done.get("error") or {}).get("message") or "")
            note = "Block ended early: the code called exit()/quit() or raised SystemExit"
            note += f" ({reason}).\n" if reason else ".\n"
            note += (
                "This ended the block only — variables defined before it were kept and "
                "are available in the next block. Nothing after the exit ran.\n"
            )
            return f"{note}{printed}" if printed.strip() else f"{note}(no output printed)"
        if status == "error":
            error = done.get("error") or {}
            header = f"Error during execution: {error.get('repr') or error.get('type') or 'Error'}"
            text = f"{header}\n{error.get('text') or ''}".rstrip()
            if printed.strip():
                text = f"Output before error:\n{printed.rstrip()}\n\n{text}"
            if error.get("type") == "NameError" and error.get("name"):
                text += LocalExecutor._unknown_tool_correction(
                    NameError(error.get("message"), name=error["name"]), tools, code
                )
            return text
        skipped = done.get("skipped") or []
        if skipped:
            printed += (
                f"\n[Not kept as variables (not plain data): {', '.join(skipped)} — "
                "store lists/dicts/strings/numbers to reuse them in the next block.]"
            )
        return printed if printed.strip() else "<code ran, no output printed to stdout>"
