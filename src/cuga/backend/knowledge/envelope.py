"""Wire envelope construction for knowledge search responses.

Single source of truth for the ``retrieval`` block schema — used by both
the HTTP route (``routes.py::search``) and the SDK client
(``client.py::KnowledgeClient.search_envelope``). Centralizing here
prevents drift between the two surfaces that would silently regress
observability for whichever caller wasn't updated.

The schema is documented in :func:`build_retrieval_envelope` — that
docstring is the contract.
"""

from __future__ import annotations

from typing import Any

from cuga.backend.knowledge.engine import (
    _JunkFilterStats,
    _MultiSearchStats,
    SearchResult,
)


_SCOPE_LEGEND_TEXT = {
    "agent": "Persistent knowledge available across all conversations (configured at agent setup).",
    "session": "Documents uploaded in this conversation only.",
}


# Recommendation thresholds. The absolute floor prevents the 1.5× ratio
# rule from firing when both
# scopes are low-confidence (e.g. 0.45 vs 0.30 — ratio fires but both
# are weak; we don't want to nudge the LLM toward weak data).
_RECOMMEND_RATIO = 1.5
_RECOMMEND_ABSOLUTE_FLOOR = 0.5
# When the entire top-K is low-confidence we emit "low_confidence" so
# the LLM treats the answer with appropriate caution. Aligns with the
# "skip relative-score gap when top<0.3" rule inside _apply_junk_filter.
_LOW_CONFIDENCE_FLOOR = 0.3


def _result_to_chunk(r: SearchResult, *, include_scores: bool) -> dict[str, Any]:
    """Serialize one :class:`SearchResult` to the wire chunk shape.

    Single helper so every code path emits identical chunk JSON —
    avoids the drift that existed between the route's scope='all' and
    single-scope branches before this consolidation.

    Per-leg ranks (``dense_rank``, ``lexical_rank``, ``rrf_score``) are
    populated by the hybrid fusion step and surfaced ONLY when non-None
    so single-leg / pure-dense / pre-upgrade responses stay terse. When
    debugging "why X ranked above Y" or "is BM25 actually firing?", the
    presence of these fields is the signal.
    """
    chunk: dict[str, Any] = {
        "source": r.scope,
        "text": r.text,
        "filename": r.filename,
        "page": r.page,
    }
    if getattr(r, "section_path", ""):
        chunk["section_path"] = r.section_path
    if include_scores:
        chunk["score"] = r.score
    # Hybrid-retrieval observability: surface ranks + fused score on any
    # chunk RRF actually fused. ``None`` means "leg didn't appear in the
    # input" (lexical leg empty for pre-FTS collections; dense leg would
    # only be None for lexical-only chunks).
    if getattr(r, "rrf_score", None) is not None:
        chunk["rrf_score"] = r.rrf_score
        if getattr(r, "dense_rank", None) is not None:
            chunk["dense_rank"] = r.dense_rank
        if getattr(r, "lexical_rank", None) is not None:
            chunk["lexical_rank"] = r.lexical_rank
    return chunk


def _stats_to_by_scope_entry(
    stats: _JunkFilterStats,
    *,
    returned: int,
    filter_mode: str,
) -> dict[str, Any]:
    """Convert a per-scope ``_JunkFilterStats`` to the wire dict shape.

    Invariant enforced by the engine:
        candidates == returned + filtered_count + below_threshold
                                + drain_drops + dedup_collapses
    """
    entry: dict[str, Any] = {
        "candidates": stats.candidates,
        "returned": returned,
        "filtered": stats.filtered_count,
        "below_threshold": stats.below_threshold,
        "drain_drops": stats.drain_drops,
        "dedup_collapses": stats.dedup_collapses,
        "filter_mode": filter_mode,
    }
    if stats.reasons:
        entry["reasons"] = dict(stats.reasons)
    return entry


def _compute_recommendation(
    top_score_by_scope: dict[str, float],
) -> str | None:
    """Return one of {prefer_<scope>, low_confidence, no_clean_results, None}.

    Emit ONLY for unambiguous patterns — the LLM contract teaches it to
    read this first. Vague hints would dilute the signal.

      ``prefer_<scope>``: one scope's top score is ≥ 1.5× the other's
          AND ≥ 0.5 absolute (so we don't push the LLM toward weak data
          just because the other scope is weaker still).
      ``low_confidence``: max top-score across scopes < 0.3 (everything
          is in the noise floor; the LLM should hedge its answer).
      ``None``: otherwise (scopes are comparable or only one scope was
          searched — no nudge needed).

    ``no_clean_results`` (entire batch was junk) is signaled at the call
    site by passing an empty ``top_score_by_scope`` dict when the
    candidates existed but all were filtered.
    """
    scored = {s: v for s, v in top_score_by_scope.items() if v is not None}
    if not scored:
        return None
    sorted_scopes = sorted(scored.items(), key=lambda kv: -kv[1])
    winner, winner_score = sorted_scopes[0]
    if winner_score < _LOW_CONFIDENCE_FLOOR:
        return "low_confidence"
    if len(sorted_scopes) < 2:
        return None
    runner_score = sorted_scopes[1][1]
    if runner_score <= 0:
        # All weight on winner — emit prefer when winner clears the
        # absolute floor (we already checked low_confidence above).
        return f"prefer_{winner}" if winner_score >= _RECOMMEND_ABSOLUTE_FLOOR else None
    if winner_score >= _RECOMMEND_ABSOLUTE_FLOOR and winner_score / runner_score >= _RECOMMEND_RATIO:
        return f"prefer_{winner}"
    return None


def build_retrieval_envelope(
    *,
    results: list[SearchResult],
    scope_requested: str,
    multi_stats: _MultiSearchStats | None,
    single_stats: _JunkFilterStats | None,
    single_scope_name: str | None,
    filter_mode: str,
    fallback_from: str | None,
    include_scores: bool,
) -> dict[str, Any]:
    """Build the full wire envelope for a knowledge search response.

    Returns a dict with shape:

    ::

        {
          "scope": "<requested scope>",
          "results": [chunk, ...],
          "by_source"?: {"<scope>": [chunk, ...]},
          "scope_legend"?: {"<scope>": "<legend>"},
          "retrieval": {
            "by_scope": {
              "<scope>": {
                "candidates": int, "returned": int, "filtered": int,
                "below_threshold": int, "drain_drops": int,
                "dedup_collapses": int, "filter_mode": "off|dry_run|enforce",
                "reasons"?: {"<reason>": int}
              }
            },
            "failed_scopes": [str, ...],
            "partial": bool,
            "fallback_from": str | null,
            "totals": {"candidates": int, "returned": int, "filtered": int},
            "recommendation": "prefer_<scope>" | "low_confidence" | "no_clean_results" | null
          },
          "filtered_count"?: int  # back-compat alias for retrieval.totals.filtered
        }

    Caller responsibilities:
      - ``multi_stats`` is non-None iff the call went through
        ``search_multi`` (scope='all' or auto-fallback). Mutually
        exclusive with ``single_stats``.
      - ``single_scope_name`` required when ``single_stats`` is set.
      - ``fallback_from`` is the originally-requested scope when the
        engine auto-retried under a broader scope (currently only
        ``session → all``). ``None`` when no fallback occurred.
      - ``filter_mode`` reflects the engine's active mode for the
        ingest path that just produced these results. The wire field
        ``filter_mode`` per scope carries the semantic: do NOT rename
        ``filtered`` → ``would_filter`` based on mode; the sibling
        field communicates that.
    """
    chunks = [_result_to_chunk(r, include_scores=include_scores) for r in results]

    # Build per-scope stats container.
    by_scope_stats: dict[str, dict[str, Any]] = {}
    returned_by_scope: dict[str, int] = {}
    for r in results:
        returned_by_scope[r.scope] = returned_by_scope.get(r.scope, 0) + 1

    failed_scopes: list[str] = []
    partial = False
    top_score_by_scope: dict[str, float] = {}

    if multi_stats is not None:
        failed_scopes = list(multi_stats.failed_scopes)
        partial = multi_stats.partial
        top_score_by_scope = dict(multi_stats.top_score_by_scope)
        for scope_name, scoped_stats in multi_stats.by_scope.items():
            by_scope_stats[scope_name] = _stats_to_by_scope_entry(
                scoped_stats,
                returned=returned_by_scope.get(scope_name, 0),
                filter_mode=filter_mode,
            )
    elif single_stats is not None:
        assert single_scope_name is not None, (
            "single_stats requires single_scope_name to attribute the bucket"
        )
        if results:
            top_score_by_scope[single_scope_name] = results[0].score
        by_scope_stats[single_scope_name] = _stats_to_by_scope_entry(
            single_stats,
            returned=returned_by_scope.get(single_scope_name, 0),
            filter_mode=filter_mode,
        )

    # Totals — convenience for LLMs that don't iterate by_scope.
    totals = {
        "candidates": sum(v.get("candidates", 0) for v in by_scope_stats.values()),
        "returned": sum(v.get("returned", 0) for v in by_scope_stats.values()),
        "filtered": sum(v.get("filtered", 0) for v in by_scope_stats.values()),
    }

    # Recommendation — emit only for unambiguous patterns.
    recommendation: str | None
    if totals["candidates"] > 0 and totals["returned"] == 0 and totals["filtered"] > 0:
        # All candidates filtered → no_clean_results regardless of score
        # math (which can't run on an empty top_score_by_scope anyway).
        recommendation = "no_clean_results"
    else:
        recommendation = _compute_recommendation(top_score_by_scope)

    retrieval: dict[str, Any] = {
        "by_scope": by_scope_stats,
        "failed_scopes": failed_scopes,
        "partial": partial,
        "fallback_from": fallback_from,
        "totals": totals,
        "recommendation": recommendation,
        # T3 reading-directive — rides with the data so the LLM sees it at
        # the moment it's composing the next code block (when the
        # system-prompt contract is already thousands of tokens upstream).
        # Three deltas in the v2 string (third-round expert review):
        #   - "answer directly when visible" lifted to sentence 1 (soft
        #     nudge against the code-first turn-1 instinct, without
        #     competing with the base prompt's CRITICAL Python-execution
        #     framing)
        #   - by_source-vs-results scope discrimination named explicitly
        #     (post-fix trace: LLM tried by_source["session"] on a
        #     scope="session" response, got empty, declared not-found)
        #   - lines[i+1] named as the WRONG move (post-fix trace: LLM
        #     understood "pair by position" but took the literal next
        #     line, which is the next LABEL not the matching value)
        "reading_directive": (
            "results[i].text IS prose. When the answer is visible in "
            "the chunk, READ it and answer in your text response with "
            "filename + page. Code is still the right move for "
            "multi-step workflows (loops over results, multiple "
            "searches per list item, aggregations, score filters). "
            "Extraction code (re.search / .split / label matching) "
            "against result['text'] is a FALLBACK — use when chunks "
            "are structurally regular (CSV/logs), to verify a "
            "candidate, or when the task IS extraction; NOT your first "
            "move when the answer is visible in prose. Access chunks "
            "at results[i] for ANY scope; by_source exists ONLY when "
            "scope='all'. If the chunk is a stacked label block "
            "followed by a stacked value block (PDF forms), pair by "
            "POSITION: find where the value run starts (after the last "
            "label), then take the value at the same ordinal as your "
            "target label — lines[i+1] is the WRONG move (it's the "
            "next LABEL, not the value). MULTI-HOP: if a chunk points "
            "elsewhere (\"see Appendix B\", \"incident #4521\") AND "
            "the pointer holds the answer, issue ONE follow-up search "
            "for the anchor; do NOT chain past one hop."
        ),
    }

    response: dict[str, Any] = {
        "scope": scope_requested,
        "results": chunks,
        "retrieval": retrieval,
    }

    # ``by_source`` + ``scope_legend`` only when multi-scope (the LLM
    # contract teaches reading these for scope='all').
    if multi_stats is not None:
        searched_scopes = list(by_scope_stats.keys())
        by_source: dict[str, list] = {s: [] for s in searched_scopes}
        for chunk, r in zip(chunks, results):
            by_source.setdefault(r.scope, []).append(chunk)
        response["by_source"] = by_source
        response["scope_legend"] = {
            s: _SCOPE_LEGEND_TEXT[s] for s in searched_scopes if s in _SCOPE_LEGEND_TEXT
        }

    # Back-compat: top-level ``filtered_count`` mirrors
    # ``retrieval.totals.filtered``. Kept for one release; consumers
    # that read it will get a DeprecationWarning when accessing via
    # the SDK wrapper. Only emitted when non-zero so a quiet response
    # stays quiet on the wire.
    if totals["filtered"]:
        response["filtered_count"] = totals["filtered"]

    return response
