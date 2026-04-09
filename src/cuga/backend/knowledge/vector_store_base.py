"""Abstract vector store interface for the knowledge engine."""

from __future__ import annotations

from abc import ABC, abstractmethod

from langchain_core.documents import Document


class VectorStoreAdapter(ABC):
    """The knowledge engine interacts ONLY through this interface."""

    @abstractmethod
    def add_documents(self, documents: list[Document]) -> dict[str, int]:
        """Insert documents. Returns {"num_added": N, "num_skipped": M}."""

    @abstractmethod
    def search(self, query: str, k: int = 10) -> list[tuple[Document, float]]:
        """Similarity search; scores in [0, 1], higher is more similar."""

    @abstractmethod
    def delete_by_source(self, source_id: str) -> None:
        """Delete all chunks whose metadata ``source`` matches ``source_id``."""

    @abstractmethod
    def drop(self) -> None:
        """Drop the entire collection/table."""
