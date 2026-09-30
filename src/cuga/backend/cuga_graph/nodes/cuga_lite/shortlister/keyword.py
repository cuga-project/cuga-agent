"""Deterministic keyword ranker — a port of Claude Code's ``ToolSearch`` scorer.

free-code ``src/tools/ToolSearchTool/ToolSearchTool.ts`` (``searchToolsWithKeywords``)
ranks deferred tools with no model call at all:

- the whole query equal to a tool name returns that tool alone;
- ``+term`` makes a term *required* (name parts, description or search hint);
- tool names are split into parts; the MCP **server** segment scores highest
  (12 exact / 6 substring), other name parts 10 / 5, a full-name fallback 3,
  a curated ``searchHint`` 4 and the description 2 (word-boundary matches);
- zero scores are dropped and the top ``max_results`` (default 5) returned.

CUGA's registry names are ``<app>_<operation>``, so the **app** plays the MCP
server's role. On top of the port: plural folding (``coaches`` ↔ ``coach``), a
small stop-word list (queries here are natural language, not keyword lists),
and the OpenAPI ``operationId`` as extra name parts.

Registered as the built-in ``keyword`` shortlister strategy, and used as the
first ranking stage of ``tool_search`` (fc-deferred-tool-search §3.3-3.4).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, ClassVar, Dict, Iterable, List, Optional, Sequence, Tuple

from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.base import (
    ShortlistCandidate,
    ShortlistRequest,
    ShortlistResult,
)

DEFAULT_MAX_RESULTS = 5

# Weights, straight from searchToolsWithKeywords (MCP server → CUGA app).
APP_EXACT = 12
APP_PARTIAL = 6
PART_EXACT = 10
PART_PARTIAL = 5
FULL_NAME_FALLBACK = 3
SEARCH_HINT = 4
DESCRIPTION = 2

#: Length normalization (CUGA addition, not in Claude Code): the score is scaled by
#: ``(1 - w) + w * coverage``, where coverage is the fraction of the tool's own
#: operation-name parts the query touches. OpenAPI catalogues are full of siblings —
#: ``get_count_territories`` vs ``get_territory_count_by_employee_name`` — and without
#: this, the longer name wins by matching the same terms plus more description words.
#: On the M3 REST catalogue (scripts/tool_search_calibration.py) w = 0.5 lifts
#: recall@1 70.6% → 79.5% and recall@5 93.3% → 96.8%.
COVERAGE_WEIGHT = 0.5
#: Name parts too generic to count toward coverage.
GENERIC_PARTS = frozenset({"get", "by", "v1", "v2", "api"})

_SPLIT = re.compile(r"[\s_\-./:]+")
_CAMEL = re.compile(r"([a-z0-9])([A-Z])")
_WORD = re.compile(r"[a-z0-9]+")

STOP_WORDS = frozenset(
    {
        "a", "an", "the", "of", "for", "to", "in", "on", "at", "by", "with", "from", "and", "or",
        "is", "are", "be", "was", "were", "been", "being", "has", "have", "had", "do", "does", "did",
        "can", "could", "should", "would", "will", "how", "many", "much", "when", "where", "why",
        "that", "which", "who", "whose", "what", "this", "these", "those",
        "i", "me", "my", "we", "our", "you", "your", "it", "its", "all", "any", "some",
        "tool", "tools", "api", "apis", "endpoint", "function", "find", "need", "want",
    }
)  # fmt: skip


def fold(word: str) -> str:
    """Cheap singular form: ``coaches``→``coach``, ``categories``→``category``, ``games``→``game``."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("ches", "shes", "sses", "xes", "zes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def split_name(name: str) -> List[str]:
    """``getCoachesWhoPlayed`` / ``get_coaches-who.played`` → folded lower-case parts."""
    spaced = _CAMEL.sub(r"\1 \2", name or "")
    return [fold(p) for p in _SPLIT.split(spaced.lower()) if p]


def _words(text: str) -> set[str]:
    return {fold(w) for w in _WORD.findall((text or "").lower())}


def _tool_app(tool: Any) -> str:
    for target in (tool, getattr(tool, "func", None), getattr(tool, "coroutine", None)):
        app = getattr(target, "_app_name", None)
        if isinstance(app, str) and app:
            return app
    metadata = getattr(tool, "metadata", None) or {}
    app = metadata.get("app_name") if isinstance(metadata, dict) else None
    return app if isinstance(app, str) else ""


def _tool_attr(tool: Any, attr: str) -> str:
    for target in (tool, getattr(tool, "func", None), getattr(tool, "coroutine", None)):
        value = getattr(target, attr, None)
        if isinstance(value, str) and value:
            return value
    metadata = getattr(tool, "metadata", None) or {}
    value = metadata.get(attr.lstrip("_")) if isinstance(metadata, dict) else None
    return value if isinstance(value, str) else ""


@dataclass(frozen=True)
class IndexedTool:
    name: str
    app_parts: Tuple[str, ...]
    parts: Tuple[str, ...]
    full: str
    hint_words: frozenset
    description_words: frozenset


def index_tool(tool: Any) -> IndexedTool:
    name = getattr(tool, "name", "") or ""
    app = _tool_app(tool)
    app_parts = tuple(split_name(app))
    parts = split_name(name)
    # Drop the ``<app>_`` prefix from the operation parts: it already scores as the app.
    if app_parts and tuple(parts[: len(app_parts)]) == app_parts:
        parts = parts[len(app_parts) :]
    parts += [p for p in split_name(_tool_attr(tool, "_operation_id")) if p not in parts]
    return IndexedTool(
        name=name,
        app_parts=app_parts,
        parts=tuple(parts),
        full=" ".join((*app_parts, *parts)),
        hint_words=frozenset(_words(_tool_attr(tool, "_search_hint"))),
        description_words=frozenset(_words(getattr(tool, "description", "") or "")),
    )


def parse_query(query: str) -> Tuple[List[str], List[str]]:
    """``(required, optional)`` folded terms; ``+x`` is required, stop words are dropped."""
    required: List[str] = []
    optional: List[str] = []
    for raw in (query or "").lower().split():
        is_required = raw.startswith("+") and len(raw) > 1
        for term in _WORD.findall(raw):
            term = fold(term)
            if is_required:
                required.append(term)
            elif term not in STOP_WORDS:
                optional.append(term)
    return required, optional


def _matches(tool: IndexedTool, term: str) -> bool:
    return (
        term in tool.app_parts
        or term in tool.parts
        or any(term in p for p in (*tool.app_parts, *tool.parts))
        or term in tool.hint_words
        or term in tool.description_words
    )


def coverage(tool: IndexedTool, terms: Sequence[str]) -> float:
    """Fraction of the tool's operation-name parts that some query term matches."""
    parts = [p for p in tool.parts if p not in GENERIC_PARTS and p not in STOP_WORDS]
    if not parts:
        return 1.0
    term_set = set(terms)
    return sum(1 for p in parts if p in term_set or any(t in p for t in term_set)) / len(parts)


def score_tool(tool: IndexedTool, terms: Sequence[str]) -> float:
    """Claude Code's additive score, then length-normalized by name coverage."""
    raw = _raw_score(tool, terms)
    if raw <= 0 or COVERAGE_WEIGHT <= 0:
        return float(raw)
    return raw * ((1 - COVERAGE_WEIGHT) + COVERAGE_WEIGHT * coverage(tool, terms))


def _raw_score(tool: IndexedTool, terms: Sequence[str]) -> int:
    score = 0
    for term in terms:
        if term in tool.app_parts:
            score += APP_EXACT
        elif any(term in p for p in tool.app_parts):
            score += APP_PARTIAL
        elif term in tool.parts:
            score += PART_EXACT
        elif any(term in p for p in tool.parts):
            score += PART_PARTIAL
        if score == 0 and term in tool.full:
            score += FULL_NAME_FALLBACK
        if term in tool.hint_words:
            score += SEARCH_HINT
        if term in tool.description_words:
            score += DESCRIPTION
    return score


def rank(
    query: str,
    tools: Iterable[Any],
    *,
    max_results: Optional[int] = DEFAULT_MAX_RESULTS,
    index: Optional[Dict[str, IndexedTool]] = None,
) -> List[Tuple[str, float]]:
    """Best-first ``(name, score)``; ``max_results=None`` returns every non-zero score."""
    tools = list(tools)
    q = (query or "").strip().lower()
    for tool in tools:  # exact-name fast path
        if (getattr(tool, "name", "") or "").lower() == q and q:
            return [(tool.name, 10_000.0)]

    required, optional = parse_query(query)
    terms = [*required, *optional] or required
    if not terms:
        return []
    indexed = [index[t.name] if index and t.name in index else index_tool(t) for t in tools]
    if required:
        indexed = [t for t in indexed if all(_matches(t, term) for term in required)]
    scored = [(t.name, score_tool(t, terms)) for t in indexed]
    scored = [item for item in scored if item[1] > 0]
    # Stable: equal scores keep catalogue order, so results are deterministic.
    scored.sort(key=lambda item: -item[1])
    return scored if max_results is None else scored[:max_results]


class KeywordShortlister:
    """``ShortlisterStrategy`` over :func:`rank` — no model, no embeddings."""

    name: ClassVar[str] = "keyword"

    async def shortlist(self, request: ShortlistRequest) -> ShortlistResult:
        limit = request.max_results or request.top_k or DEFAULT_MAX_RESULTS
        ranked = rank(request.query, request.tools, max_results=limit)
        return ShortlistResult(
            candidates=[
                ShortlistCandidate(name=name, score=float(score), reasoning=f"keyword score {score:.1f}")
                for name, score in ranked
            ]
        )
