import asyncio
import sqlite3
from typing import Any, List, Optional


class LocalRelationalStore:
    def __init__(self, db_path: str):
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _execute_sync(self, sql: str, params: tuple = ()) -> None:
        cur = self._get_conn().execute(sql, params)
        self._last_rowcount = getattr(cur, "rowcount", -1)

    def _fetchall_sync(self, sql: str, params: tuple = ()) -> List[Any]:
        return list(self._get_conn().execute(sql, params).fetchall())

    def _fetchone_sync(self, sql: str, params: tuple = ()) -> Optional[Any]:
        row = self._get_conn().execute(sql, params).fetchone()
        return dict(row) if row is not None else None

    async def execute(self, sql: str, params: tuple = ()) -> None:
        await asyncio.to_thread(self._execute_sync, sql, params)

    async def fetchall(self, sql: str, params: tuple = ()) -> List[Any]:
        rows = await asyncio.to_thread(self._fetchall_sync, sql, params)
        return [dict(r) for r in rows]

    async def fetchone(self, sql: str, params: tuple = ()) -> Optional[Any]:
        return await asyncio.to_thread(self._fetchone_sync, sql, params)

    async def commit(self) -> None:
        if self._conn is not None:
            await asyncio.to_thread(self._conn.commit)

    async def close(self) -> None:
        if self._conn is not None:
            await asyncio.to_thread(self._conn.close)
            self._conn = None
