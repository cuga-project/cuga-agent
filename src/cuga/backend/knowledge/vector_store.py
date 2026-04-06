"""Vector store abstraction for the knowledge engine.

Provides a unified interface over multiple LangChain vector store backends.
The engine calls ONLY VectorStoreAdapter methods — never backend-specific APIs.

Supported backends:
    - sqlite  : SQLiteVec (local, zero-dependency default)
    - milvus  : Milvus / Milvus Lite (local file or remote server)
    - pgvector: PostgreSQL + pgvector (production, requires PostgreSQL)

Adding a new backend:
    1. Create a class inheriting from VectorStoreAdapter
    2. Implement all abstract methods
    3. Register it in BACKENDS dict at the bottom of this file
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from loguru import logger as loguru_logger

logger = loguru_logger


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class VectorStoreAdapter(ABC):
    """Unified interface for vector store operations.

    The knowledge engine interacts ONLY through this interface.
    Backend-specific logic is encapsulated in each subclass.
    """

    @abstractmethod
    def add_documents(self, documents: list[Document]) -> dict[str, int]:
        """Insert documents into the store.

        Returns:
            {"num_added": N, "num_skipped": M}
        """

    @abstractmethod
    def search(self, query: str, k: int = 10) -> list[tuple[Document, float]]:
        """Similarity search.

        Returns:
            List of (document, similarity_score) tuples.
            Score is normalized to [0, 1] where 1 = most similar.
            Uses LangChain's built-in relevance score normalization when available.
        """

    @abstractmethod
    def delete_by_source(self, source_id: str) -> None:
        """Delete all documents matching a source identifier.

        Args:
            source_id: The source metadata value (e.g., "collection/filename").
        """

    @abstractmethod
    def drop(self) -> None:
        """Drop the entire collection/table. Irreversible."""


# ---------------------------------------------------------------------------
# SQLiteVec backend (local default)
# ---------------------------------------------------------------------------


class SQLiteVecStore(VectorStoreAdapter):
    """SQLiteVec-backed vector store. File-based, zero external dependencies."""

    def __init__(self, collection: str, embeddings: Embeddings, persist_dir: Path, **kwargs: Any):
        import sqlite3

        import sqlite_vec

        self._db_file = str(persist_dir / "knowledge_vectors.db")
        self._collection = collection

        conn = sqlite3.connect(self._db_file, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.execute("PRAGMA journal_mode=WAL")
        self._conn = conn

        from langchain_community.vectorstores import SQLiteVec

        self._store = SQLiteVec(
            table=collection,
            connection=conn,
            embedding=embeddings,
            db_file=self._db_file,
        )

        # SQLiteVec doesn't implement _select_relevance_score_fn.
        # sqlite-vec returns L2 distance. Use LangChain's built-in L2→similarity.
        self._relevance_score_fn = self._store.__class__._euclidean_relevance_score_fn

    # -- VectorStoreAdapter implementation --

    def add_documents(self, documents: list[Document]) -> dict[str, int]:
        ids = self._store.add_documents(documents)
        return {"num_added": len(ids), "num_skipped": 0}

    def search(self, query: str, k: int = 10) -> list[tuple[Document, float]]:
        raw = self._store.similarity_search_with_score(query, k=k)
        return [(doc, self._relevance_score_fn(score)) for doc, score in raw]

    def delete_by_source(self, source_id: str) -> None:
        """Delete by source metadata (stored as JSON in metadata column)."""
        rows = self._conn.execute(
            f"SELECT rowid FROM {self._collection} WHERE json_extract(metadata, '$.source') = ?",
            (source_id,),
        ).fetchall()
        if not rows:
            return
        ids = [r["rowid"] for r in rows]
        placeholders = ",".join("?" * len(ids))
        self._conn.execute(f"DELETE FROM {self._collection} WHERE rowid IN ({placeholders})", ids)
        self._conn.execute(f"DELETE FROM {self._collection}_vec WHERE rowid IN ({placeholders})", ids)
        self._conn.commit()

    def drop(self) -> None:
        self._conn.execute(f"DROP TRIGGER IF EXISTS {self._collection}_embed_text")
        self._conn.execute(f"DROP TABLE IF EXISTS {self._collection}_vec")
        self._conn.execute(f"DROP TABLE IF EXISTS {self._collection}")
        self._conn.commit()


# ---------------------------------------------------------------------------
# Milvus backend (local Milvus Lite or remote Milvus server)
# ---------------------------------------------------------------------------


class MilvusStore(VectorStoreAdapter):
    """Milvus-backed vector store. Supports Milvus Lite (file) and remote server."""

    def __init__(
        self,
        collection: str,
        embeddings: Embeddings,
        persist_dir: Path,
        metric_type: str = "COSINE",
        **kwargs: Any,
    ):
        from langchain_milvus import Milvus

        self._uri = str(persist_dir / "knowledge.db")
        self._collection = collection

        self._store = Milvus(
            embedding_function=embeddings,
            collection_name=collection,
            connection_args={"uri": self._uri},
            auto_id=True,
            index_params={
                "metric_type": metric_type,
                "index_type": "AUTOINDEX",
                "params": {},
            },
        )

    # -- VectorStoreAdapter implementation --

    def add_documents(self, documents: list[Document]) -> dict[str, int]:
        ids = self._store.add_documents(documents)
        return {"num_added": len(ids), "num_skipped": 0}

    def search(self, query: str, k: int = 10) -> list[tuple[Document, float]]:
        # Milvus implements _select_relevance_score_fn — use LangChain's normalization
        return self._store.similarity_search_with_relevance_scores(query, k=k)

    def delete_by_source(self, source_id: str) -> None:
        try:
            self._store.delete(expr=f'source == "{source_id}"')
        except Exception as e:
            logger.debug(f"Milvus delete for {source_id}: {e}")

    def drop(self) -> None:
        from pymilvus import MilvusClient

        client = MilvusClient(self._uri)
        if client.has_collection(self._collection):
            client.drop_collection(self._collection)


# ---------------------------------------------------------------------------
# PGVector backend (production — requires running PostgreSQL)
# ---------------------------------------------------------------------------


class PGVectorStore(VectorStoreAdapter):
    """PostgreSQL + pgvector backed vector store. Production-grade."""

    def __init__(self, collection: str, embeddings: Embeddings, connection_string: str = "", **kwargs: Any):
        if not connection_string:
            raise ValueError(
                "pgvector_connection_string is required for pgvector backend. Set it in knowledge settings."
            )
        from langchain_postgres import PGVector

        self._store = PGVector(
            embeddings=embeddings,
            collection_name=collection,
            connection=connection_string,
        )

    # -- VectorStoreAdapter implementation --

    def add_documents(self, documents: list[Document]) -> dict[str, int]:
        ids = self._store.add_documents(documents)
        return {"num_added": len(ids), "num_skipped": 0}

    def search(self, query: str, k: int = 10) -> list[tuple[Document, float]]:
        # Use LangChain's built-in normalization if available, fallback to raw scores
        try:
            return self._store.similarity_search_with_relevance_scores(query, k=k)
        except NotImplementedError:
            return self._store.similarity_search_with_score(query, k=k)

    def delete_by_source(self, source_id: str) -> None:
        self._store.delete(filter={"source": source_id})

    def drop(self) -> None:
        self._store.delete_collection()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

BACKENDS: dict[str, type[VectorStoreAdapter]] = {
    "sqlite": SQLiteVecStore,
    "milvus": MilvusStore,
    "pgvector": PGVectorStore,
}


def create_vector_store(
    backend: str,
    collection: str,
    embeddings: Embeddings,
    persist_dir: Path,
    metric_type: str = "COSINE",
    pgvector_connection_string: str = "",
    **kwargs: Any,
) -> VectorStoreAdapter:
    """Create a vector store adapter for the specified backend.

    Args:
        backend: One of "sqlite", "milvus", "pgvector".
        collection: Collection/table name.
        embeddings: LangChain Embeddings instance.
        persist_dir: Directory for file-based stores (sqlite, milvus).
        metric_type: Distance metric (COSINE, IP, L2).
        pgvector_connection_string: PostgreSQL connection string (pgvector only).

    Returns:
        VectorStoreAdapter instance.

    Raises:
        ValueError: If backend is unknown.
    """
    adapter_cls = BACKENDS.get(backend)
    if adapter_cls is None:
        raise ValueError(f"Unknown vector_store backend: '{backend}'. Available: {sorted(BACKENDS.keys())}")

    if backend == "pgvector":
        adapter = adapter_cls(
            collection=collection,
            embeddings=embeddings,
            connection_string=pgvector_connection_string,
        )
        logger.info(f"Vector store created: backend={backend}, collection={collection}")
        return adapter

    adapter = adapter_cls(
        collection=collection,
        embeddings=embeddings,
        persist_dir=persist_dir,
        metric_type=metric_type,
    )
    logger.info(f"Vector store created: backend={backend}, collection={collection}, path={persist_dir}")
    return adapter
