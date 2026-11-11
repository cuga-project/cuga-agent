"""Shared integration-test helpers (import-safe under pytest importlib mode)."""

from __future__ import annotations

import uuid


def unique_collection(prefix: str = "test") -> str:
    """Generate a unique collection name matching adapter regex ``[A-Za-z0-9_]{1,63}``."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class DeterministicEmbeddings:
    """Offline LangChain ``Embeddings`` stub: stable 384-dim vectors, no model download.

    For tests that exercise storage and keyword retrieval, where embedding quality is irrelevant.
    """

    dim = 384

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[float((i + j) % 7) / 7.0 for j in range(self.dim)] for i in range(len(texts))]

    def embed_query(self, text: str) -> list[float]:
        return [0.1] * self.dim
