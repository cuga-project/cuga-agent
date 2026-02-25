import sqlite3
from typing import Any, List, Optional


class LocalRelationalStore:
    def __init__(self, db_path: str):
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def execute(self, sql: str, params: tuple = ()) -> None:
        cur = self._get_conn().execute(sql, params)
        self._last_rowcount = getattr(cur, "rowcount", -1)

    def fetchall(self, sql: str, params: tuple = ()) -> List[Any]:
        return self._get_conn().execute(sql, params).fetchall()

    def fetchone(self, sql: str, params: tuple = ()) -> Optional[Any]:
        return self._get_conn().execute(sql, params).fetchone()

    def commit(self) -> None:
        if self._conn is not None:
            self._conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
