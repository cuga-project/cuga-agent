"""Knowledge vectors using :class:`LocalEmbeddingStore` (sqlite-vec in ``knowledge_vectors.db`` under persist_dir)."""

from __future__ import annotations

from langchain_core.embeddings import Embeddings

from cuga.backend.knowledge.storage.adapter import StorageBackedKnowledgeVectorStore
from cuga.backend.knowledge.vector_store_base import VectorStoreAdapter


def create_storage_local_knowledge_store(
    collection: str, embeddings: Embeddings, local_db_path: str
) -> VectorStoreAdapter:
    return StorageBackedKnowledgeVectorStore(
        collection, embeddings, local_db_path=local_db_path, postgres_url=""
    )
