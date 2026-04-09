"""Knowledge vectors using :class:`ProdEmbeddingStore` (PostgreSQL + pgvector)."""

from __future__ import annotations

from langchain_core.embeddings import Embeddings

from cuga.backend.knowledge.storage.adapter import StorageBackedKnowledgeVectorStore
from cuga.backend.knowledge.vector_store_base import VectorStoreAdapter


def create_storage_prod_knowledge_store(
    collection: str, embeddings: Embeddings, postgres_url: str
) -> VectorStoreAdapter:
    if not postgres_url:
        raise ValueError("postgres_url is required for storage_prod knowledge vector store")
    return StorageBackedKnowledgeVectorStore(
        collection, embeddings, local_db_path="", postgres_url=postgres_url
    )
