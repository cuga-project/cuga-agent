"""Owned subprocess, ACP connection, and session lifecycle."""

from __future__ import annotations

import asyncio
import inspect
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Mapping

from .callbacks import ACPClientCallbacks
from .config import ACPProcessConfig

_BASELINE_ENV = ("PATH", "HOME", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR")
_STDERR_BYTE_LIMIT = 8192


def build_process_environment(
    config: ACPProcessConfig,
    parent: Mapping[str, str] | None = None,
) -> dict[str, str]:
    source = os.environ if parent is None else parent
    names = dict.fromkeys((*_BASELINE_ENV, *config.env))
    return {name: source[name] for name in names if source.get(name)}


def sanitize_stderr(data: bytes, secret_values: list[str], *, byte_limit: int = _STDERR_BYTE_LIMIT) -> str:
    bounded = data[:byte_limit].decode("utf-8", errors="replace")
    for value in sorted((value for value in secret_values if value), key=len, reverse=True):
        bounded = bounded.replace(value, "[REDACTED]")
    encoded = bounded.encode("utf-8")
    if len(encoded) > byte_limit:
        bounded = encoded[:byte_limit].decode("utf-8", errors="ignore")
    return bounded


class ACPStartupTimeoutError(TimeoutError):
    """ACP initialization or session creation exceeded its configured bound."""


@dataclass(frozen=True)
class ACPProcessSession:
    connection: Any
    process: Any
    session_id: str


async def _await_if_needed(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def _wait_bounded(process: Any, timeout: float) -> bool:
    if process.returncode is not None:
        await process.wait()
        return True
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout)
        return True
    except TimeoutError:
        return False


async def _cancel_session(connection: Any, session_id: str | None) -> None:
    if connection is None or session_id is None:
        return
    try:
        await _await_if_needed(connection.cancel(session_id))
    except Exception:
        pass


async def _close_connection(connection: Any | None) -> None:
    if connection is None:
        return
    try:
        await _await_if_needed(connection.close())
    except Exception:
        pass


async def _signal_process(process: Any, signal: str) -> None:
    try:
        getattr(process, signal)()
    except ProcessLookupError:
        pass


async def _stop_and_reap(process: Any | None, grace: float) -> None:
    if process is None:
        return
    if await _wait_bounded(process, grace):
        return
    await _signal_process(process, "terminate")
    if await _wait_bounded(process, grace):
        return
    await _signal_process(process, "kill")
    await process.wait()


async def _drain_stderr(stream: Any, secret_values: list[str]) -> str:
    chunks = bytearray()
    while True:
        chunk = await stream.read(1024)
        if not chunk:
            break
        if len(chunks) < _STDERR_BYTE_LIMIT:
            remaining = _STDERR_BYTE_LIMIT - len(chunks)
            chunks.extend(chunk[:remaining])
    return sanitize_stderr(bytes(chunks), secret_values)


@asynccontextmanager
async def open_acp_process_session(
    config: ACPProcessConfig,
    callbacks: ACPClientCallbacks,
    *,
    process_factory: Callable[..., Any] | None = None,
    connection_factory: Callable[..., Any] | None = None,
    environ: Mapping[str, str] | None = None,
) -> AsyncIterator[ACPProcessSession]:
    """Spawn, initialize, create one session, then always close and reap it."""

    process = None
    connection = None
    session_id = None
    stderr_task = None
    spawn = process_factory or asyncio.create_subprocess_exec
    env = build_process_environment(config, environ)
    secret_values = [env[name] for name in config.env if name in env]
    try:
        process = await _await_if_needed(
            spawn(
                config.command,
                *config.args,
                cwd=str(config.cwd) if config.cwd is not None else None,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        )
        if process.returncode is not None:
            raise ChildProcessError("ACP subprocess exited during startup")
        stderr_task = asyncio.create_task(_drain_stderr(process.stderr, secret_values))

        if connection_factory is None:
            from acp import connect_to_agent

            connection_factory = connect_to_agent
        connection = connection_factory(callbacks, process.stdin, process.stdout)

        from acp import PROTOCOL_VERSION
        from acp.schema import ClientCapabilities, Implementation

        async def initialize_and_create_session() -> str:
            await _await_if_needed(
                connection.initialize(
                    PROTOCOL_VERSION,
                    ClientCapabilities(),
                    Implementation(name="cuga", title="CUGA", version="1"),
                )
            )
            response = await connection.new_session(
                cwd=str(config.cwd or os.getcwd()),
                mcp_servers=[],
            )
            return response.session_id

        try:
            session_id = await asyncio.wait_for(
                initialize_and_create_session(), timeout=config.startup_timeout
            )
        except TimeoutError as exc:
            raise ACPStartupTimeoutError from exc
        except Exception as exc:
            if process.returncode is not None:
                raise ChildProcessError("ACP subprocess exited during startup") from exc
            raise
        callbacks.bind_session(session_id)
        yield ACPProcessSession(connection=connection, process=process, session_id=session_id)
    except BaseException:
        callbacks.cancelled = True
        cancel_task = asyncio.create_task(_cancel_session(connection, session_id))
        try:
            await asyncio.wait_for(asyncio.shield(cancel_task), timeout=config.shutdown_grace_period)
        except TimeoutError:
            cancel_task.cancel()
        raise
    finally:
        callbacks.closed = True
        try:
            await asyncio.wait_for(_close_connection(connection), timeout=config.shutdown_grace_period)
        except TimeoutError:
            pass
        cleanup_task = asyncio.create_task(_stop_and_reap(process, config.shutdown_grace_period))
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            await cleanup_task
            raise
        if stderr_task is not None:
            if not stderr_task.done():
                stderr_task.cancel()
            try:
                await stderr_task
            except (Exception, asyncio.CancelledError):
                pass
