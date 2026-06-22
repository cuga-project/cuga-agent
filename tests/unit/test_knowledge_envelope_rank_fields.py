"""Hybrid-rank wire-field surfacing on the search envelope.

Closes the coverage gap Reviewer A flagged as A4: review-comment-45's fix
(``envelope._result_to_chunk`` now surfaces ``dense_rank`` /
``lexical_rank`` / ``rrf_score`` on hybrid-fused chunks) had ZERO tests.
A future cleanup that drops the conditional block would silently regress
observability without anyone noticing.

This file locks the contract:
  - When fields are populated → they appear in the wire chunk.
  - When fields are None → they DON'T appear (terse responses for
    single-leg / pure-dense / pre-FTS-upgrade collections).
"""

from __future__ import annotations

from cuga.backend.knowledge.engine import SearchResult
from cuga.backend.knowledge.envelope import _result_to_chunk


def _make_result(**overrides) -> SearchResult:
    """SearchResult with sensible defaults, override per-test."""
    defaults = dict(
        text="text body",
        filename="a.pdf",
        page=1,
        score=0.85,
        scope="agent",
    )
    defaults.update(overrides)
    r = SearchResult(
        **{k: v for k, v in defaults.items() if k in {"text", "filename", "page", "score", "scope"}}
    )
    # Per-leg ranks live as separate attributes; SearchResult permits
    # setting them post-construction.
    for k in ("dense_rank", "lexical_rank", "rrf_score", "section_path"):
        if k in overrides:
            setattr(r, k, overrides[k])
    return r


# ---------------------------------------------------------------------------
# Invariant — populated rank fields surface on the wire
# ---------------------------------------------------------------------------


def test_rrf_score_populated_surfaces_on_chunk():
    """A chunk that survived hybrid RRF fusion carries ``rrf_score``;
    the wire helper must expose it so observability tools can answer
    'is BM25 actually firing?' without re-querying."""
    r = _make_result(rrf_score=0.0312)
    chunk = _result_to_chunk(r, include_scores=True)
    assert chunk["rrf_score"] == 0.0312


def test_both_per_leg_ranks_surface_when_present():
    """Hybrid-fused chunks carry per-leg ranks. The wire helper
    surfaces both ``dense_rank`` and ``lexical_rank`` when populated."""
    r = _make_result(rrf_score=0.04, dense_rank=2, lexical_rank=5)
    chunk = _result_to_chunk(r, include_scores=True)
    assert chunk["rrf_score"] == 0.04
    assert chunk["dense_rank"] == 2
    assert chunk["lexical_rank"] == 5


# ---------------------------------------------------------------------------
# Invariant — None-leg ranks stay OFF the wire (terse response invariant)
# ---------------------------------------------------------------------------


def test_no_rank_fields_when_rrf_is_none():
    """A pure-dense response (pre-FTS-upgrade collection, single-leg)
    has rrf_score=None. The wire chunk must NOT contain ``rrf_score``,
    ``dense_rank``, or ``lexical_rank`` — those fields are meaningless
    when no fusion happened."""
    r = _make_result()  # no rrf_score, no per-leg ranks
    chunk = _result_to_chunk(r, include_scores=True)
    for key in ("rrf_score", "dense_rank", "lexical_rank"):
        assert key not in chunk, f"unexpected {key!r} on a non-hybrid chunk: {chunk}"


def test_dense_rank_only_surfaces_when_rrf_score_present():
    """If a chunk has ``dense_rank`` set but ``rrf_score`` is None
    (shouldn't happen in practice, but defensive), the per-leg ranks
    are NOT surfaced — they're gated behind ``rrf_score`` for
    consistency with the source code's ``if rrf_score is not None``
    block."""
    r = _make_result(dense_rank=3)  # dense_rank set; rrf_score absent
    chunk = _result_to_chunk(r, include_scores=True)
    assert "rrf_score" not in chunk
    assert "dense_rank" not in chunk


# ---------------------------------------------------------------------------
# Invariant — include_scores=False still hides everything
# ---------------------------------------------------------------------------


def test_include_scores_false_hides_score_but_rank_fields_still_visible():
    """``include_scores=False`` blanks ``score`` (the user-facing
    similarity) but the per-leg observability fields stay visible
    — they're for debugging, not for user-facing scoring."""
    r = _make_result(rrf_score=0.05, dense_rank=1, lexical_rank=3)
    chunk = _result_to_chunk(r, include_scores=False)
    assert "score" not in chunk
    # Per-leg observability fields still surface even when the
    # user-facing score is hidden — they're for ops, not for users.
    assert chunk["rrf_score"] == 0.05
    assert chunk["dense_rank"] == 1
    assert chunk["lexical_rank"] == 3
