from typing import Any, Dict, List, Optional

from cuga.backend.storage.embedding.base import EmbeddingSchemaConfig

SCOPE_COLS = ["tenant_id", "instance_id"]


def _placeholders(n: int) -> str:
    return ", ".join(f"${i + 1}" for i in range(n))


def _pg_type(s: str) -> str:
    m = {"text": "TEXT", "integer": "BIGINT", "boolean": "BOOLEAN", "float": "DOUBLE PRECISION"}
    return m.get(s.lower(), "TEXT")


class ProdEmbeddingStore:
    def __init__(self, postgres_url: str, collection_name: str, schema: EmbeddingSchemaConfig):
        self._postgres_url = postgres_url
        self._collection_name = collection_name
        self._schema = schema
        self._pool: Any = None

    async def _get_pool(self):
        if self._pool is None:
            import asyncpg

            try:
                from pgvector.asyncpg import register_vector
            except ImportError:
                raise ImportError("pgvector is required for storage.mode=prod. Install with: uv add pgvector")

            self._pool = await asyncpg.create_pool(
                self._postgres_url,
                min_size=1,
                max_size=4,
                command_timeout=60,
            )
            async with self._pool.acquire() as conn:
                await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
                await register_vector(conn)
            await self._ensure_table()
        return self._pool

    def _scope_cols(self) -> List[str]:
        meta = self._schema.metadata_columns
        return [c for c in SCOPE_COLS if c in meta]

    async def _ensure_table(self) -> None:
        pool = self._pool
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
        async with pool.acquire() as conn:
            await conn.execute(create_sql)
            await conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{self._collection_name}_embedding "
                f"ON {self._collection_name} USING hnsw (embedding vector_cosine_ops)"
            )

    def _meta_keys(self) -> List[str]:
        return list(self._schema.metadata_columns.keys())

    def _aux_keys(self) -> List[str]:
        return list(self._schema.auxiliary_columns.keys())

    async def add(self, id: str, embedding: List[float], metadata: Dict[str, Any]) -> None:
        pool = await self._get_pool()
        id_col = self._schema.id_column
        meta_keys = self._meta_keys()
        aux_keys = self._aux_keys()
        full = {id_col: id, **metadata}
        meta_keys_no_id = [k for k in meta_keys if k != id_col]
        cols = ["embedding", id_col] + meta_keys_no_id + aux_keys
        n = len(cols)
        ph = _placeholders(n)
        col_list = ", ".join(cols)
        values = [embedding, id] + [full.get(k) for k in meta_keys_no_id] + [full.get(k) for k in aux_keys]
        upsert = ", ".join(f"{c} = EXCLUDED.{c}" for c in ["embedding"] + meta_keys + aux_keys)
        scope = self._scope_cols()
        conflict_cols = f"{', '.join(scope + [id_col])}" if scope else id_col
        async with pool.acquire() as conn:
            await conn.execute(
                f"INSERT INTO {self._collection_name} ({col_list}) VALUES ({ph}) "
                f"ON CONFLICT ({conflict_cols}) DO UPDATE SET {upsert}",
                *values,
            )

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
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
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
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    f"SELECT {', '.join(cols)} FROM {self._collection_name} WHERE {' AND '.join(where_parts)}",
                    *scope_vals,
                    id,
                )
        else:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    f"SELECT {', '.join(cols)} FROM {self._collection_name} WHERE {id_col} = $1",
                    id,
                )
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
            async with pool.acquire() as conn:
                await conn.execute(
                    f"DELETE FROM {self._collection_name} WHERE {' AND '.join(where_parts)}",
                    *scope_vals,
                    id,
                )
        else:
            async with pool.acquire() as conn:
                await conn.execute(f"DELETE FROM {self._collection_name} WHERE {id_col} = $1", id)

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
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT {', '.join(cols)} FROM {self._collection_name}{where}LIMIT ${len(params)}",
                *params,
            )
        return [dict(row) for row in rows]
