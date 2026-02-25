from typing import Any, Dict, List, Optional

from cuga.backend.storage.embedding.base import EmbeddingSchemaConfig


def _pg_type(s: str) -> str:
    m = {"text": "TEXT", "integer": "BIGINT", "boolean": "BOOLEAN", "float": "DOUBLE PRECISION"}
    return m.get(s.lower(), "TEXT")


class ProdEmbeddingStore:
    def __init__(self, postgres_url: str, collection_name: str, schema: EmbeddingSchemaConfig):
        self._postgres_url = postgres_url
        self._collection_name = collection_name
        self._schema = schema
        self._conn: Any = None

    def _get_conn(self):
        if self._conn is None or getattr(self._conn, "closed", True):
            import psycopg

            try:
                from pgvector.psycopg import register_vector
            except ImportError:
                raise ImportError("pgvector is required for storage.mode=prod. Install with: uv add pgvector")
            self._conn = psycopg.connect(self._postgres_url)
            self._conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            register_vector(self._conn)
            self._ensure_table()
        return self._conn

    def _ensure_table(self) -> None:
        dim = self._schema.embedding_dim
        id_col = self._schema.id_column
        meta = self._schema.metadata_columns
        aux = self._schema.auxiliary_columns
        parts = [f"{id_col} TEXT PRIMARY KEY", f"embedding vector({dim})"]
        for k, v in meta.items():
            if k == id_col:
                continue
            parts.append(f"{k} {_pg_type(v)}")
        for k, v in aux.items():
            parts.append(f"{k} {_pg_type(v)}")
        create_sql = f"CREATE TABLE IF NOT EXISTS {self._collection_name} ({', '.join(parts)})"
        with self._conn.cursor() as cur:
            cur.execute(create_sql)
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{self._collection_name}_embedding "
                f"ON {self._collection_name} USING hnsw (embedding vector_cosine_ops)"
            )
        self._conn.commit()

    def _meta_keys(self) -> List[str]:
        return list(self._schema.metadata_columns.keys())

    def _aux_keys(self) -> List[str]:
        return list(self._schema.auxiliary_columns.keys())

    def add(self, id: str, embedding: List[float], metadata: Dict[str, Any]) -> None:
        conn = self._get_conn()
        meta_keys = self._meta_keys()
        aux_keys = self._aux_keys()
        full = {self._schema.id_column: id, **metadata}
        cols = ["embedding"] + meta_keys + aux_keys
        placeholders = ", ".join("%s" for _ in cols)
        col_list = ", ".join(cols)
        values = [embedding] + [full.get(k) for k in meta_keys] + [full.get(k) for k in aux_keys]
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {self._collection_name} ({col_list}) VALUES ({placeholders}) "
                f"ON CONFLICT ({self._schema.id_column}) DO UPDATE SET "
                + ", ".join(f"{c} = EXCLUDED.{c}" for c in ["embedding"] + meta_keys + aux_keys),
                values,
            )
        conn.commit()

    def search(
        self, query_embedding: List[float], limit: int, metadata_filter: Dict[str, Any]
    ) -> List[tuple]:
        conn = self._get_conn()
        id_col = self._schema.id_column
        aux_keys = self._aux_keys()
        where_parts: List[str] = []
        params: List[Any] = []
        for k, v in (metadata_filter or {}).items():
            if k in self._schema.metadata_columns:
                where_parts.append(f"{k} = %s")
                params.append(v)
        params.extend([query_embedding, query_embedding, limit])
        where = (" WHERE " + " AND ".join(where_parts) + " ") if where_parts else " "
        sql = (
            f"SELECT {id_col}, {', '.join(aux_keys)}, (embedding <=> %s) AS distance "
            f"FROM {self._collection_name}{where}ORDER BY embedding <=> %s LIMIT %s"
        )
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [tuple(r) for r in rows]

    def get(self, id: str) -> Optional[Dict[str, Any]]:
        conn = self._get_conn()
        id_col = self._schema.id_column
        meta_keys = self._meta_keys()
        aux_keys = self._aux_keys()
        cols = [id_col] + meta_keys + aux_keys
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {', '.join(cols)} FROM {self._collection_name} WHERE {id_col} = %s",
                (id,),
            )
            row = cur.fetchone()
        if not row:
            return None
        return dict(zip(cols, row))

    def delete(self, id: str) -> None:
        conn = self._get_conn()
        id_col = self._schema.id_column
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM {self._collection_name} WHERE {id_col} = %s", (id,))
        conn.commit()

    def list(self, metadata_filter: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        meta_keys = self._meta_keys()
        aux_keys = self._aux_keys()
        cols = [self._schema.id_column] + meta_keys + aux_keys
        where_parts: List[str] = []
        params: List[Any] = []
        for k, v in (metadata_filter or {}).items():
            if k in self._schema.metadata_columns:
                where_parts.append(f"{k} = %s")
                params.append(v)
        params.append(limit)
        where = (" WHERE " + " AND ".join(where_parts) + " ") if where_parts else " "
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {', '.join(cols)} FROM {self._collection_name}{where}LIMIT %s",
                params,
            )
            rows = cur.fetchall()
        return [dict(zip(cols, row)) for row in rows]
