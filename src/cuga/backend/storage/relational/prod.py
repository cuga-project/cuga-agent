from typing import Any, List, Optional


class ProdRelationalStore:
    def __init__(self, postgres_url: str, db_name: str):
        self._postgres_url = postgres_url
        self._db_name = db_name
        self._conn: Any = None

    def _get_conn(self):
        if self._conn is None or getattr(self._conn, "closed", True):
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError:
                raise ImportError("psycopg or psycopg[binary] required for storage.mode=prod")
            self._conn = psycopg.connect(self._postgres_url, row_factory=dict_row)
        return self._conn

    def _placeholders(self, sql: str) -> str:
        return sql.replace("?", "%s")

    def execute(self, sql: str, params: tuple = ()) -> None:
        sql = self._placeholders(sql)
        with self._get_conn().cursor() as cur:
            cur.execute(sql, params)
            self._last_rowcount = cur.rowcount
        self._get_conn().commit()

    def fetchall(self, sql: str, params: tuple = ()) -> List[Any]:
        sql = self._placeholders(sql)
        with self._get_conn().cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())

    def fetchone(self, sql: str, params: tuple = ()) -> Optional[Any]:
        sql = self._placeholders(sql)
        with self._get_conn().cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    def commit(self) -> None:
        conn = self._conn
        if conn is not None and not getattr(conn, "closed", True):
            conn.commit()

    def close(self) -> None:
        if self._conn is not None and not getattr(self._conn, "closed", True):
            self._conn.close()
            self._conn = None
