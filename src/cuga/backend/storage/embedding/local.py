import sqlite3
from typing import Any, Dict, List, Optional

from cuga.backend.storage.embedding.base import EmbeddingSchemaConfig

_VEC0_TYPE = {"text": "TEXT", "integer": "INTEGER", "boolean": "BOOLEAN", "float": "FLOAT"}


def _serialize_float32(embedding: List[float]):
    try:
        from sqlite_vec import serialize_float32

        return serialize_float32(embedding)
    except ImportError:
        import struct

        return struct.pack(f"{len(embedding)}f", *embedding)


class LocalEmbeddingStore:
    def __init__(self, db_path: str, collection_name: str, schema: EmbeddingSchemaConfig):
        self._db_path = db_path
        self._collection_name = collection_name
        self._schema = schema
        self._conn: Optional[sqlite3.Connection] = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path)
            self._conn.enable_load_extension(True)
            try:
                import sqlite_vec

                sqlite_vec.load(self._conn)
            finally:
                self._conn.enable_load_extension(False)
            self._ensure_table()
        return self._conn

    def _ensure_table(self) -> None:
        meta = self._schema.metadata_columns
        aux = self._schema.auxiliary_columns
        dim = self._schema.embedding_dim
        parts = [f"embedding float[{dim}]"]
        for k, v in meta.items():
            typ = _VEC0_TYPE.get(v.lower(), "TEXT")
            parts.append(f"{k} {typ}")
        for k, v in aux.items():
            typ = _VEC0_TYPE.get(v.lower(), "TEXT")
            parts.append(f"+{k} {typ}")
        cols = ", ".join(parts)
        sql = f"CREATE VIRTUAL TABLE IF NOT EXISTS {self._collection_name} USING vec0({cols})"
        self._conn.execute(sql)
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
        placeholders = ", ".join("?" for _ in cols)
        col_list = ", ".join(cols)
        values = (
            [_serialize_float32(embedding)]
            + [full.get(k) for k in meta_keys]
            + [full.get(k) for k in aux_keys]
        )
        conn.execute(
            f"INSERT INTO {self._collection_name} ({col_list}) VALUES ({placeholders})",
            values,
        )
        conn.commit()

    def search(
        self, query_embedding: List[float], limit: int, metadata_filter: Dict[str, Any]
    ) -> List[tuple]:
        conn = self._get_conn()
        aux_keys = self._aux_keys()
        select_cols = [self._schema.id_column] + aux_keys + ["distance"]
        where_parts = ["embedding MATCH ?", "k = ?"]
        params: List[Any] = [_serialize_float32(query_embedding), limit]
        for k, v in (metadata_filter or {}).items():
            if k in self._schema.metadata_columns:
                where_parts.append(f"{k} = ?")
                params.append(v)
        sql = (
            f"SELECT {', '.join(select_cols)} FROM {self._collection_name} "
            f"WHERE {' AND '.join(where_parts)} ORDER BY distance"
        )
        cur = conn.execute(sql, params)
        return [tuple(row) for row in cur.fetchall()]

    def get(self, id: str) -> Optional[Dict[str, Any]]:
        conn = self._get_conn()
        id_col = self._schema.id_column
        meta_keys = self._meta_keys()
        aux_keys = self._aux_keys()
        cols = [id_col] + meta_keys + aux_keys
        row = conn.execute(
            f"SELECT {', '.join(cols)} FROM {self._collection_name} WHERE {id_col} = ?",
            (id,),
        ).fetchone()
        if not row:
            return None
        return dict(zip(cols, row))

    def delete(self, id: str) -> None:
        conn = self._get_conn()
        id_col = self._schema.id_column
        conn.execute(f"DELETE FROM {self._collection_name} WHERE {id_col} = ?", (id,))
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
                where_parts.append(f"{k} = ?")
                params.append(v)
        params.append(limit)
        where = (" WHERE " + " AND ".join(where_parts) + " ") if where_parts else " "
        sql = f"SELECT {', '.join(cols)} FROM {self._collection_name}{where}LIMIT ?"
        cur = conn.execute(sql, params)
        return [dict(zip(cols, row)) for row in cur.fetchall()]
