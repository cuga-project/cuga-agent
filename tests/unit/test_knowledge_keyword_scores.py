"""Keyword-only results carry a keyword score, not a meaning (cosine) score.

Fusion ties, the junk filter's relative-gap rule and the envelope's confidence advice must
not treat the two scales as one.
"""

import pytest

from cuga.backend.knowledge.engine import SearchResult, _JunkFilterStats, _apply_junk_filter, _rrf_fuse
from cuga.backend.knowledge.envelope import build_retrieval_envelope

pytestmark = pytest.mark.unit

LONG = "The backup policy requires a nightly copy of every server, kept for thirty days."


def _dense(name: str, score: float, text: str = LONG) -> SearchResult:
    return SearchResult(text=f"{text} ({name})", filename=name, page=1, score=score)


def _keyword(name: str, score: float, text: str = LONG) -> SearchResult:
    return SearchResult(text=f"{text} ({name})", filename=name, page=1, score=score, score_from_keywords=True)


def test_fusion_tie_goes_to_the_chunk_that_contains_the_query_terms():
    """A dense-only and a keyword-only chunk at rank 1 tie on RRF; filename must not decide."""
    fused = _rrf_fuse([_dense("a_meaning.txt", 0.82)], [_keyword("z_exact.txt", 0.13)])

    assert [r.filename for r in fused] == ["z_exact.txt", "a_meaning.txt"]


def test_fusion_still_prefers_a_chunk_found_by_both_searches():
    both_dense, both_kw = _dense("both.txt", 0.7), _keyword("both.txt", 0.5)
    fused = _rrf_fuse([_dense("a_meaning.txt", 0.9), both_dense], [both_kw, _keyword("b_exact.txt", 0.4)])

    assert fused[0].filename == "both.txt"
    assert fused[0].score_from_keywords is False  # the dense object (cosine score) is kept


def test_short_exact_keyword_chunk_survives_the_dry_run_gap_rule():
    """'SKU 12345 - $40' is flagged too_short; its small keyword score must not read as far below
    the top cosine score, or Postgres (ts_rank scale) drops it while SQLite (BM25) keeps it."""
    results = [_dense("policy.txt", 0.8), _keyword("price.txt", 0.13, text="SKU 12345 - $40")]

    kept, stats = _apply_junk_filter(results, "dry_run")

    assert [r.filename for r in kept] == ["policy.txt", "price.txt"]
    assert stats.filtered_count == 1  # still counted as flagged, not dropped


def test_gap_rule_still_drops_a_flagged_chunk_far_below_the_top_meaning_score():
    results = [_dense("policy.txt", 0.8), _dense("caption.txt", 0.2, text="Figure 3")]

    kept, _ = _apply_junk_filter(results, "dry_run")

    assert [r.filename for r in kept] == ["policy.txt"]


def _single_scope_recommendation(results: list[SearchResult]) -> str | None:
    env = build_retrieval_envelope(
        results=results,
        scope_requested="agent",
        multi_stats=None,
        single_stats=_JunkFilterStats(),
        single_scope_name="agent",
        filter_mode="dry_run",
        fallback_from=None,
        include_scores=True,
    )
    return env["retrieval"]["recommendation"]


@pytest.mark.parametrize(
    ("results", "expected"),
    [
        ([_keyword("exact.txt", 0.13), _dense("near.txt", 0.75)], None),
        ([_keyword("exact.txt", 0.13)], None),
        ([_dense("weak.txt", 0.2)], "low_confidence"),
    ],
    ids=["keyword-top-with-strong-meaning-hit", "keyword-hits-only", "weak-meaning-hits"],
)
def test_confidence_advice_uses_meaning_scores_only(results, expected):
    """An exact keyword hit with a small keyword score must not tell the agent to hedge."""
    assert _single_scope_recommendation(results) == expected
