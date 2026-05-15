"""Unit tests for the unknown slash-command resolver (PRD #13, slice #20)."""

import asyncio
from typing import Any, Dict, List, Optional

from cuga.backend.slash_commands.command_resolver import (
    CommandResolver,
    CommandSuggestion,
    _cosine,
)
from cuga.backend.slash_commands.types import CommandRef


# --------------------------------------------------------------------------
# Deterministic stubs
# --------------------------------------------------------------------------

# Hand-chosen canned vectors. Cosine similarity ignores magnitude, so the
# direction of each vector fully determines the ranking. The query strings
# are mapped so that ranking is completely predictable.
_CANNED_VECTORS: Dict[str, List[float]] = {
    # commands
    "summarize: condense text": [1.0, 0.0, 0.0],
    "deploy: ship the app": [0.0, 1.0, 0.0],
    "search: find things": [0.0, 0.0, 1.0],
    # queries
    "sumarize": [0.9, 0.1, 0.0],  # closest to summarize
    "deploi": [0.1, 0.9, 0.0],  # closest to deploy
    "zero": [0.0, 0.0, 0.0],  # zero-norm query
}


async def _stub_embed_fn(text: str) -> List[float]:
    """Deterministic embedder: known strings map to canned vectors."""
    if text in _CANNED_VECTORS:
        return list(_CANNED_VECTORS[text])
    # Unknown strings get an orthogonal-ish vector so they never collide.
    return [0.0, 0.0, 0.0]


class StubEmbeddingStore:
    """In-memory deterministic EmbeddingStoreBackend stub."""

    def __init__(self) -> None:
        self.rows: Dict[str, Dict[str, Any]] = {}

    async def add(self, id: str, embedding: List[float], metadata: Dict[str, Any]) -> None:
        self.rows[id] = {"embedding": embedding, "metadata": dict(metadata)}

    async def search(
        self,
        query_embedding: List[float],
        limit: int,
        metadata_filter: Dict[str, Any],
    ) -> List[tuple]:
        return []

    async def get(self, id: str) -> Optional[Dict[str, Any]]:
        return self.rows.get(id)

    async def delete(self, id: str) -> None:
        self.rows.pop(id, None)

    async def list(self, metadata_filter: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
        return [dict(v["metadata"]) for v in list(self.rows.values())[:limit]]


_COMMANDS = [
    CommandRef(name="summarize", description="condense text", kind="builtin"),
    CommandRef(name="deploy", description="ship the app", kind="skill"),
    CommandRef(name="search", description="find things", kind="builtin"),
]


def _build_resolver(commands=_COMMANDS) -> CommandResolver:
    store = StubEmbeddingStore()
    resolver = CommandResolver(store=store, embed_fn=_stub_embed_fn)
    asyncio.run(resolver.index(commands))
    return resolver


# --------------------------------------------------------------------------
# _cosine helper
# --------------------------------------------------------------------------


def test_cosine_identical_vectors_is_one():
    assert _cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == 1.0


def test_cosine_orthogonal_vectors_is_zero():
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_cosine_zero_norm_vector_is_zero():
    assert _cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
    assert _cosine([1.0, 1.0], [0.0, 0.0]) == 0.0


# --------------------------------------------------------------------------
# resolve()
# --------------------------------------------------------------------------


def test_top_k_ranking_by_cosine_similarity():
    resolver = _build_resolver()
    results = asyncio.run(resolver.resolve("sumarize"))
    # summarize is the closest command to the "sumarize" query vector.
    assert results[0].name == "summarize"
    # scores are sorted descending.
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


def test_typo_ranks_intended_command_above_unrelated():
    resolver = _build_resolver()
    results = asyncio.run(resolver.resolve("sumarize"))
    names = [r.name for r in results]
    assert names.index("summarize") < names.index("deploy")
    assert names.index("summarize") < names.index("search")


def test_limit_is_respected():
    resolver = _build_resolver()
    results = asyncio.run(resolver.resolve("sumarize", limit=1))
    assert len(results) == 1
    assert results[0].name == "summarize"


def test_threshold_filtering():
    resolver = _build_resolver()
    # "sumarize" ~ summarize is high (~0.99); deploy/search are ~0.
    results = asyncio.run(resolver.resolve("sumarize", threshold=0.5))
    assert [r.name for r in results] == ["summarize"]


def test_exact_match_short_circuit_returns_score_one():
    resolver = _build_resolver()
    results = asyncio.run(resolver.resolve("summarize"))
    assert len(results) == 1
    assert results[0].name == "summarize"
    assert results[0].score == 1.0
    assert results[0].kind == "builtin"


def test_exact_match_is_case_insensitive_and_stripped():
    resolver = _build_resolver()
    results = asyncio.run(resolver.resolve("  SUMMARIZE  "))
    assert len(results) == 1
    assert results[0].name == "summarize"
    assert results[0].score == 1.0


def test_empty_registry_returns_empty_list():
    resolver = _build_resolver(commands=[])
    assert asyncio.run(resolver.resolve("anything")) == []


def test_never_returns_the_input_itself():
    # A typo query never echoes the raw input back as a suggestion: the
    # input string itself is never one of the ranked candidate names.
    resolver = _build_resolver()
    for query in ("sumarize", "deploi", "totally-unrelated"):
        results = asyncio.run(resolver.resolve(query))
        assert all(r.name != query for r in results)


def test_suggestion_carries_kind_and_description():
    resolver = _build_resolver()
    results = asyncio.run(resolver.resolve("deploi"))
    top = results[0]
    assert isinstance(top, CommandSuggestion)
    assert top.name == "deploy"
    assert top.kind == "skill"
    assert top.description == "ship the app"
