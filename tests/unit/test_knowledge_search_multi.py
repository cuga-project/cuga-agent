"""Cross-scope ``search_multi`` invariants — focused regression spine.

Re-introduces six contract invariants that lost coverage when the
audit-style ``test_knowledge_multi_scope_search.py`` monolith
(~3,629 LOC) was dropped per review comment 61. The dropped file
had ~17 ``test_search_multi_*`` tests; only ONE survived in
``test_knowledge_rag_scope_failure_fix.py`` (the original failing case).
This file covers the cross-scope contract: dedup, hard cap, scope
tagging, deterministic tiebreak, partial-failure semantics, and the
explicit cross-scope leak guard.

Out of scope here:
  - The junk-filter rules — covered in ``test_knowledge_rag_scope_failure_fix.py``.
  - Engine init / config — covered in ``test_knowledge_config_perf_keys.py``.
  - The audit-IDed ``test_b1_*`` / ``test_c3_*`` matrices — intentionally
    not re-added; the maintainable shape is invariant-named.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from cuga.backend.knowledge.engine import (
    KnowledgeConfig,
    KnowledgeEngine,
    SearchResult,
    _JunkFilterStats,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _engine() -> KnowledgeEngine:
    persist = Path(tempfile.mkdtemp(prefix="cuga-search-multi-"))
    return KnowledgeEngine(KnowledgeConfig(enabled=True, persist_dir=persist))


def _stub(by_collection: dict[str, list[SearchResult]]):
    """Build a ``search_with_stats`` stub that returns per-collection
    fixtures, tagging each SearchResult with the requested ``scope``."""

    async def _impl(*, collection, query, limit, score_threshold, scope=""):
        results = [
            SearchResult(
                text=r.text,
                filename=r.filename,
                page=r.page,
                score=r.score,
                scope=scope,
            )
            for r in by_collection.get(collection, [])
        ]
        return results, _JunkFilterStats(candidates=len(results))

    return _impl


def _run_multi(eng, scoped, *, limit=10, per_scope_limit=True):
    return asyncio.run(
        eng.search_multi(
            scoped_collections=scoped,
            query="anything",
            limit=limit,
            per_scope_limit=per_scope_limit,
        )
    )


# ---------------------------------------------------------------------------
# Invariant 1 — every result is tagged with its scope (no untagged orphans)
# ---------------------------------------------------------------------------


def test_every_result_carries_its_source_scope(monkeypatch):
    """The wire envelope routes chunks to scope-keyed buckets via
    ``r.scope``. An untagged result becomes invisible / wrongly
    bucketed. Lock the invariant: every returned SearchResult has a
    non-empty ``scope``.
    """
    eng = _engine()
    monkeypatch.setattr(
        eng,
        "search_with_stats",
        _stub(
            {
                "kb_agent": [SearchResult(text="a", filename="a.pdf", page=1, score=0.9, scope="")],
                "kb_sess": [SearchResult(text="s", filename="s.pdf", page=1, score=0.85, scope="")],
            }
        ),
    )
    results, _stats = _run_multi(eng, [("agent", "kb_agent"), ("session", "kb_sess")], limit=10)
    assert len(results) == 2
    assert {r.scope for r in results} == {"agent", "session"}, [r.scope for r in results]


# ---------------------------------------------------------------------------
# Invariant 2 — cross-scope dedup keeps the higher-RRF copy
# ---------------------------------------------------------------------------


def test_same_chunk_in_both_scopes_collapses_keeping_higher(monkeypatch):
    """If the same chunk (same filename + page + text-hash key) appears
    in BOTH scopes, the dedup pass collapses to one row and credits the
    higher-rank scope. Without this, an LLM sees two copies of the same
    evidence and over-weights it.
    """
    eng = _engine()
    same_text = "identical chunk body — appears in both scopes verbatim"
    monkeypatch.setattr(
        eng,
        "search_with_stats",
        _stub(
            {
                # Agent scope ranks it FIRST (higher score → higher RRF).
                "kb_agent": [
                    SearchResult(text=same_text, filename="shared.pdf", page=1, score=0.95, scope=""),
                    SearchResult(text="agent-only", filename="a.pdf", page=2, score=0.40, scope=""),
                ],
                # Session ranks it SECOND.
                "kb_sess": [
                    SearchResult(text="session-only", filename="s.pdf", page=1, score=0.80, scope=""),
                    SearchResult(text=same_text, filename="shared.pdf", page=1, score=0.78, scope=""),
                ],
            }
        ),
    )
    results, _stats = _run_multi(eng, [("agent", "kb_agent"), ("session", "kb_sess")], limit=10)
    # Exactly one copy of the shared chunk survives.
    shared_copies = [r for r in results if r.filename == "shared.pdf" and r.page == 1]
    assert len(shared_copies) == 1, [r.scope for r in shared_copies]
    # The survivor came from the higher-ranking scope.
    assert shared_copies[0].scope == "agent"


# ---------------------------------------------------------------------------
# Invariant 3 — distinct chunks with same (filename, page) do NOT collapse
# ---------------------------------------------------------------------------


def test_distinct_chunks_with_same_filename_page_survive_dedup(monkeypatch):
    """Two different chunks on the same PDF page are legitimately
    different evidence (table row vs surrounding prose, e.g.). The
    dedup key includes a content hash so they don't false-positive-
    collapse."""
    eng = _engine()
    monkeypatch.setattr(
        eng,
        "search_with_stats",
        _stub(
            {
                "kb_agent": [
                    SearchResult(text="chunk one", filename="doc.pdf", page=1, score=0.80, scope=""),
                ],
                "kb_sess": [
                    SearchResult(text="chunk two", filename="doc.pdf", page=1, score=0.80, scope=""),
                ],
            }
        ),
    )
    results, _stats = _run_multi(eng, [("agent", "kb_agent"), ("session", "kb_sess")], limit=10)
    assert len(results) == 2
    assert {r.text for r in results} == {"chunk one", "chunk two"}


# ---------------------------------------------------------------------------
# Invariant 4 — one scope erroring returns partial (don't kill the call)
# ---------------------------------------------------------------------------


def test_one_scope_erroring_returns_partial_results(monkeypatch):
    """If the agent collection raises mid-fan-out, the session results
    must still be returned. ``stats.failed_scopes`` records the casualty
    so the envelope can flag ``partial=True``."""
    eng = _engine()

    async def _impl(*, collection, query, limit, score_threshold, scope=""):
        if collection == "kb_agent":
            raise RuntimeError("simulated agent-collection failure")
        return (
            [
                SearchResult(text=f"s-{i}", filename="s.pdf", page=i, score=0.7 - 0.01 * i, scope=scope)
                for i in range(3)
            ],
            _JunkFilterStats(candidates=3),
        )

    monkeypatch.setattr(eng, "search_with_stats", _impl)
    results, stats = _run_multi(eng, [("agent", "kb_agent"), ("session", "kb_sess")], limit=10)
    # Session results survive.
    assert len(results) == 3
    assert all(r.scope == "session" for r in results)
    # Agent is recorded as failed.
    assert "agent" in stats.failed_scopes, stats.failed_scopes


# ---------------------------------------------------------------------------
# Invariant 5 — Option B (per_scope_limit=True) caps at min(100, limit × n)
# ---------------------------------------------------------------------------


def test_option_b_caps_total_at_hundred_even_with_huge_scopes(monkeypatch):
    """When ``per_scope_limit=True`` (the default for explicit
    ``scope='all'``), each scope contributes up to ``limit`` chunks,
    but the absolute ceiling is 100. Without this an LLM gets an
    unbounded token bill.
    """
    eng = _engine()
    big_agent = [
        SearchResult(text=f"a{i}", filename="a.pdf", page=i, score=0.99 - 0.001 * i, scope="")
        for i in range(80)
    ]
    big_sess = [
        SearchResult(text=f"s{i}", filename="s.pdf", page=i, score=0.99 - 0.001 * i, scope="")
        for i in range(80)
    ]
    monkeypatch.setattr(eng, "search_with_stats", _stub({"kb_agent": big_agent, "kb_sess": big_sess}))
    results, _stats = _run_multi(
        eng, [("agent", "kb_agent"), ("session", "kb_sess")], limit=100, per_scope_limit=True
    )
    assert len(results) <= 100, len(results)


# ---------------------------------------------------------------------------
# Invariant 6 — scope='session' caller does NOT see agent results
# ---------------------------------------------------------------------------


def test_session_only_search_does_not_bleed_agent_results(monkeypatch):
    """When the caller passes ONLY the session collection to
    ``search_multi``, no agent results appear. Sounds tautological,
    but it's the integration-level check that the fan-out doesn't
    secretly pull agent collections from the engine's state.
    """
    eng = _engine()
    monkeypatch.setattr(
        eng,
        "search_with_stats",
        _stub(
            {
                "kb_sess": [SearchResult(text="session-only", filename="s.pdf", page=1, score=0.9, scope="")],
            }
        ),
    )
    results, _stats = _run_multi(eng, [("session", "kb_sess")], limit=10)
    assert {r.scope for r in results} == {"session"}, [r.scope for r in results]
    assert all(r.filename == "s.pdf" for r in results)


# ---------------------------------------------------------------------------
# Invariant 7 (bonus — Reviewer B asked for ≥6; this is the 7th) —
# deterministic order on equal scores so repeated runs don't flicker
# ---------------------------------------------------------------------------


def test_deterministic_tiebreak_on_equal_scores(monkeypatch):
    """Two results with identical scores must come out in a stable order
    across runs — otherwise the UI shows different "top result" on
    every search of the same query.
    """
    eng = _engine()
    monkeypatch.setattr(
        eng,
        "search_with_stats",
        _stub(
            {
                "kb_agent": [
                    SearchResult(text="A", filename="a.pdf", page=1, score=0.8, scope=""),
                    SearchResult(text="B", filename="b.pdf", page=1, score=0.8, scope=""),
                ],
                "kb_sess": [
                    SearchResult(text="C", filename="c.pdf", page=1, score=0.8, scope=""),
                ],
            }
        ),
    )
    first, _ = _run_multi(eng, [("agent", "kb_agent"), ("session", "kb_sess")], limit=10)
    second, _ = _run_multi(eng, [("agent", "kb_agent"), ("session", "kb_sess")], limit=10)
    assert [r.text for r in first] == [r.text for r in second]
