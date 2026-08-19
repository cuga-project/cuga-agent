"""Cosine prefilter, then LLM rerank.

Cosine cuts a large catalogue down cheaply (high recall, poor precision on
near-identical CRUD siblings); the LLM then makes the final selection with full
schemas in front of it, on a pool small enough to be affordable.

If the embedding leg is unavailable — model still downloading, fastembed
missing — this degrades to plain LLM shortlisting, which is exactly today's
behavior, so the degraded path is already well covered by existing tests.
"""

from __future__ import annotations

import dataclasses
from typing import Any, ClassVar, List

from loguru import logger

from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.base import (
    ShortlistRequest,
    ShortlistResult,
    ShortlisterUnavailableError,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.embedding import EmbeddingShortlister
from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.llm import LLMShortlister


class HybridShortlister:
    """Embedding prefilter to ``top_k``, then :class:`LLMShortlister` ranks."""

    name: ClassVar[str] = "hybrid"

    def __init__(self, embedding: EmbeddingShortlister, llm: LLMShortlister) -> None:
        self._embedding = embedding
        self._llm = llm

    async def warm(self, tools: List[Any]) -> int:
        """Warm the embedding leg; the LLM leg has nothing to preload."""
        return await self._embedding.warm(tools)

    async def shortlist(self, request: ShortlistRequest) -> ShortlistResult:
        if not request.tools:
            return ShortlistResult()

        pool: List[Any] = request.tools

        # Prefilter only when there is something to cut. At or below the cut
        # width the LLM would see the same set either way.
        width = request.top_k or 0
        if width and len(pool) > width:
            try:
                prefiltered = await self._embedding.shortlist(
                    dataclasses.replace(
                        request,
                        top_k=width,
                        max_results=None,  # the cut width governs here, not the render cap
                    )
                )
                by_name = {t.name: t for t in pool}
                narrowed = [by_name[c.name] for c in prefiltered.candidates if c.name in by_name]
                if narrowed:
                    logger.debug("Hybrid shortlister: cosine cut {} tools to {}", len(pool), len(narrowed))
                    pool = narrowed
            except ShortlisterUnavailableError as e:
                logger.warning(
                    "Hybrid shortlister: embedding leg unavailable ({}); ranking all {} tools "
                    "with the LLM for this call",
                    e,
                    len(pool),
                )

        return await self._llm.shortlist(dataclasses.replace(request, tools=pool))
