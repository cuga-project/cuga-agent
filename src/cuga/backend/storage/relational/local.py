import asyncio
import sqlite3
from typing import Any, List, Optional


class LocalRelationalStore:
    def __init__(self, db_path: str):
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = asyncio.Lock()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._on_connection_opened(self._conn)
        return self._conn

    def _on_connection_opened(self, conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")

    def _execute_sync(self, sql: str, params: tuple = ()) -> None:
        cur = self._get_conn().execute(sql, params)
        self._last_rowcount = getattr(cur, "rowcount", -1)

    def _fetchall_sync(self, sql: str, params: tuple = ()) -> List[Any]:
        return list(self._get_conn().execute(sql, params).fetchall())

    def _fetchone_sync(self, sql: str, params: tuple = ()) -> Optional[Any]:
        row = self._get_conn().execute(sql, params).fetchone()
        return dict(row) if row is not None else None

    async def execute(self, sql: str, params: tuple = ()) -> None:
        async with self._lock:
            await asyncio.to_thread(self._execute_sync, sql, params)

    async def fetchall(self, sql: str, params: tuple = ()) -> List[Any]:
        async with self._lock:
            rows = await asyncio.to_thread(self._fetchall_sync, sql, params)
        return [dict(r) for r in rows]

    async def fetchone(self, sql: str, params: tuple = ()) -> Optional[Any]:
        async with self._lock:
            return await asyncio.to_thread(self._fetchone_sync, sql, params)

    async def commit(self) -> None:
        async with self._lock:
            if self._conn is not None:
                await asyncio.to_thread(self._conn.commit)

    async def close(self) -> None:
        async with self._lock:
            if self._conn is not None:
                await asyncio.to_thread(self._conn.close)
                self._conn = None

    def close_sync(self) -> None:
        """Close the connection synchronously.

        For destructive resets (e.g. reset_config_db) invoked from sync callers that
        delete the backing file; not lock-guarded since no concurrent access is expected.
        """
        if self._conn is not None:
            self._conn.close()
            self._conn = None
