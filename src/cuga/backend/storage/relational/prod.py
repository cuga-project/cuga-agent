import re
from typing import Any, List, Optional

try:
    import asyncpg
except ImportError:
    asyncpg = None


def _placeholders(sql: str) -> str:
    i = [0]

    def repl(_: Any) -> str:
        i[0] += 1
        return f"${i[0]}"

    return re.sub(r"\?", repl, sql)


class ProdRelationalStore:
    def __init__(self, postgres_url: str, db_name: str):
        self._postgres_url = postgres_url
        self._db_name = db_name
        self._pool: Any = None

    async def _get_pool(self):
        if self._pool is None:
            if asyncpg is None:
                raise ImportError("asyncpg required for storage.mode=prod. Install with: uv add asyncpg")
            self._pool = await asyncpg.create_pool(
                self._postgres_url,
                min_size=1,
                max_size=4,
                command_timeout=60,
            )
        return self._pool

    async def execute(self, sql: str, params: tuple = ()) -> None:
        sql = _placeholders(sql)
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(sql, *params)
            if result:
                parts = result.split()
                try:
                    self._last_rowcount = int(parts[-1])
                except (ValueError, IndexError):
                    self._last_rowcount = -1
            else:
                self._last_rowcount = -1

    async def fetchall(self, sql: str, params: tuple = ()) -> List[Any]:
        sql = _placeholders(sql)
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
            return [dict(r) for r in rows]

    async def fetchone(self, sql: str, params: tuple = ()) -> Optional[Any]:
        sql = _placeholders(sql)
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(sql, *params)
            return dict(row) if row else None

    async def commit(self) -> None:
        pass

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
