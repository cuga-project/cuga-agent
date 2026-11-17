"""Create the LangGraph checkpointer for ``settings.storage`` (issue #774).

- ``checkpointer = "memory"``: ``MemorySaver`` with langgraph's default serializer.
- ``checkpointer = "durable"``, ``mode = "local"``: ``AsyncSqliteSaver`` on one
  ``aiosqlite`` connection to ``resolve_checkpoint_db_path(storage)``.
- ``checkpointer = "durable"``, ``mode = "prod"``: ``AsyncPostgresSaver`` on a
  ``psycopg_pool.AsyncConnectionPool``.

Durable savers use ``build_checkpoint_serde()``. Close the **handle**, never
``graph.checkpointer``: a strict-msgpack ``compile()`` swaps in a clone of the saver.

Keep this module import-light: ``aiosqlite``, ``psycopg_pool`` and
``langgraph.checkpoint.*`` are imported inside the functions that use them.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from loguru import logger

if TYPE_CHECKING:
    from langgraph.checkpoint.base import BaseCheckpointSaver

CheckpointerKind = Literal["memory", "sqlite", "postgres"]


async def _noop() -> None:
    return None


@dataclass
class CheckpointerHandle:
    """A saver plus the connection or pool it runs on. ``aclose()`` is idempotent."""

    saver: BaseCheckpointSaver
    kind: CheckpointerKind
    _close: Callable[[], Awaitable[None]] = field(default=_noop, repr=False)
    closed: bool = field(default=False, init=False)

    async def aclose(self) -> None:
        if self.closed:
            return
        self.closed = True
        await self._close()


async def create_checkpointer(storage: Any) -> CheckpointerHandle:
    """Build the checkpointer described by ``settings.storage``.

    Raises ``ValueError`` for an unknown ``checkpointer`` or ``mode`` (never falls
    back to memory) and for ``mode = "prod"`` without ``postgres_url``.
    """
    checkpointer = getattr(storage, "checkpointer", None)
    if checkpointer == "memory":
        from langgraph.checkpoint.memory import MemorySaver

        return CheckpointerHandle(saver=MemorySaver(), kind="memory")
    if checkpointer != "durable":
        raise ValueError(f"storage.checkpointer must be 'durable' or 'memory', got {checkpointer!r}")

    mode = getattr(storage, "mode", None) or "local"
    if mode == "local":
        from cuga.backend.storage.checkpoint.paths import resolve_checkpoint_db_path

        return await _create_sqlite(Path(resolve_checkpoint_db_path(storage)))
    if mode == "prod":
        url = getattr(storage, "postgres_url", None) or ""
        if not url:
            raise ValueError("storage.postgres_url is required when storage.mode=prod")
        return await _create_postgres(
            url,
            min_size=getattr(storage, "checkpoint_pool_min_size", 1),
            max_size=getattr(storage, "checkpoint_pool_max_size", 10),
        )
    raise ValueError(f"storage.mode must be 'local' or 'prod', got {mode!r}")


async def _create_sqlite(path: Path) -> CheckpointerHandle:
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    from cuga.backend.storage.checkpoint.serde import build_checkpoint_serde

    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Holds conversation content; SQLite gives the -wal/-shm files the same mode.
        path.touch(mode=0o600, exist_ok=True)
        path.chmod(0o600)
    except OSError as exc:
        logger.warning("Could not restrict checkpoint DB permissions to 0o600: {}", type(exc).__name__)

    conn = await aiosqlite.connect(str(path))
    try:
        await conn.execute("PRAGMA journal_mode=WAL")
        saver = AsyncSqliteSaver(conn, serde=build_checkpoint_serde())
        await saver.setup()
    except BaseException:
        await conn.close()
        raise
    logger.info("Checkpointer ready (kind=sqlite, file={})", path.name)
    return CheckpointerHandle(saver=saver, kind="sqlite", _close=conn.close)


async def _create_postgres(url: str, *, min_size: int, max_size: int) -> CheckpointerHandle:
    if min_size < 1 or min_size > max_size:
        raise ValueError(
            "storage.checkpoint_pool_min_size must be >= 1 and <= storage.checkpoint_pool_max_size"
        )

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    from cuga.backend.storage.checkpoint.serde import build_checkpoint_serde

    pool = AsyncConnectionPool(
        url,
        min_size=min_size,
        max_size=max_size,
        # Required by AsyncPostgresSaver. No prepared statements, so transaction poolers work.
        kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": None},
        open=False,
    )
    try:
        await pool.open(wait=True)
        saver = AsyncPostgresSaver(pool, serde=build_checkpoint_serde())
        await saver.setup()  # idempotent; the database user needs DDL rights
    except BaseException:
        await pool.close()
        raise
    logger.info("Checkpointer ready (kind=postgres, pool={}..{})", min_size, max_size)
    return CheckpointerHandle(saver=saver, kind="postgres", _close=pool.close)
