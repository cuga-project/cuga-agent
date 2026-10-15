"""Owned subprocess, ACP connection, and session lifecycle."""

from __future__ import annotations

import asyncio
import inspect
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Iterable, Mapping

from .callbacks import ACPClientCallbacks
from .config import ACPProcessConfig

_BASELINE_ENV = ("PATH", "HOME", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR")
_STDERR_BYTE_LIMIT = 8192
_MAX_SECRET_BYTE_LENGTH = 4096
_MAX_SECRET_CHAR_LENGTH = _MAX_SECRET_BYTE_LENGTH
_MAX_SECRET_COUNT = 64
_MAX_PENDING_FACTORY_TASKS = 16
_STDERR_RETENTION_LIMIT = _STDERR_BYTE_LIMIT + _MAX_SECRET_BYTE_LENGTH - 1
_PENDING_FACTORY_TASKS: set[asyncio.Task[Any]] = set()
_PENDING_FACTORY_CLEANUPS: set[asyncio.Task[None]] = set()


def build_process_environment(
    config: ACPProcessConfig,
    parent: Mapping[str, str] | None = None,
) -> dict[str, str]:
    source = os.environ if parent is None else parent
    names = dict.fromkeys((*_BASELINE_ENV, *config.env))
    return {name: source[name] for name in names if source.get(name)}


def _validated_secret_values(secret_values: Iterable[str]) -> list[str]:
    secrets: list[str] = []
    seen: set[str] = set()
    candidate_count = 0
    for value in secret_values:
        candidate_count += 1
        if candidate_count > _MAX_SECRET_COUNT:
            raise ValueError("too many forwarded ACP environment secrets")
        if not value:
            continue
        if len(value) > _MAX_SECRET_CHAR_LENGTH:
            raise ValueError("forwarded ACP environment secret exceeds the safe size limit")
        if value in seen:
            continue
        if len(value.encode("utf-8")) > _MAX_SECRET_BYTE_LENGTH:
            raise ValueError("forwarded ACP environment secret exceeds the safe size limit")
        seen.add(value)
        secrets.append(value)
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
    except (TimeoutError, asyncio.TimeoutError):
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


class ACPFactoryContractError(RuntimeError):
    """An injected startup factory violates the required asynchronous contract."""


def _is_async_factory(factory: Callable[..., Any]) -> bool:
    """Return whether an injected factory has the required native async call contract."""

    if inspect.iscoroutinefunction(factory):
        return True
    return inspect.iscoroutinefunction(getattr(factory, "__call__", None))


def _track_cleanup(cleanup: Awaitable[None]) -> None:
    task = asyncio.create_task(cleanup)
    _PENDING_FACTORY_CLEANUPS.add(task)
    task.add_done_callback(_PENDING_FACTORY_CLEANUPS.discard)


def _own_detached_factory_task(
    task: asyncio.Task[Any],
    *,
    cleanup_result: Callable[[Any], Awaitable[None]],
) -> None:
    """Retain a cancelled acquisition and clean any resource it returns later."""

    _PENDING_FACTORY_TASKS.add(task)

    def completed(done: asyncio.Task[Any]) -> None:
        _PENDING_FACTORY_TASKS.discard(done)
        if done.cancelled():
            return
        try:
            result = done.result()
        except BaseException:
            return
        _track_cleanup(cleanup_result(result))

    task.add_done_callback(completed)


async def _acquire_injected_factory(
    factory: Callable[..., Any],
    *args: Any,
    timeout: float,
    cleanup_result: Callable[[Any], Awaitable[None]],
    **kwargs: Any,
) -> Any:
    """Invoke only native async injected factories under bounded detached ownership.

    Injected factories must be native async callables, return their resource only
    on successful completion, and cooperate with cancellation. Misbehaving tasks
    are retained in a fixed-size ownership set; any eventual result is cleaned.
    """

    if not _is_async_factory(factory):
        raise ACPFactoryContractError("ACP injected factories must be async callables")
    if len(_PENDING_FACTORY_TASKS) + len(_PENDING_FACTORY_CLEANUPS) >= _MAX_PENDING_FACTORY_TASKS:
        raise ACPFactoryContractError("too many pending ACP factory acquisitions")
    task = asyncio.create_task(factory(*args, **kwargs))
    _PENDING_FACTORY_TASKS.add(task)
    try:
        done, _ = await asyncio.wait({task}, timeout=timeout)
    except asyncio.CancelledError:
        task.cancel()
        _own_detached_factory_task(task, cleanup_result=cleanup_result)
        raise
    if done:
        _PENDING_FACTORY_TASKS.discard(task)
        return task.result()
    task.cancel()
    _own_detached_factory_task(task, cleanup_result=cleanup_result)
    raise ACPStartupTimeoutError


@asynccontextmanager
async def open_acp_process_session(
    config: ACPProcessConfig,
    callbacks: ACPClientCallbacks,
    *,
    process_factory: Callable[..., Awaitable[Any]] | None = None,
    connection_factory: Callable[..., Awaitable[Any]] | None = None,
    environ: Mapping[str, str] | None = None,
) -> AsyncIterator[ACPProcessSession]:
    """Spawn, initialize, create one session, then always close and reap it."""

    process = None
    connection = None
    session_id = None
    stderr_task = None
    cancel_session = False
    env = build_process_environment(config, environ)
    secret_values = _validated_secret_values([env[name] for name in config.env if name in env])
    loop = asyncio.get_running_loop()
    startup_deadline = loop.time() + config.startup_timeout

    def remaining_startup_time() -> float:
        remaining = startup_deadline - loop.time()
        if remaining <= 0:
            raise ACPStartupTimeoutError
        return remaining

    async def cleanup_process(acquired: Any) -> None:
        await _stop_and_reap(acquired, config.shutdown_grace_period)

    async def cleanup_connection(acquired: Any) -> None:
        await asyncio.wait_for(_close_connection(acquired), timeout=config.shutdown_grace_period)

    try:
        spawn = asyncio.create_subprocess_exec if process_factory is None else process_factory
        process = await _acquire_injected_factory(
            spawn,
            config.command,
            *config.args,
            timeout=remaining_startup_time(),
            cleanup_result=cleanup_process,
            cwd=str(config.cwd) if config.cwd is not None else None,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if process.returncode is not None:
            raise ChildProcessError("ACP subprocess exited during startup")
        stderr_task = asyncio.create_task(_drain_stderr(process.stderr, secret_values))

        if connection_factory is None:
            from acp import connect_to_agent

            connection = connect_to_agent(callbacks, process.stdin, process.stdout)
        else:
            connection = await _acquire_injected_factory(
                connection_factory,
                callbacks,
                process.stdin,
                process.stdout,
                timeout=remaining_startup_time(),
                cleanup_result=cleanup_connection,
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
        except (TimeoutError, asyncio.TimeoutError) as exc:
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
