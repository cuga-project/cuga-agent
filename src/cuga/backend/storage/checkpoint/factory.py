"""Create the LangGraph checkpointer for ``settings.storage`` (issue #774, W02.1).

``create_checkpointer(storage)`` maps the ``[storage]`` settings onto one of
three savers and returns a :class:`CheckpointerHandle` that owns the underlying
connection or pool:

- ``checkpointer = "memory"``: ``MemorySaver`` with langgraph's default
  serializer (no CUGA allowlist, so in-memory behavior is unchanged).
- ``checkpointer = "durable"`` and ``mode = "local"``: ``AsyncSqliteSaver`` on
  one ``aiosqlite`` connection to ``resolve_checkpoint_db_path(storage)``.
- ``checkpointer = "durable"`` and ``mode = "prod"``: ``AsyncPostgresSaver`` on
  a ``psycopg_pool.AsyncConnectionPool``.

Durable savers use ``build_checkpoint_serde()`` unless ``serde=`` is passed.

Close and probe the **handle**, never ``graph.checkpointer``: a strict-msgpack
``compile()`` swaps in a ``saver.with_allowlist()`` clone, so the graph's saver
is not the object that owns the connection.

``_create_memory``, ``_create_sqlite`` and ``_create_postgres`` take explicit
arguments and do not read settings, so the SDK constructors (W15.3) can reuse
them.

Keep this module import-light: ``aiosqlite``, ``psycopg``, ``psycopg_pool`` and
``langgraph.checkpoint.*`` are imported inside the functions that use them.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Optional

from loguru import logger

if TYPE_CHECKING:
    from langgraph.checkpoint.base import BaseCheckpointSaver
    from langgraph.checkpoint.serde.base import SerializerProtocol

CheckpointerKind = Literal["memory", "sqlite", "postgres"]

_VALID_CHECKPOINTERS = ("durable", "memory")
_VALID_MODES = ("local", "prod")
_DB_FILE_MODE = 0o600
DEFAULT_PING_TIMEOUT_S = 1.0
DEFAULT_POOL_OPEN_TIMEOUT_S = 30.0


async def _noop() -> None:
    return None


async def _close_after_failure(close: Callable[[], Awaitable[None]], kind: str) -> None:
    # Release the connection/pool without masking the original startup error.
    try:
        await close()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Closing the {} checkpointer after a failed startup also failed: {}", kind, type(exc).__name__
        )


@dataclass
class CheckpointerHandle:
    """A saver plus the connection or pool it runs on.

    ``aclose()`` releases the connection or pool; calling it again is a no-op.
    ``ping()`` is the health probe (W02.4) and never raises. The handle is also
    an async context manager that closes on exit.
    """

    saver: BaseCheckpointSaver
    kind: CheckpointerKind
    _close: Callable[[], Awaitable[None]] = field(default=_noop, repr=False)
    _probe: Callable[[], Awaitable[None]] = field(default=_noop, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    @property
    def closed(self) -> bool:
        return self._closed

    async def aclose(self) -> None:
        if self._closed:
            return
        # Mark first: a failing close is not retried by a second call.
        self._closed = True
        await self._close()

    async def ping(self, timeout: float = DEFAULT_PING_TIMEOUT_S) -> bool:
        """Return whether the saver's backend answers within ``timeout`` seconds.

        Memory is always healthy while open. SQLite is healthy while its
        aiosqlite connection is open (no query: one would queue behind writes on
        the connection's single thread); Postgres runs ``SELECT 1`` on a pooled
        connection. Never
        raises; a closed handle reports ``False``.
        """
        if self._closed:
            return False
        try:
            await asyncio.wait_for(self._probe(), timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - the probe must never raise
            logger.debug("Checkpointer health probe failed (kind={}): {}", self.kind, type(exc).__name__)
            return False
        return True

    async def __aenter__(self) -> CheckpointerHandle:
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.aclose()


# ---------------------------------------------------------------------------
# Settings → explicit arguments
# ---------------------------------------------------------------------------


async def create_checkpointer(
    storage: Any, *, serde: Optional[SerializerProtocol] = None
) -> CheckpointerHandle:
    """Build the checkpointer described by ``settings.storage``.

    Raises ``ValueError`` for an unknown ``storage.checkpointer`` or
    ``storage.mode`` (never falls back to memory), invalid pool sizes, or
    ``mode = "prod"`` without ``postgres_url`` (same message as the other stores).
    """
    checkpointer = getattr(storage, "checkpointer", None)
    if checkpointer not in _VALID_CHECKPOINTERS:
        raise ValueError(f"storage.checkpointer must be one of {_VALID_CHECKPOINTERS}, got {checkpointer!r}")

    if checkpointer == "memory":
        return _create_memory(serde=serde)

    mode = getattr(storage, "mode", None) or "local"
    if mode == "local":
        from cuga.backend.storage.checkpoint.paths import resolve_checkpoint_db_path

        return await _create_sqlite(resolve_checkpoint_db_path(storage), serde=serde)
    if mode == "prod":
        url = getattr(storage, "postgres_url", None) or ""
        if not url:
            raise ValueError("storage.postgres_url is required when storage.mode=prod")
        return await _create_postgres(
            url,
            min_size=getattr(storage, "checkpoint_pool_min_size", 1),
            max_size=getattr(storage, "checkpoint_pool_max_size", 10),
            serde=serde,
        )
    raise ValueError(f"storage.mode must be one of {_VALID_MODES}, got {mode!r}")


def _durable_serde(serde: Optional[SerializerProtocol]) -> SerializerProtocol:
    if serde is not None:
        return serde
    from cuga.backend.storage.checkpoint.serde import build_checkpoint_serde

    return build_checkpoint_serde()


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------


def _create_memory(*, serde: Optional[SerializerProtocol] = None) -> CheckpointerHandle:
    from langgraph.checkpoint.memory import MemorySaver

    # serde=None keeps langgraph's default serializer (BaseCheckpointSaver uses
    # ``serde or self.serde``), not the CUGA allowlist, which would turn
    # pydantic/Enum/dataclass values in free-form fields into dicts (#774).
    saver = MemorySaver(serde=serde)
    logger.info("Checkpointer ready (kind=memory)")
    return CheckpointerHandle(saver=saver, kind="memory")


# ---------------------------------------------------------------------------
# SQLite (storage.mode = local)
# ---------------------------------------------------------------------------


def _ensure_private_file(path: Path) -> None:
    """Create ``path`` owner-only (0o600): it holds conversation content.

    SQLite creates the ``-wal``/``-shm`` side files with the main file's mode.
    Best effort, like ``storage.facade._local_db_path``: skipped on file systems
    without POSIX modes.
    """
    try:
        path.touch(mode=_DB_FILE_MODE, exist_ok=True)
        path.chmod(_DB_FILE_MODE)
    except OSError as exc:
        logger.warning("Could not restrict checkpoint DB permissions to 0o600: {}", type(exc).__name__)


def _sqlite_connection_open(conn: Any) -> bool:
    """Whether an ``aiosqlite.Connection`` can still run queries, without queueing one.

    aiosqlite (0.22.1) has no public "is open" property. This mirrors the guard
    its own ``Connection._execute`` uses before queueing work
    (``if not self._running or not self._connection``, aiosqlite/core.py:152)
    and also checks the worker thread is alive (``self._thread``, core.py:90).
    Missing attributes read as closed, so an aiosqlite change fails the probe
    (and the tests) rather than reporting healthy.
    """
    thread = getattr(conn, "_thread", None)
    return bool(
        getattr(conn, "_running", False)
        and getattr(conn, "_connection", None) is not None
        and thread is not None
        and thread.is_alive()
    )


async def _create_sqlite(
    path: str | Path, *, serde: Optional[SerializerProtocol] = None
) -> CheckpointerHandle:
    """Open ``AsyncSqliteSaver`` on ``path`` (WAL, tables created)."""
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    _ensure_private_file(path)

    conn = await aiosqlite.connect(str(path))
    try:
        # Readers do not block the writer.
        await conn.execute("PRAGMA journal_mode=WAL")
        saver = AsyncSqliteSaver(conn, serde=_durable_serde(serde))
        await saver.setup()
    except BaseException:
        await _close_after_failure(conn.close, "sqlite")
        raise

    async def _probe() -> None:
        if not _sqlite_connection_open(conn):
            raise ConnectionError("sqlite checkpointer connection is closed")

    logger.info("Checkpointer ready (kind=sqlite, file={})", path.name)
    return CheckpointerHandle(saver=saver, kind="sqlite", _close=conn.close, _probe=_probe)


# ---------------------------------------------------------------------------
# Postgres (storage.mode = prod)
# ---------------------------------------------------------------------------


def _check_pool_sizes(min_size: Any, max_size: Any) -> tuple[int, int]:
    # The settings validators allow 0 (gte=0); a pool needs at least one
    # connection, and psycopg_pool rejects min > max with a less clear message.
    for name, value in (("checkpoint_pool_min_size", min_size), ("checkpoint_pool_max_size", max_size)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"storage.{name} must be an integer, got {value!r}")
        if value < 1:
            raise ValueError(f"storage.{name} must be >= 1, got {value}")
    if min_size > max_size:
        raise ValueError(
            f"storage.checkpoint_pool_min_size ({min_size}) must be <= storage.checkpoint_pool_max_size ({max_size})"
        )
    return min_size, max_size


def _redacted_host(url: str) -> str:
    """Return ``host[:port]`` from a Postgres URL or conninfo string, never credentials."""
    try:
        if "://" in url:
            from urllib.parse import urlsplit

            parts = urlsplit(url)
            host = parts.hostname or "localhost"
            return f"{host}:{parts.port}" if parts.port else host

        from psycopg.conninfo import conninfo_to_dict

        params = conninfo_to_dict(url)
        host = str(params.get("host") or "localhost")
        port = params.get("port")
        return f"{host}:{port}" if port else host
    except Exception:  # noqa: BLE001 - logging must not fail or echo the URL
        return "<unparsed>"


async def _create_postgres(
    url: str,
    *,
    min_size: int = 1,
    max_size: int = 10,
    serde: Optional[SerializerProtocol] = None,
    open_timeout: float = DEFAULT_POOL_OPEN_TIMEOUT_S,
) -> CheckpointerHandle:
    """Open ``AsyncPostgresSaver`` on a connection pool (tables created/migrated).

    ``setup()`` runs every time; it is idempotent, and the database user needs
    DDL rights.
    """
    if not url:
        raise ValueError("storage.postgres_url is required when storage.mode=prod")
    min_size, max_size = _check_pool_sizes(min_size, max_size)

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    host = _redacted_host(url)
    pool = AsyncConnectionPool(
        url,
        min_size=min_size,
        max_size=max_size,
        # Required by AsyncPostgresSaver: autocommit DDL/writes, dict rows; no
        # server-side prepared statements, so transaction poolers work.
        kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": 0},
        open=False,
    )
    try:
        await pool.open(wait=True, timeout=open_timeout)
        saver = AsyncPostgresSaver(pool, serde=_durable_serde(serde))
        await saver.setup()
    except BaseException as exc:
        # psycopg_pool closes the pool itself when ``open(wait=True)`` times out;
        # this also covers setup() failures and cancellation (close is idempotent).
        logger.error("Checkpointer startup failed (kind=postgres, host={}): {}", host, type(exc).__name__)
        await _close_after_failure(pool.close, "postgres")
        raise

    async def _probe() -> None:
        async with pool.connection() as conn:
            await conn.execute("SELECT 1")

    logger.info("Checkpointer ready (kind=postgres, host={}, pool={}..{})", host, min_size, max_size)
    return CheckpointerHandle(saver=saver, kind="postgres", _close=pool.close, _probe=_probe)
