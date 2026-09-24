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
_MAX_SECRET_BYTE_LENGTH = 4096
_MAX_SECRET_COUNT = 64
_STDERR_RETENTION_LIMIT = _STDERR_BYTE_LIMIT + _MAX_SECRET_BYTE_LENGTH - 1


def build_process_environment(
    config: ACPProcessConfig,
    parent: Mapping[str, str] | None = None,
) -> dict[str, str]:
    source = os.environ if parent is None else parent
    names = dict.fromkeys((*_BASELINE_ENV, *config.env))
    return {name: source[name] for name in names if source.get(name)}


def _validated_secret_values(secret_values: list[str]) -> list[str]:
    secrets = [value for value in dict.fromkeys(secret_values) if value]
    if len(secrets) > _MAX_SECRET_COUNT:
        raise ValueError("too many forwarded ACP environment secrets")
    if any(len(value.encode("utf-8")) > _MAX_SECRET_BYTE_LENGTH for value in secrets):
        raise ValueError("forwarded ACP environment secret exceeds the safe size limit")
    return sorted(secrets, key=len, reverse=True)


def sanitize_stderr(data: bytes, secret_values: list[str], *, byte_limit: int = _STDERR_BYTE_LIMIT) -> str:
    """Remove bounded validated secrets and any prefix exposed by the output boundary."""

    secrets = _validated_secret_values(secret_values)
    encoded_secrets = [value.encode("utf-8") for value in secrets]
    sanitized = data
    while True:
        redacted = sanitized
        for secret in encoded_secrets:
            redacted = redacted.replace(secret, b"")
        if redacted == sanitized:
            break
        sanitized = redacted

    bounded = sanitized[:byte_limit].decode("utf-8", errors="ignore")
    while True:
        redacted_text = bounded
        for secret in secrets:
            redacted_text = redacted_text.replace(secret, "")
        if redacted_text == bounded:
            break
        bounded = redacted_text

    exposed_prefix = max(
        (
            prefix_length
            for secret in secrets
            for prefix_length in range(1, len(secret))
            if bounded.endswith(secret[:prefix_length])
        ),
        default=0,
    )
    return bounded[:-exposed_prefix] if exposed_prefix else bounded


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
    secrets = _validated_secret_values(secret_values)
    chunks = bytearray()
    while True:
        chunk = await stream.read(1024)
        if not chunk:
            break
        if len(chunks) < _STDERR_RETENTION_LIMIT:
            remaining = _STDERR_RETENTION_LIMIT - len(chunks)
            chunks.extend(chunk[:remaining])
    return sanitize_stderr(bytes(chunks), secrets)


async def _cleanup_lifecycle(
    *,
    connection: Any | None,
    session_id: str | None,
    process: Any | None,
    stderr_task: asyncio.Task[str] | None,
    grace: float,
    cancel_session: bool,
) -> None:
    """Attempt every teardown stage and defer cancellation until cleanup completes."""

    cancellation_seen = False

    async def attempt(operation: Any) -> None:
        nonlocal cancellation_seen
        try:
            await operation
        except asyncio.CancelledError:
            cancellation_seen = True
        except Exception:
            pass

    if cancel_session:
        await attempt(asyncio.wait_for(_cancel_session(connection, session_id), timeout=grace))

    close_cancelled_before = cancellation_seen
    await attempt(asyncio.wait_for(_close_connection(connection), timeout=grace))
    if cancellation_seen and not cancel_session and not close_cancelled_before:
        await attempt(asyncio.wait_for(_cancel_session(connection, session_id), timeout=grace))

    while process is not None:
        await attempt(_stop_and_reap(process, grace))
        if process.returncode is not None:
            break

    if stderr_task is not None:
        if not stderr_task.done():
            stderr_task.cancel()
        await attempt(stderr_task)

    if cancellation_seen:
        raise asyncio.CancelledError


async def _await_spawn_task(task: asyncio.Task[Any], grace: float) -> Any | None:
    """Cancel a pending spawn and acquire/clean a process returned in the cancellation race."""

    task.cancel()
    try:
        process = await asyncio.shield(task)
    except asyncio.CancelledError:
        if task.done() and not task.cancelled():
            try:
                return task.result()
            except BaseException:
                return None
        return None
    except BaseException:
        return None
    await _stop_and_reap(process, grace)
    return process


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
    cancel_session = False
    spawn_task = None
    spawn = process_factory or asyncio.create_subprocess_exec
    env = build_process_environment(config, environ)
    secret_values = _validated_secret_values([env[name] for name in config.env if name in env])
    loop = asyncio.get_running_loop()
    startup_deadline = loop.time() + config.startup_timeout

    def remaining_startup_time() -> float:
        remaining = startup_deadline - loop.time()
        if remaining <= 0:
            raise ACPStartupTimeoutError
        return remaining

    try:
        spawn_task = asyncio.create_task(
            _await_if_needed(
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
        )
        done, _ = await asyncio.wait({spawn_task}, timeout=remaining_startup_time())
        if not done:
            await _await_spawn_task(spawn_task, config.shutdown_grace_period)
            raise ACPStartupTimeoutError
        process = spawn_task.result()
        if process.returncode is not None:
            raise ChildProcessError("ACP subprocess exited during startup")
        stderr_task = asyncio.create_task(_drain_stderr(process.stderr, secret_values))

        if connection_factory is None:
            from acp import connect_to_agent

            connection_factory = connect_to_agent
        connection = await asyncio.wait_for(
            _await_if_needed(connection_factory(callbacks, process.stdin, process.stdout)),
            timeout=remaining_startup_time(),
        )

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
                initialize_and_create_session(), timeout=remaining_startup_time()
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
        cancel_session = True
        if process is None and spawn_task is not None and not spawn_task.done():
            process = await _await_spawn_task(spawn_task, config.shutdown_grace_period)
        raise
    finally:
        callbacks.closed = True
        cleanup_task = asyncio.create_task(
            _cleanup_lifecycle(
                connection=connection,
                session_id=session_id,
                process=process,
                stderr_task=stderr_task,
                grace=config.shutdown_grace_period,
                cancel_session=cancel_session,
            )
        )
        cancellation_seen = False
        while not cleanup_task.done():
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                cancellation_seen = True
        try:
            await cleanup_task
        except asyncio.CancelledError:
            cancellation_seen = True
        if cancellation_seen:
            raise asyncio.CancelledError
