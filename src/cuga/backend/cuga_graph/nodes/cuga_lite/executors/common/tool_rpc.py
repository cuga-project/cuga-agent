"""Let a child process call the agent's tools — the host side of ``rpc_client/cuga_tools.py``.

Two features run code outside this process and still need the agent's tools:

- the ``subprocess`` Python backend (``executors/subprocess``): every
  ``execute_python`` block runs in its own sandboxed interpreter;
- ``run_command``: a bash command, and any script it starts, can call tools
  (``from cuga_tools import call`` / ``cuga-tool NAME '{...}'``) — free-code's
  "write a script and run it" with the agent's tools in reach.

The channel is a pair of inherited pipes, not a socket: nothing listens on a port, a
sandbox can deny the child all network, and only the process tree the host started
can reach the tools. Every call is served *in the task that started the child*, one
at a time, so the context-scoped machinery — tool-call budgets, the run tracker,
``execute_python``'s call recording, the pre-write check wrapped around commit
tools — applies exactly as it does to a native call.
"""

from __future__ import annotations

import asyncio
import contextlib
import difflib
import inspect
import json
import os
import shutil
import stat
import sys
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

from loguru import logger

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common.rpc_client import cuga_tools as wire

CLIENT_DIR = Path(__file__).resolve().parent / "rpc_client"
WORKER_PATH = CLIENT_DIR / "cuga_worker.py"

#: Tools a shell command started now may call back into. Set around a ``run_command``
#: call by ``tool_exec`` (native call) and by ``run_code`` (call from ``execute_python``).
SHELL_TOOLS: ContextVar[Optional[Dict[str, Callable[..., Any]]]] = ContextVar(
    "cuga_shell_tools", default=None
)

#: Largest single message (one tool result or one job's variables).
_MAX_LINE = 512 * 1024 * 1024

OnMessage = Callable[[Dict[str, Any]], None]


def bash_path() -> str:
    """``bash`` when installed (the shell the model expects), else ``/bin/sh``."""
    if Path("/bin/bash").exists():
        return "/bin/bash"
    return shutil.which("bash") or "/bin/sh"


_cli_dir: Optional[Path] = None


def cli_dir() -> Path:
    """A directory holding the ``cuga-tool`` command (created once per process)."""
    global _cli_dir
    if _cli_dir is None or not (_cli_dir / "cuga-tool").exists():
        import tempfile

        path = Path(tempfile.mkdtemp(prefix="cuga-tool-bin-"))
        script = path / "cuga-tool"
        script.write_text(
            f'#!/bin/sh\nexec {json.dumps(sys.executable)} {json.dumps(str(CLIENT_DIR / "cuga_tools.py"))} "$@"\n'
        )
        script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        _cli_dir = path
    return _cli_dir


def _reply_error(call_id: Any, message: str, kind: str = "ToolCallError") -> Dict[str, Any]:
    return {"id": call_id, "ok": False, "error": message, "kind": kind}


async def _dispatch(tools: Dict[str, Callable[..., Any]], message: Dict[str, Any]) -> Dict[str, Any]:
    from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import ToolCallBudgetExceeded

    call_id = message.get("id")
    op = message.get("op")
    if op == "list":
        names = sorted(n for n, fn in tools.items() if callable(fn) and not n.startswith("_"))
        return {"id": call_id, "ok": True, "result": names}
    if op != "call":
        return _reply_error(call_id, f"unknown operation {op!r}")

    name = str(message.get("tool") or "")
    fn = tools.get(name)
    if fn is None or not callable(fn) or name.startswith("_"):
        close = difflib.get_close_matches(name, [n for n in tools if not n.startswith("_")], n=3, cutoff=0.6)
        hint = f" Did you mean: {', '.join(close)}?" if close else ""
        return _reply_error(call_id, f"Unknown tool {name!r}.{hint}", kind="NameError")
    args = wire.decode(message.get("args") or [])
    kwargs = wire.decode(message.get("kwargs") or {})
    try:
        result = fn(*args, **kwargs)
        if inspect.isawaitable(result):
            result = await result
    except ToolCallBudgetExceeded as exc:
        return _reply_error(call_id, str(exc), kind="ToolCallBudgetExceeded")
    except TypeError as exc:
        return _reply_error(call_id, f"{name} rejected these arguments: {exc}", kind="TypeError")
    except Exception as exc:
        text = str(exc) or type(exc).__name__
        return _reply_error(call_id, text if name in text else f"{name} failed: {text}")
    return {"id": call_id, "ok": True, "result": wire.encode(result)}


class ToolChannel:
    """Two pipes a child inherits: requests (child → host) and replies (host → child)."""

    def __init__(self) -> None:
        self._req_r, self._req_w = os.pipe()
        self._rep_r, self._rep_w = os.pipe()
        self._child_ends_closed = False

    @property
    def child_fds(self) -> Tuple[int, int]:
        return (self._req_w, self._rep_r)

    def child_env(self) -> Dict[str, str]:
        return {wire.ENV_OUT: str(self._req_w), wire.ENV_IN: str(self._rep_r)}

    def close_child_ends(self) -> None:
        """After the child started: the host must not hold the child's ends, or EOF never comes."""
        if not self._child_ends_closed:
            self._child_ends_closed = True
            for fd in (self._req_w, self._rep_r):
                with contextlib.suppress(OSError):
                    os.close(fd)

    def close(self) -> None:
        self.close_child_ends()
        for fd in (self._req_r, self._rep_w):
            with contextlib.suppress(OSError):
                os.close(fd)

    async def serve(
        self, tools: Dict[str, Callable[..., Any]], on_message: Optional[OnMessage] = None
    ) -> None:
        """Answer calls until every process holding the request pipe has exited.

        Calls are served one at a time, in this task: budgets and trackers are
        context-scoped, and a call served from a fresh task would escape them.
        """
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader(limit=_MAX_LINE, loop=loop)
        read_pipe = os.fdopen(self._req_r, "rb", 0)
        read_transport, _ = await loop.connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader, loop=loop), read_pipe
        )
        write_pipe = os.fdopen(self._rep_w, "wb", 0)
        write_transport, write_protocol = await loop.connect_write_pipe(
            lambda: asyncio.streams.FlowControlMixin(loop=loop), write_pipe
        )
        writer = asyncio.StreamWriter(write_transport, write_protocol, None, loop)
        # The pipe objects own the fds now.
        self._req_r = self._rep_w = -1
        try:
            while True:
                line = await reader.readline()
                if not line:
                    return
                try:
                    message = json.loads(line)
                except ValueError:
                    logger.debug("tool channel: dropped a malformed message")
                    continue
                if message.get("op") in ("call", "list"):
                    reply = await _dispatch(tools, message)
                    try:
                        writer.write(wire.dumps(reply))
                        await writer.drain()
                    except (BrokenPipeError, ConnectionResetError):
                        return  # the caller is gone; its result has nowhere to go
                elif on_message is not None:
                    on_message(message)
        finally:
            read_transport.close()
            write_transport.close()


async def run_process(
    argv: Sequence[str],
    *,
    env: Optional[Dict[str, str]] = None,
    cwd: Optional[str] = None,
    timeout: float,
    tools: Optional[Dict[str, Callable[..., Any]]] = None,
    stdin_data: Optional[bytes] = None,
    on_message: Optional[OnMessage] = None,
) -> Tuple[str, str, int]:
    """Run ``argv``; while it runs, serve its tool calls when ``tools`` is given.

    Returns ``(stdout, stderr, returncode)``. Raises ``TimeoutError`` after killing
    the process tree when ``timeout`` passes.
    """
    channel: Optional[ToolChannel] = ToolChannel() if tools is not None else None
    child_env = dict(os.environ if env is None else env)
    pass_fds: Tuple[int, ...] = ()
    if channel is not None:
        child_env.update(channel.child_env())
        child_env["PYTHONPATH"] = os.pathsep.join(
            p for p in (str(CLIENT_DIR), child_env.get("PYTHONPATH", "")) if p
        )
        child_env["PATH"] = os.pathsep.join(p for p in (str(cli_dir()), child_env.get("PATH", "")) if p)
        pass_fds = channel.child_fds
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=child_env,
            stdin=asyncio.subprocess.PIPE if stdin_data is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            pass_fds=pass_fds,
            start_new_session=True,
        )
    except BaseException:
        if channel is not None:
            channel.close()
        raise
    serving: Optional[asyncio.Future] = None
    if channel is not None:
        channel.close_child_ends()
        serving = asyncio.ensure_future(channel.serve(tools or {}, on_message))
    try:
        communicate = asyncio.ensure_future(proc.communicate(stdin_data))
        try:
            stdout, stderr = await _wait_with_service(communicate, serving, timeout)
        except asyncio.TimeoutError:
            _kill_tree(proc)
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(communicate, 5)
            raise TimeoutError(f"Command timed out after {timeout:g}s")
        if serving is not None:
            # The child is gone; let the reader drain what it wrote last (a final
            # ``done`` message), then stop — a daemonized grandchild may hold the pipe.
            with contextlib.suppress(asyncio.TimeoutError, Exception):
                await asyncio.wait_for(asyncio.shield(serving), 2)
        return (
            stdout.decode(errors="replace"),
            stderr.decode(errors="replace"),
            proc.returncode or 0,
        )
    finally:
        if serving is not None and not serving.done():
            serving.cancel()
            with contextlib.suppress(BaseException):
                await serving
        if channel is not None:
            channel.close()


async def _wait_with_service(
    communicate: "asyncio.Future[Tuple[bytes, bytes]]",
    serving: Optional[asyncio.Future],
    timeout: float,
) -> Tuple[bytes, bytes]:
    """Wait for the process; a crash in the serving loop is logged, never fatal."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    pending = {communicate} | ({serving} if serving is not None else set())
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise asyncio.TimeoutError
        done, _ = await asyncio.wait(pending, timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
        if communicate in done:
            return communicate.result()
        if serving is not None and serving in done:
            exc = serving.exception()
            if exc is not None:
                logger.warning(f"tool channel stopped: {exc!r}")
            pending.discard(serving)
        if not done:
            raise asyncio.TimeoutError


def _kill_tree(proc: Any) -> None:
    import signal

    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(ProcessLookupError):
        proc.kill()
