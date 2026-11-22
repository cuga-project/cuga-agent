import asyncio
import contextlib
import hashlib
import re
import threading
import weakref
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional

from loguru import logger

from cuga.backend.storage.embedding.base import EmbeddingSchemaConfig
from cuga.backend.storage.loop_lock import PerLoopLock

SCOPE_COLS = ["tenant_id", "instance_id"]

# Table identifiers are interpolated directly into DDL statements where PostgreSQL
# does not support parameterised identifiers. Restricting names to lowercase ASCII
# also makes the 63-character bound equivalent to PostgreSQL's 63-byte limit.
_SAFE_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_INDEX_DIGEST_LENGTH = 16
_INDEX_PREFIX = "idx_"
_INDEX_SUFFIX = "_embedding"
_INDEX_NAME_LIMIT = 63


def _embedding_index_name(collection_name: str) -> str:
    """Return a bounded deterministic index identifier for a collection table."""
    digest = hashlib.sha256(collection_name.encode("ascii")).hexdigest()[:_INDEX_DIGEST_LENGTH]
    fixed_length = len(_INDEX_PREFIX) + 1 + len(digest) + len(_INDEX_SUFFIX)
    collection_prefix = collection_name[: _INDEX_NAME_LIMIT - fixed_length]
    return f"{_INDEX_PREFIX}{collection_prefix}_{digest}{_INDEX_SUFFIX}"


def _placeholders(n: int) -> str:
    return ", ".join(f"${i + 1}" for i in range(n))


def _pg_type(s: str) -> str:
    m = {"text": "TEXT", "integer": "BIGINT", "boolean": "BOOLEAN", "float": "DOUBLE PRECISION"}
    return m.get(s.lower(), "TEXT")


# Stores created with shared_pool=True use one pool per (event loop, database) instead of one each.
# The knowledge adapter runs every Postgres call on one loop that lives as long as the process, so
# all its collections (one per agent and one per conversation) share this bounded set of
# connections. A shared pool is never closed: use it only from such a loop.
SHARED_POOL_MAX_SIZE = 8
SHARED_POOL_IDLE_S = 60.0  # an idle connection closes after this, down to none
_shared_pools: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, Dict[str, Any]]" = (
    weakref.WeakKeyDictionary()
)
_shared_pools_guard = threading.Lock()
_shared_pool_lock = PerLoopLock()


async def _new_pool(postgres_url: str, **pool_kwargs: Any) -> Any:
    """An asyncpg pool whose every connection can store and return vectors."""
    import asyncpg

    try:
        from pgvector.asyncpg import register_vector
    except ImportError:
        raise ImportError("pgvector is required for storage.mode=prod. Install with: uv add pgvector")

    async def prepare_connection(conn: Any) -> None:
        # Runs for every connection the pool opens, so each one accepts and returns vectors.
        try:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        except asyncpg.exceptions.UniqueViolationError:
            pass  # another session created it at the same moment
        except asyncpg.exceptions.InsufficientPrivilegeError:
            logger.warning(
                "Permission denied for CREATE EXTENSION vector; assuming it was pre-installed by "
                "a database admin. If pgvector is not installed in this database, embedding store "
                "setup will fail next."
            )
        await register_vector(conn)

    return await asyncpg.create_pool(postgres_url, command_timeout=60, init=prepare_connection, **pool_kwargs)


async def shared_pool(postgres_url: str) -> Any:
    """The running loop's shared pool for ``postgres_url``, created on first use: at most
    SHARED_POOL_MAX_SIZE connections, each closed after SHARED_POOL_IDLE_S idle. It is never
    closed, so call this only from an event loop that lives as long as the process."""
    loop = asyncio.get_running_loop()
    with _shared_pools_guard:
        pool = _shared_pools.get(loop, {}).get(postgres_url)
    if pool is None:
        async with _shared_pool_lock.get():
            with _shared_pools_guard:
                pool = _shared_pools.get(loop, {}).get(postgres_url)
            if pool is None:
                pool = await _new_pool(
                    postgres_url,
                    min_size=0,
                    max_size=SHARED_POOL_MAX_SIZE,
                    max_inactive_connection_lifetime=SHARED_POOL_IDLE_S,
                    server_settings={"application_name": "cuga-knowledge"},  # visible in pg_stat_activity
                )
                with _shared_pools_guard:
                    _shared_pools.setdefault(loop, {})[postgres_url] = pool
    return pool


@contextlib.asynccontextmanager
async def _acquire(pool: Any) -> AsyncIterator[Any]:
    """A connection from ``pool``. asyncpg returns a slot whose connect failed (for example at a
    role's connection limit) to the top of its LIFO queue, so every later call would retry that
    refused connect while the pool's open connections sit idle below it until they expire. After
    a failed acquire, the open connections go back on top."""
    try:
        conn = await pool.acquire()
    except Exception:
        slots = getattr(getattr(pool, "_queue", None), "_queue", None)  # asyncpg internals, pinned by a test
        if isinstance(slots, list):
            slots.sort(key=lambda holder: holder.is_connected())  # popped from the end: open ones first
        raise
    try:
        yield conn
    finally:
        await pool.release(conn)


class ProdEmbeddingStore:
    def __init__(
        self,
        postgres_url: str,
        collection_name: str,
        schema: EmbeddingSchemaConfig,
        *,
        shared_pool: bool = False,
    ):
        if _SAFE_IDENTIFIER_RE.fullmatch(collection_name) is None:
            raise ValueError(
                f"collection_name {collection_name!r} is not a valid PostgreSQL identifier. "
                "Must match ^[a-z][a-z0-9_]{0,62}$ (lowercase-leading, max 63 characters)."
            )
        self._postgres_url = postgres_url
        self._collection_name = collection_name
        self._schema = schema
        # An asyncpg pool works only on the event loop that created it, and callers may use several
        # loops (worker threads that each call asyncio.run, the server's loop). So each loop gets its
        # own pool, and closing one loop's pool leaves the pools other loops are still using alone.
        # With shared_pool, the store uses the loop's pool for its database (_shared_pool) instead.
        self._shared = shared_pool
        self._ensured_pool: Any = None  # the shared pool this store's table was last checked on
        self._slots: Optional[asyncio.Semaphore] = None  # this store's share of the shared pool
        self._pools: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, Any]" = weakref.WeakKeyDictionary()
        self._pools_guard = threading.Lock()
        self._pool_lock = PerLoopLock()

    async def _get_pool(self):
        if self._shared:
            pool = await shared_pool(self._postgres_url)
            if self._ensured_pool is not pool:
                async with self._pool_lock.get():
                    if self._ensured_pool is not pool:
                        await self._ensure_table(pool)
                        self._ensured_pool = pool
            return pool
        loop = asyncio.get_running_loop()
        with self._pools_guard:
            pool = self._pools.get(loop)
        if pool is None:
            # One caller creates and prepares the loop's pool; concurrent first callers on the
            # same loop wait for it instead of creating their own or using it before the table exists.
            async with self._pool_lock.get():
                with self._pools_guard:
                    pool = self._pools.get(loop)
                if pool is None:
                    pool = await self._create_pool()
                    with self._pools_guard:
                        self._pools[loop] = pool
        return pool

    async def _create_pool(self) -> Any:
        pool = await _new_pool(self._postgres_url, min_size=1, max_size=4)
        try:
            await self._ensure_table(pool)
        except BaseException:
            await pool.close()
            raise
        return pool

    async def close_pool(self) -> None:
        """Close the running event loop's pool (required before that loop is discarded, e.g. at the end of
        ``asyncio.run``). Pools that other loops are using stay open. A shared pool stays open for the
        other stores using it; this store only forgets that its table was checked."""
        if self._shared:
            self._ensured_pool = None
            return
        with self._pools_guard:
            pool = self._pools.pop(asyncio.get_running_loop(), None)
        if pool is not None:
            await pool.close()

    @contextlib.asynccontextmanager
    async def _connection(self, pool: Any) -> AsyncIterator[Any]:
        """A connection for one operation. On the shared pool a store holds at most
        SHARED_POOL_MAX_SIZE - 2 connections, so one slow or locked table leaves connections for
        every other collection."""
        if self._shared and self._slots is None:
            self._slots = asyncio.Semaphore(SHARED_POOL_MAX_SIZE - 2)  # shared stores run on one loop
        async with self._slots if self._shared else contextlib.nullcontext():
            async with _acquire(pool) as conn:
                yield conn

    def _table_is_missing(self, exc: BaseException) -> bool:
        """A shared (knowledge) store's table was dropped by another process. Such a store reads it
        as empty, and an upload's first batch creates it again. Other stores (the policy store)
        keep failing, as before."""
        from asyncpg.exceptions import UndefinedTableError

        return self._shared and isinstance(exc, UndefinedTableError)

    def _scope_cols(self) -> List[str]:
        meta = self._schema.metadata_columns
        return [c for c in SCOPE_COLS if c in meta]

    async def _ensure_table(self, pool: Any = None) -> None:
        pool = pool if pool is not None else await self._get_pool()
        id_col = self._schema.id_column
        meta = self._schema.metadata_columns
        aux = self._schema.auxiliary_columns
        scope = self._scope_cols()
        pk = f"({', '.join(scope + [id_col])})" if scope else f"({id_col})"
        parts = [f"{id_col} TEXT", f"embedding vector({self._schema.embedding_dim})"]
        for k, v in meta.items():
            if k == id_col:
                continue
            parts.append(f"{k} {_pg_type(v)}")
        for k, v in aux.items():
            parts.append(f"{k} {_pg_type(v)}")
        parts.append(f"PRIMARY KEY {pk}")
        create_sql = f"CREATE TABLE IF NOT EXISTS {self._collection_name} ({', '.join(parts)})"
        async with self._connection(pool) as conn:
            # Most calls find both in place and skip the DDL, which would otherwise wait for
            # any insert in progress on the table.
            exists_sql = "SELECT to_regclass($1) IS NOT NULL AND to_regclass($2) IS NOT NULL"
            if await conn.fetchval(
                exists_sql, self._collection_name, _embedding_index_name(self._collection_name)
            ):
                return
            # Calls on other event loops, or in other processes, can create the same new table at
            # the same moment, and then all but one fail with a unique violation in the catalog.
            # The lock makes them wait until the first has created both; their IF NOT EXISTS then
            # skips. It is a session lock: the pool's reset of the connection when this block
            # ends (asyncpg runs pg_advisory_unlock_all) releases it, also when a statement fails.
            await conn.execute("SELECT pg_advisory_lock(hashtextextended($1, 0))", self._collection_name)
            await conn.execute(create_sql)
            index_name = _embedding_index_name(self._collection_name)
            await conn.execute(
                f"CREATE INDEX IF NOT EXISTS {index_name} "
                f"ON {self._collection_name} USING hnsw (embedding vector_cosine_ops)"
            )

    def _meta_keys(self) -> List[str]:
        return list(self._schema.metadata_columns.keys())

    def _aux_keys(self) -> List[str]:
        return list(self._schema.auxiliary_columns.keys())

    def _upsert_sql(self, extra: Optional[Dict[str, str]] = None) -> str:
        """Build the INSERT ... ON CONFLICT DO UPDATE statement once.

        Shared between ``add`` (one row via ``executemany`` of length 1) and
        ``add_many`` so schema/UPSERT order lives in exactly one place.
        ``extra`` maps more columns, written after the schema's, to the cast their
        placeholder needs (``""`` for none).
        """
        extra = extra or {}
        id_col = self._schema.id_column
        meta_keys = self._meta_keys()
        aux_keys = self._aux_keys()
        meta_keys_no_id = [k for k in meta_keys if k != id_col]
        cols = ["embedding", id_col] + meta_keys_no_id + aux_keys
        ph = ", ".join(
            [_placeholders(len(cols))]
            + [f"${len(cols) + i + 1}{cast}" for i, cast in enumerate(extra.values())]
        )
        cols += list(extra)
        col_list = ", ".join(cols)
        upsert = ", ".join(f"{c} = EXCLUDED.{c}" for c in ["embedding"] + meta_keys + aux_keys + list(extra))
        scope = self._scope_cols()
        conflict_cols = f"{', '.join(scope + [id_col])}" if scope else id_col
        return (
            f"INSERT INTO {self._collection_name} ({col_list}) VALUES ({ph}) "
            f"ON CONFLICT ({conflict_cols}) DO UPDATE SET {upsert}"
        )

    def _row_values(self, id_: str, embedding: List[float], metadata: Dict[str, Any]) -> List[Any]:
        id_col = self._schema.id_column
        meta_keys = self._meta_keys()
        aux_keys = self._aux_keys()
        meta_keys_no_id = [k for k in meta_keys if k != id_col]
        full = {id_col: id_, **metadata}
        return [embedding, id_] + [full.get(k) for k in meta_keys_no_id] + [full.get(k) for k in aux_keys]

    async def add(self, id: str, embedding: List[float], metadata: Dict[str, Any]) -> None:
        # Delegate to add_many so the SQL/row layout lives in one place. The
        # single-row upsert via executemany has identical pool/transaction
        # semantics to the original direct conn.execute.
        await self.add_many([(id, embedding, metadata)])

    async def add_many(
        self,
        items: List[tuple[str, List[float], Dict[str, Any]]],
        columns: Optional[Callable[[Any], Awaitable[Dict[str, tuple[str, List[Any]]]]]] = None,
        *,
        recreate_if_missing: bool = True,
    ) -> None:
        """Bulk-upsert items via a single ``executemany`` (issue #183 step 3).

        When a shared store's table was dropped by another process, the batch creates it again and
        is retried, but only with ``recreate_if_missing`` (an upload's first batch). A later batch
        fails instead, so the upload rolls back rather than bring back a collection deleted while
        it ran.

        ``columns(conn)``, when given, runs in the batch's transaction before the rows are
        written and returns more columns for them, ``{name: (placeholder cast, [value per
        item])}``. They are written in the same INSERT, and updated with the row on conflict.

        One ``pool.acquire`` and one ``conn.transaction`` per batch instead of
        per item, which is the big win on pgvector where each row was
        previously a network round-trip.
        """
        if not items:
            return
        pool = await self._get_pool()
        base_rows = [self._row_values(id_, embedding, metadata) for id_, embedding, metadata in items]
        # ``conn.transaction`` so a mid-batch failure rolls back cleanly.
        # The original ``add`` had implicit per-row transactions; the bulk
        # path needs an explicit one to retain the same safety.
        for attempt in (1, 2):
            try:
                async with self._connection(pool) as conn:
                    async with conn.transaction():
                        extra = await columns(conn) if columns is not None else {}
                        sql = self._upsert_sql({name: cast for name, (cast, _) in extra.items()})
                        rows = [
                            row + [values[i] for _, values in extra.values()]
                            for i, row in enumerate(base_rows)
                        ]
                        await conn.executemany(sql, rows)
                return
            except Exception as exc:
                if attempt == 2 or not recreate_if_missing or not self._table_is_missing(exc):
                    raise
                await self._ensure_table(pool)  # another process dropped it: create it again

    async def search(
        self, query_embedding: List[float], limit: int, metadata_filter: Dict[str, Any]
    ) -> List[tuple]:
        pool = await self._get_pool()
        id_col = self._schema.id_column
        aux_keys = self._aux_keys()
        where_parts: List[str] = []
        params: List[Any] = []
        for k, v in (metadata_filter or {}).items():
            if k in self._schema.metadata_columns:
                params.append(v)
                where_parts.append(f"{k} = ${len(params)}")
        params.extend([query_embedding, query_embedding, limit])
        where = (" WHERE " + " AND ".join(where_parts) + " ") if where_parts else " "
        i1, i2, i3 = len(params) - 2, len(params) - 1, len(params)
        sql = (
            f"SELECT {id_col}, {', '.join(aux_keys)}, (embedding <=> ${i1}) AS distance "
            f"FROM {self._collection_name}{where}ORDER BY embedding <=> ${i2} LIMIT ${i3}"
        )
        try:
            async with self._connection(pool) as conn:
                rows = await conn.fetch(sql, *params)
        except Exception as exc:
            if not self._table_is_missing(exc):
                raise
            return []  # nothing to find
        return [tuple(r) for r in rows]

    async def get(self, id: str, tenant_id: str = "", instance_id: str = "") -> Optional[Dict[str, Any]]:
        pool = await self._get_pool()
        id_col = self._schema.id_column
        meta_keys = self._meta_keys()
        aux_keys = self._aux_keys()
        cols = [id_col] + meta_keys + aux_keys
        scope = self._scope_cols()
        scope_vals = []
        if "tenant_id" in scope:
            scope_vals.append(tenant_id)
        if "instance_id" in scope:
            scope_vals.append(instance_id)
        if scope and any(scope_vals):
            where_parts = [f"{c} = ${i + 1}" for i, c in enumerate(scope)]
            where_parts.append(f"{id_col} = ${len(scope) + 1}")
            sql, args = (
                f"SELECT {', '.join(cols)} FROM {self._collection_name} WHERE {' AND '.join(where_parts)}",
                [*scope_vals, id],
            )
        else:
            sql, args = f"SELECT {', '.join(cols)} FROM {self._collection_name} WHERE {id_col} = $1", [id]
        try:
            async with self._connection(pool) as conn:
                row = await conn.fetchrow(sql, *args)
        except Exception as exc:
            if not self._table_is_missing(exc):
                raise
            return None
        if not row:
            return None
        return dict(row)

    async def delete(self, id: str, tenant_id: str = "", instance_id: str = "") -> None:
        pool = await self._get_pool()
        id_col = self._schema.id_column
        scope = self._scope_cols()
        scope_vals = []
        if "tenant_id" in scope:
            scope_vals.append(tenant_id)
        if "instance_id" in scope:
            scope_vals.append(instance_id)
        if scope and any(scope_vals):
            where_parts = [f"{c} = ${i + 1}" for i, c in enumerate(scope)]
            where_parts.append(f"{id_col} = ${len(scope) + 1}")
            sql, args = (
                f"DELETE FROM {self._collection_name} WHERE {' AND '.join(where_parts)}",
                [*scope_vals, id],
            )
        else:
            sql, args = f"DELETE FROM {self._collection_name} WHERE {id_col} = $1", [id]
        try:
            async with self._connection(pool) as conn:
                await conn.execute(sql, *args)
        except Exception as exc:
            if not self._table_is_missing(exc):
                raise

    async def list(self, metadata_filter: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
        pool = await self._get_pool()
        meta_keys = self._meta_keys()
        aux_keys = self._aux_keys()
        cols = [self._schema.id_column] + meta_keys + aux_keys
        where_parts: List[str] = []
        params: List[Any] = []
        for k, v in (metadata_filter or {}).items():
            if k in self._schema.metadata_columns:
                params.append(v)
                where_parts.append(f"{k} = ${len(params)}")
        params.append(limit)
        where = (" WHERE " + " AND ".join(where_parts) + " ") if where_parts else " "
        try:
            async with self._connection(pool) as conn:
                rows = await conn.fetch(
                    f"SELECT {', '.join(cols)} FROM {self._collection_name}{where}LIMIT ${len(params)}",
                    *params,
                )
        except Exception as exc:
            if not self._table_is_missing(exc):
                raise
            return []  # nothing to list
        return [dict(row) for row in rows]
