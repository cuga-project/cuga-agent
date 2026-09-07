from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from collections.abc import Iterable

from .graph import MemoryGraph
from .schemas import MemoryNode, NodeKind


DEFAULT_TOP_K = 5
DEFAULT_MIN_COMBINED_SIMILARITY = 0.45
DEFAULT_LEXICAL_WEIGHT = 0.5
DEFAULT_EMBEDDING_WEIGHT = 0.5

_TOKEN_RE = re.compile(r"[A-Za-z0-9_@.+-]+")

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "has",
    "have",
    "if",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "their",
    "then",
    "this",
    "to",
    "was",
    "were",
    "with",
}

# PropositionPayload is deliberately limited to coarse subject-predicate-object
# lexical-anchor lists. Fine-grained semantics remain authoritative in node.content and
# are handled after retrieval by semantic relation/verification stages.
_LEXICAL_FIELD_WEIGHTS: dict[str, float] = {
    "subject": 4.0,
    "predicate": 3.0,
    "object": 4.0,
    "routing_text": 2.5,
    "content": 1.5,
}

_SAME_SOURCE_BONUS = 0.5

_MAX_LEXICAL_SCORE = (
    sum(_LEXICAL_FIELD_WEIGHTS.values())
    + _SAME_SOURCE_BONUS
)


class RetrievalError(ValueError):
    pass


@dataclass(frozen=True)
class PairRetrievalScore:
    """Deterministic retrieval score for one anchor/candidate pair."""

    lexical_score: float
    embedding_score: float | None
    combined_score: float
    used_embedding: bool
    lexical_components: dict[str, float]


@dataclass(frozen=True)
class RankedNode:
    """One evidence node ranked against an atomic query node."""

    node: MemoryNode
    lexical_score: float
    embedding_score: float | None
    combined_score: float
    used_embedding: bool
    lexical_components: dict[str, float]


def build_retrieval_text(node: MemoryNode) -> str:
    """Return the canonical text used to embed an atomic node.

    V1 deliberately combines the concise retrieval-oriented routing text with
    the full atomic statement. Structured proposition fields remain on the
    lexical side of the hybrid retrieval score.
    """
    return f"{node.routing_text.strip()}\n{node.content.strip()}"


def retrieval_text_hash(text: str) -> str:
    """Return a stable SHA-256 hash for embedded retrieval text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def score_pair(
    anchor: MemoryNode,
    candidate: MemoryNode,
    *,
    lexical_weight: float = DEFAULT_LEXICAL_WEIGHT,
    embedding_weight: float = DEFAULT_EMBEDDING_WEIGHT,
) -> PairRetrievalScore:
    """Score one atomic-node pair for retrieval.

    When compatible cached embeddings exist on both nodes, V1 uses:

        combined = 0.5 * lexical + 0.5 * embedding

    by default.

    During migration, if either node does not yet have an embedding, the
    available lexical signal is re-normalized to full weight instead of
    artificially halving the score.
    """
    _validate_atomic_node(anchor, role="anchor")
    _validate_atomic_node(candidate, role="candidate")
    _validate_signal_weights(
        lexical_weight=lexical_weight,
        embedding_weight=embedding_weight,
    )

    lexical, components = lexical_similarity(
        anchor,
        candidate,
        include_components=True,
    )
    embedding = embedding_similarity(anchor, candidate)

    if embedding is None:
        combined = lexical
        used_embedding = False
    else:
        total_weight = lexical_weight + embedding_weight
        combined = (
            lexical_weight * lexical
            + embedding_weight * embedding
        ) / total_weight
        used_embedding = True

    return PairRetrievalScore(
        lexical_score=lexical,
        embedding_score=embedding,
        combined_score=_clamp01(combined),
        used_embedding=used_embedding,
        lexical_components=components,
    )


def rank_nodes(
    anchor: MemoryNode,
    candidates: Iterable[MemoryNode],
    *,
    top_k: int = DEFAULT_TOP_K,
    min_combined_similarity: float = DEFAULT_MIN_COMBINED_SIMILARITY,
    exclude_node_ids: set[str] | None = None,
    lexical_weight: float = DEFAULT_LEXICAL_WEIGHT,
    embedding_weight: float = DEFAULT_EMBEDDING_WEIGHT,
) -> list[RankedNode]:
    """Rank atomic evidence nodes against one atomic query node.

    Candidates below ``min_combined_similarity`` are discarded before the
    top-k slice, so retrieval may return fewer than ``top_k`` nodes. Ranking
    is deterministic. Ties are broken by node ID.
    """
    _validate_atomic_node(anchor, role="anchor")

    if top_k <= 0:
        raise ValueError("top_k must be positive")
    if not 0.0 <= min_combined_similarity <= 1.0:
        raise ValueError("min_combined_similarity must be between 0 and 1")

    excluded = set(exclude_node_ids or ())
    excluded.add(anchor.id)

    ranked: list[RankedNode] = []

    for candidate in candidates:
        if candidate.id in excluded:
            continue
        if candidate.kind != NodeKind.ATOMIC_FACT:
            continue

        pair_score = score_pair(
            anchor,
            candidate,
            lexical_weight=lexical_weight,
            embedding_weight=embedding_weight,
        )

        if pair_score.combined_score < min_combined_similarity:
            continue

        ranked.append(
            RankedNode(
                node=candidate,
                lexical_score=pair_score.lexical_score,
                embedding_score=pair_score.embedding_score,
                combined_score=pair_score.combined_score,
                used_embedding=pair_score.used_embedding,
                lexical_components=pair_score.lexical_components,
            )
        )

    ranked.sort(
        key=lambda item: (
            -item.combined_score,
            -item.lexical_score,
            item.node.id,
        )
    )

    return ranked[:top_k]


def retrieve_top_k(
    graph: MemoryGraph,
    anchor: MemoryNode,
    *,
    top_k: int = DEFAULT_TOP_K,
    min_combined_similarity: float = DEFAULT_MIN_COMBINED_SIMILARITY,
    exclude_node_ids: set[str] | None = None,
    active_only: bool = True,
    lexical_weight: float = DEFAULT_LEXICAL_WEIGHT,
    embedding_weight: float = DEFAULT_EMBEDDING_WEIGHT,
) -> list[RankedNode]:
    """Retrieve the top-k atomic nodes from one MemoryGraph."""
    return rank_nodes(
        anchor,
        graph.atomic_nodes(active_only=active_only),
        top_k=top_k,
        min_combined_similarity=min_combined_similarity,
        exclude_node_ids=exclude_node_ids,
        lexical_weight=lexical_weight,
        embedding_weight=embedding_weight,
    )


def lexical_similarity(
    left: MemoryNode,
    right: MemoryNode,
    *,
    include_components: bool = False,
) -> float | tuple[float, dict[str, float]]:
    """Return normalized structured lexical similarity in [0, 1].

    Structured lexical scoring uses only the lightweight subject/predicate/object
    anchor lists plus routing_text/content similarity and the same-source bonus.
    Each S/P/O component is the maximum pairwise similarity across its two lists.
    """
    _validate_atomic_node(left, role="left")
    _validate_atomic_node(right, role="right")

    components: dict[str, float] = {
        "subject": 0.0,
        "predicate": 0.0,
        "object": 0.0,
        "routing_text": 0.0,
        "content": 0.0,
        "same_source": 0.0,
    }

    left_prop = left.proposition
    right_prop = right.proposition

    if left_prop is not None and right_prop is not None:
        components["subject"] = _best_field_similarity(
            left_prop.subjects,
            right_prop.subjects,
        )
        components["predicate"] = _best_field_similarity(
            left_prop.predicates,
            right_prop.predicates,
        )
        components["object"] = _best_field_similarity(
            left_prop.objects,
            right_prop.objects,
        )

    components["routing_text"] = _token_similarity(
        left.routing_text,
        right.routing_text,
    )
    components["content"] = _token_similarity(
        left.content,
        right.content,
    )

    if left.source_root_id == right.source_root_id:
        components["same_source"] = 1.0

    weighted_score = sum(
        _LEXICAL_FIELD_WEIGHTS[field] * components[field]
        for field in _LEXICAL_FIELD_WEIGHTS
    )
    weighted_score += (
        _SAME_SOURCE_BONUS * components["same_source"]
    )

    normalized = _clamp01(
        weighted_score / _MAX_LEXICAL_SCORE
    )

    if include_components:
        return normalized, components

    return normalized


def embedding_similarity(
    left: MemoryNode,
    right: MemoryNode,
) -> float | None:
    """Return normalized cosine similarity in [0, 1].

    Returns None while either node is missing a cached retrieval embedding.

    Embeddings must come from the same model and have matching dimensions.
    Cosine similarity is mapped from [-1, 1] to [0, 1] so it can be combined
    directly with the normalized lexical score.
    """
    left_embedding = left.retrieval_embedding
    right_embedding = right.retrieval_embedding

    if left_embedding is None or right_embedding is None:
        return None

    if left_embedding.model != right_embedding.model:
        raise RetrievalError(
            "Cannot compare embeddings from different models: "
            f"{left_embedding.model!r} vs {right_embedding.model!r}"
        )

    if left_embedding.dimensions != right_embedding.dimensions:
        raise RetrievalError(
            "Cannot compare embeddings with different dimensions: "
            f"{left_embedding.dimensions} vs {right_embedding.dimensions}"
        )

    cosine = _cosine_similarity(
        left_embedding.vector,
        right_embedding.vector,
    )

    return _clamp01((cosine + 1.0) / 2.0)


def _cosine_similarity(
    left: list[float],
    right: list[float],
) -> float:
    if len(left) != len(right):
        raise RetrievalError(
            "Embedding vectors must have equal length."
        )
    if not left:
        raise RetrievalError(
            "Embedding vectors cannot be empty."
        )

    dot_product = sum(
        left_value * right_value
        for left_value, right_value in zip(left, right)
    )
    left_norm = math.sqrt(
        sum(value * value for value in left)
    )
    right_norm = math.sqrt(
        sum(value * value for value in right)
    )

    if left_norm == 0.0 or right_norm == 0.0:
        raise RetrievalError(
            "Embedding vectors cannot have zero norm."
        )

    cosine = dot_product / (left_norm * right_norm)

    # Floating-point arithmetic can produce tiny excursions outside [-1, 1].
    return max(-1.0, min(1.0, cosine))


def _field_similarity(
    left: str | None,
    right: str | None,
) -> float:
    if not left or not right:
        return 0.0

    left_norm = _normalize_text(left)
    right_norm = _normalize_text(right)

    if not left_norm or not right_norm:
        return 0.0

    if left_norm == right_norm:
        return 1.0

    return _token_similarity(left_norm, right_norm)


def _best_field_similarity(
    left_values: list[str],
    right_values: list[str],
) -> float:
    """Return the strongest lexical alignment between two payload fields.

    Multiple lexical perspectives never accumulate extra score: the field keeps
    its original weight and contributes only its single best pairwise match.
    """
    if not left_values or not right_values:
        return 0.0

    return max(
        _field_similarity(left, right)
        for left in left_values
        for right in right_values
    )


def _token_similarity(
    left: str,
    right: str,
) -> float:
    """Jaccard similarity over normalized non-stopword tokens."""
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)

    if not left_tokens or not right_tokens:
        return 0.0

    union = left_tokens | right_tokens
    if not union:
        return 0.0

    return len(left_tokens & right_tokens) / len(union)


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in (
            match.group(0).casefold()
            for match in _TOKEN_RE.finditer(text or "")
        )
        if token not in _STOPWORDS
    }


def _normalize_text(text: str) -> str:
    return " ".join(
        match.group(0).casefold()
        for match in _TOKEN_RE.finditer(text or "")
    )


def _validate_atomic_node(
    node: MemoryNode,
    *,
    role: str,
) -> None:
    if node.kind != NodeKind.ATOMIC_FACT:
        raise RetrievalError(
            f"{role} node must be atomic, got {node.kind.value!r}"
        )


def _validate_signal_weights(
    *,
    lexical_weight: float,
    embedding_weight: float,
) -> None:
    if lexical_weight < 0.0 or embedding_weight < 0.0:
        raise ValueError(
            "Retrieval weights must be non-negative."
        )
    if lexical_weight + embedding_weight <= 0.0:
        raise ValueError(
            "At least one retrieval weight must be positive."
        )


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))