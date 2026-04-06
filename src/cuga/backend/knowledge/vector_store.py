"""Vector store factory for the knowledge engine.

Backends:
    - storage_local: sqlite-vec via ``storage/embedding/local.py`` (tenant/instance scoped)
    - storage_prod: pgvector via ``storage/embedding/prod.py`` (Postgres URL from settings
      or ``knowledge.pgvector_connection_string``)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.embeddings import Embeddings
from loguru import logger as loguru_logger

from cuga.backend.knowledge.vector_store_base import VectorStoreAdapter

logger = loguru_logger


def create_vector_store(
    backend: str,
    collection: str,
    embeddings: Embeddings,
    persist_dir: Path,
    metric_type: str = "COSINE",
    pgvector_connection_string: str = "",
    **kwargs: Any,
) -> VectorStoreAdapter:
    """Create a vector store adapter.

    Args:
        backend: ``storage_local`` or ``storage_prod``.
        collection: Table/collection name.
        embeddings: LangChain embeddings.
        persist_dir: Unused; kept for API stability.
        metric_type: Unused; kept for API stability.
        pgvector_connection_string: Optional Postgres URL for ``storage_prod`` when
            ``storage.postgres_url`` is not set (legacy ``knowledge.pgvector_connection_string``).
    """
    if backend == "storage_local":
        from cuga.backend.storage.facade import get_storage_connection_params

        _mode, local_path, _pg = get_storage_connection_params()
        from cuga.backend.knowledge.storage.local import create_storage_local_knowledge_store

        adapter = create_storage_local_knowledge_store(collection, embeddings, local_path)
        logger.info(
            "Vector store created: backend=storage_local, collection={}, db={}",
            collection,
            local_path,
        )
        return adapter

    if backend == "storage_prod":
        from cuga.backend.storage.facade import get_storage_connection_params

        _mode, _local_path, settings_pg = get_storage_connection_params()
        pg_url = (pgvector_connection_string or "").strip() or (settings_pg or "").strip()
        if not pg_url:
            raise ValueError(
                "storage_prod requires a Postgres URL: set storage.postgres_url or "
                "knowledge.pgvector_connection_string in settings."
            )
        from cuga.backend.knowledge.storage.prod import create_storage_prod_knowledge_store

        adapter = create_storage_prod_knowledge_store(collection, embeddings, pg_url)
        logger.info("Vector store created: backend=storage_prod, collection={}", collection)
        return adapter

    raise ValueError(f"Unknown vector_store backend: {backend!r}. Use storage_local or storage_prod.")
