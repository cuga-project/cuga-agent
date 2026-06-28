"""Abstract vector store interface for the knowledge engine."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from langchain_core.documents import Document


class VectorStoreAdapter(ABC):
    """The knowledge engine interacts ONLY through this interface."""

    @abstractmethod
    def add_documents(
        self,
        documents: list[Document],
        stage_timings: dict[str, Any] | None = None,
    ) -> dict[str, int]:
        """Insert documents. Returns ``{"num_added": N, "num_skipped": M}``.

        When ``stage_timings`` is provided, implementations may populate
        it with per-stage timings (e.g. ``embed_s``, ``insert_s``) — the
        engine uses this to render the granular ingest progress bar.
        Implementations may ignore it.
        """

    @abstractmethod
    def search(self, query: str, k: int = 10) -> list[tuple[Document, float]]:
        """Dense similarity search; scores in [0, 1], higher is more similar."""

    def search_lexical(self, query: str, k: int = 10) -> list[tuple[Document, float]]:
        """BM25 / lexical retrieval companion to :meth:`search`.

        Default implementation returns ``[]`` — adapters that don't have
        a lexical index (or haven't backfilled an existing collection)
        degrade gracefully to dense-only when the engine fuses them. NOT
        abstract: adding a new adapter shouldn't be forced into
        implementing a BM25 index right away; ``[]`` is a correct answer
        for "no lexical signal available."
        """
        return []

    @abstractmethod
    def delete_by_source(self, source_id: str) -> None:
        """Delete all chunks whose metadata ``source`` matches ``source_id``."""

    @abstractmethod
    def drop(self) -> None:
        """Drop the entire collection/table."""
