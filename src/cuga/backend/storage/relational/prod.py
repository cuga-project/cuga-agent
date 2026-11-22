import asyncio
import re
import threading
import weakref
from typing import Any, List, Optional

from cuga.backend.storage.loop_lock import PerLoopLock

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
        # An asyncpg pool works only on the event loop that created it. The store is cached for
        # the whole process, and sync callers (config_store.run_sync, secrets_store.get_secret_sync)
        # run each call on its own short-lived loop, so each loop gets its own pool.
        self._pools: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, Any]" = weakref.WeakKeyDictionary()
        self._pools_guard = threading.Lock()
        self._pool_lock = PerLoopLock()

    async def _get_pool(self):
        loop = asyncio.get_running_loop()
        with self._pools_guard:
            pool = self._pools.get(loop)
        if pool is None:
            # Concurrent first requests on a loop would each create a pool (up to 4 connections
            # each) and leak all but the last one.
            async with self._pool_lock.get():
                with self._pools_guard:
                    pool = self._pools.get(loop)
                if pool is None:
                    if asyncpg is None:
                        raise ImportError(
                            "asyncpg required for storage.mode=prod. Install with: uv add asyncpg"
                        )
                    pool = await asyncpg.create_pool(
                        self._postgres_url,
                        min_size=1,
                        max_size=4,
                        command_timeout=60,
                    )
                    with self._pools_guard:
                        self._pools[loop] = pool
        return pool

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

    async def execute_batch(self, statements: list[tuple[str, tuple]]) -> None:
        """Run all statements on one connection and one PostgreSQL transaction."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                for sql, params in statements:
                    await conn.execute(_placeholders(sql), *params)

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
        """Close the running event loop's pool (required before a short-lived loop ends, e.g. at the
        end of ``asyncio.run``). Pools that other loops are using stay open."""
        with self._pools_guard:
            pool = self._pools.pop(asyncio.get_running_loop(), None)
        if pool is not None:
            await pool.close()
