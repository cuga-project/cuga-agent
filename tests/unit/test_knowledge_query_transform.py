"""Query transformation: multi_query parsing, HyDE-keeps-the-real-query, fail-open,
N-list RRF fusion, and config/profile wiring (default off)."""

from __future__ import annotations

import asyncio

import pytest

from cuga.backend.knowledge.config import KnowledgeConfig
from cuga.backend.knowledge.engine import SearchResult, _rrf_fuse_lists
from cuga.backend.knowledge.query_transform import expand_query


class _Gen:
    def __init__(self, out, delay=0.0):
        self._out = out
        self._delay = delay

    async def generate(self, prompt):
        if self._delay:
            await asyncio.sleep(self._delay)
        return self._out


# ── query_transform module ───────────────────────────────────────────────────


def test_multi_query_parses_dedups_and_strips_numbering():
    v = asyncio.run(
        expand_query(
            "multi_query",
            "reset password",
            _Gen("1. change my password\n2) recover account\nreset password"),
            n=3,
        )
    )
    assert v.dense_extra == ["change my password", "recover account"]  # numbering stripped, original deduped
    assert v.lexical_extra == v.dense_extra  # rewrites are real queries → safe for BM25
    assert v.active


def test_hyde_keeps_real_query_and_doc_is_dense_only():
    v = asyncio.run(expand_query("hyde", "what is the SLA", _Gen("The SLA guarantees 99.9% uptime."), n=3))
    # The hypothetical doc is an EXTRA dense leg; lexical stays empty so hallucinated
    # tokens never hit BM25. The real query still runs on the engine's normal path.
    assert v.dense_extra == ["The SLA guarantees 99.9% uptime."]
    assert v.lexical_extra == []


def test_n_controls_rewrite_count():
    v = asyncio.run(expand_query("multi_query", "q", _Gen("a\nb\nc\nd\ne"), n=3))
    assert len(v.dense_extra) == 2  # original + (n-1) legs


def test_fail_open_off_none_and_error():
    assert not asyncio.run(expand_query("off", "x", _Gen("a\nb"))).active
    assert not asyncio.run(expand_query("multi_query", "x", None)).active

    class _Boom:
        async def generate(self, p):
            raise RuntimeError("llm down")

    assert not asyncio.run(expand_query("hyde", "x", _Boom())).active


def test_timeout_fails_open():
    v = asyncio.run(expand_query("multi_query", "x", _Gen("a\nb", delay=0.3), timeout_s=0.01))
    assert not v.active  # search proceeds on the plain query, never blocked


# ── N-list RRF fusion ────────────────────────────────────────────────────────


def _sr(text, fn="f", pg=1):
    return SearchResult(text=text, filename=fn, page=pg, score=0.5)


def test_rrf_fuse_lists_single_is_passthrough():
    one = [_sr("a")]
    assert _rrf_fuse_lists([one]) is one


def test_rrf_fuse_lists_empty():
    assert _rrf_fuse_lists([]) == []
    assert _rrf_fuse_lists([[], []]) == []


def test_rrf_fuse_lists_prefers_doc_in_multiple_legs():
    # "shared" appears in both legs → highest fused score; singletons follow.
    fused = _rrf_fuse_lists([[_sr("shared"), _sr("onlyA", "g", 2)], [_sr("shared"), _sr("onlyB", "h", 3)]])
    assert fused[0].text == "shared"
    assert {r.text for r in fused} == {"shared", "onlyA", "onlyB"}


# ── config / profile wiring ──────────────────────────────────────────────────


def test_config_validates_query_transform():
    KnowledgeConfig(search_query_transform="multi_query").validate()
    KnowledgeConfig(search_query_transform="hyde").validate()
    with pytest.raises(ValueError):
        KnowledgeConfig(search_query_transform="bogus").validate()
    with pytest.raises(ValueError):
        KnowledgeConfig(search_query_transform_n=0).validate()


def test_all_profiles_default_query_transform_off():
    for p in ["speed", "standard", "balanced", "max_quality"]:
        c = KnowledgeConfig.from_settings({"knowledge": {"enabled": True, "search": {"rag_profile": p}}})
        c.validate()
        assert c.search_query_transform == "off", p


# ── end-to-end: HyDE searches the REAL query AND the hypothetical doc ─────────


def test_hyde_searches_real_query_and_hypothetical_doc_end_to_end(monkeypatch):
    """The load-bearing requirement: with HyDE on, the user's real query still runs
    (dense + lexical) and the hypothetical doc is only an EXTRA dense leg."""
    import tempfile
    from pathlib import Path
    from types import SimpleNamespace

    from cuga.backend.knowledge.engine import KnowledgeEngine, _sanitize_collection

    cfg = KnowledgeConfig(
        enabled=True,
        persist_dir=Path(tempfile.mkdtemp(prefix="cuga-qt-")),
        embedding_provider="fastembed",
        search_query_transform="hyde",
        search_hybrid_mode="auto",
        search_junk_filter="off",  # don't let the junk filter drop the short fake chunks
        rerank_enabled=False,
    )
    eng = KnowledgeEngine(cfg, chat_generator=_Gen("HYPO DOC about widgets"))

    dense_qs: list[str] = []
    lexical_qs: list[str] = []

    def _doc(t):
        return SimpleNamespace(page_content=t, metadata={"filename": "f", "page": 1})

    class _Adapter:
        def search(self, q, k=10):
            dense_qs.append(q)
            return [(_doc(f"chunk::{q}"), 0.9)]

        def search_lexical(self, q, k=10):
            lexical_qs.append(q)
            return []

    async def _noop(*_a, **_k):
        return None

    monkeypatch.setattr(eng, "_ensure_metadata_ready", _noop)
    monkeypatch.setattr(eng, "_ensure_vector_store_cached", _noop)
    coll = _sanitize_collection("test")
    eng._vector_stores[coll] = _Adapter()

    results, _stats = asyncio.run(eng.search_with_stats("test", "what is a widget", limit=5, scope="agent"))

    # The REAL query ran on BOTH legs; the hypothetical doc ran on dense ONLY.
    assert "what is a widget" in dense_qs
    assert "HYPO DOC about widgets" in dense_qs  # extra dense leg
    assert "what is a widget" in lexical_qs
    assert "HYPO DOC about widgets" not in lexical_qs  # never poisons BM25
    assert results  # fused results returned
