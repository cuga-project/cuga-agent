"""Knowledge engine metadata: documents, tasks, collection config (local SQLite or Postgres)."""

from __future__ import annotations

from pathlib import Path

from cuga.backend.knowledge.metadata.base import KnowledgeMetadataStore
from cuga.backend.knowledge.metadata.postgres_store import (
    PostgresKnowledgeMetadata,
    truncate_knowledge_metadata_tables,
)
from cuga.backend.knowledge.metadata.sqlite_store import MetadataDB, SqliteKnowledgeMetadata


def create_knowledge_metadata(persist_dir: Path, *, mode: str, postgres_url: str) -> KnowledgeMetadataStore:
    persist_dir.mkdir(parents=True, exist_ok=True)
    if (mode or "local").lower() == "prod":
        if not (postgres_url or "").strip():
            raise ValueError("storage.postgres_url is required when storage.mode=prod")
        return PostgresKnowledgeMetadata(postgres_url.strip())
    return SqliteKnowledgeMetadata(persist_dir / "metadata.db")


__all__ = [
    "KnowledgeMetadataStore",
    "MetadataDB",
    "PostgresKnowledgeMetadata",
    "SqliteKnowledgeMetadata",
    "create_knowledge_metadata",
    "truncate_knowledge_metadata_tables",
]
