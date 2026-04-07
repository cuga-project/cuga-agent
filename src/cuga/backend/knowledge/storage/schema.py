"""Embedding schema for knowledge chunks (sqlite-vec + pgvector via storage/embedding)."""

from __future__ import annotations

from cuga.backend.storage.embedding.base import EmbeddingSchemaConfig


def knowledge_embedding_schema(embedding_dim: int) -> EmbeddingSchemaConfig:
    """Columns aligned with policy-style storage: tenant/instance scope + chunk metadata.

    ``meta_json`` holds serialized {source, filename, page} so vector search can return
    display fields without changing shared LocalEmbeddingStore SELECT shape for policy
    (policy rows stay id, auxiliary..., distance).
    """
    return EmbeddingSchemaConfig(
        embedding_dim=embedding_dim,
        id_column="id",
        metadata_columns={
            "id": "text",
            "tenant_id": "text",
            "instance_id": "text",
            "source": "text",
            "filename": "text",
            "page": "integer",
        },
        auxiliary_columns={
            "chunk_text": "text",
            "meta_json": "text",
        },
    )
