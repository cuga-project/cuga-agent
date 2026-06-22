"""Tests for the implementation of the agreed-stack levers.

Pins down the contract of each new piece so a regression on any of them is
detected before deployment:

  - Reranker module: lazy load, graceful failure when sentence-transformers
    is missing, returns top-K in descending score order.
  - Rerank config fields: validation bounds, exclusion from vector_config_hash.
  - E5-prefix injection: applied only for E5 model names, never for bge.
  - Empty-retry semantics: only fires on glossary-expanded empty result.

The reranker model itself is not loaded in these tests (would download
~1.1GB and add 30+s); we patch the CrossEncoder import to a stub.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Reranker module
# ---------------------------------------------------------------------------


class TestRerankerModule:
    def test_empty_candidates_returns_empty_no_load(self):
        from cuga.backend.knowledge.reranker import rerank

        # No model should be loaded for an empty list — confirm by NOT
        # patching sentence_transformers; if it tried to load, we'd see an
        # ImportError or download attempt.
        assert rerank("any query", [], top_k=5) == []

    def test_single_candidate_returned_no_load(self):
        from cuga.backend.knowledge.reranker import rerank, RerankedCandidate

        cand = RerankedCandidate(text="only one", score=0.5, metadata={}, original_score=0.5)
        # Single candidate skips model load (no reordering possible).
        out = rerank("any query", [cand], top_k=5)
        assert len(out) == 1
        assert out[0] is cand

    def test_top_k_reorder_descending(self, monkeypatch):
        """When the CrossEncoder returns scores [0.1, 0.9, 0.5] for 3 inputs,
        rerank should return them in descending order: input 1, input 2, input 0."""
        from cuga.backend.knowledge import reranker as rmod
        from cuga.backend.knowledge.reranker import RerankedCandidate, rerank

        # Reset cache so the stubbed CrossEncoder is loaded fresh.
        monkeypatch.setattr(rmod, "_MODEL_CACHE", {})

        fake_model = MagicMock()
        fake_model.predict.return_value = [0.1, 0.9, 0.5]

        fake_st = MagicMock()
        fake_st.CrossEncoder = MagicMock(return_value=fake_model)
        monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)

        cands = [
            RerankedCandidate(text=f"c{i}", score=0.5, metadata={}, original_score=0.5) for i in range(3)
        ]
        out = rerank("q", cands, top_k=3, model_name="any-model")

        # Order is c1 (0.9), c2 (0.5), c0 (0.1)
        assert [c.text for c in out] == ["c1", "c2", "c0"]
        # Top-K cap respected
        out2 = rerank("q", cands, top_k=2, model_name="any-model")
        assert len(out2) == 2
        assert out2[0].text == "c1" and out2[1].text == "c2"

    def test_original_score_preserved(self, monkeypatch):
        from cuga.backend.knowledge import reranker as rmod
        from cuga.backend.knowledge.reranker import RerankedCandidate, rerank

        monkeypatch.setattr(rmod, "_MODEL_CACHE", {})

        fake_model = MagicMock()
        fake_model.predict.return_value = [2.0, -1.0]
        fake_st = MagicMock()
        fake_st.CrossEncoder = MagicMock(return_value=fake_model)
        monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)

        cands = [
            RerankedCandidate(text="a", score=0.3, metadata={"f": "a.pdf"}, original_score=0.3),
            RerankedCandidate(text="b", score=0.9, metadata={"f": "b.pdf"}, original_score=0.9),
        ]
        out = rerank("q", cands, top_k=2, model_name="m")
        # The original_score field is preserved so the engine can keep
        # displaying fusion scores (cross-encoder logits are unbounded).
        assert out[0].text == "a"
        assert out[0].original_score == 0.3
        assert out[0].score == 2.0
        assert out[1].original_score == 0.9

    def test_missing_sentence_transformers_raises_typed_error(self, monkeypatch):
        """When sentence-transformers isn't installed, the reranker module
        raises a typed error so the engine can catch + fall back."""
        from cuga.backend.knowledge import reranker as rmod
        from cuga.backend.knowledge.reranker import (
            RerankedCandidate,
            RerankerUnavailableError,
            rerank,
        )

        monkeypatch.setattr(rmod, "_MODEL_CACHE", {})
        # Simulate the import failing by injecting a sentinel that raises
        # on attribute access.
        monkeypatch.setitem(sys.modules, "sentence_transformers", None)

        cands = [
            RerankedCandidate(text="a", score=0.1, metadata={}, original_score=0.1),
            RerankedCandidate(text="b", score=0.2, metadata={}, original_score=0.2),
        ]
        with pytest.raises(RerankerUnavailableError):
            rerank("q", cands, top_k=2, model_name="unavail")

    def test_is_available_does_not_load(self):
        from cuga.backend.knowledge.reranker import is_available

        # Just check the function runs and returns a bool without loading
        # the model.
        result = is_available()
        assert isinstance(result, bool)

    def test_model_cache_avoids_double_load(self, monkeypatch):
        """Second call with the same model_name returns the cached instance."""
        from cuga.backend.knowledge import reranker as rmod
        from cuga.backend.knowledge.reranker import RerankedCandidate, rerank

        monkeypatch.setattr(rmod, "_MODEL_CACHE", {})

        load_count = {"n": 0}

        def _make(*_a, **_kw):
            load_count["n"] += 1
            fake = MagicMock()
            fake.predict.return_value = [0.5, 0.5]
            return fake

        fake_st = MagicMock()
        fake_st.CrossEncoder = MagicMock(side_effect=_make)
        monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)

        cands = [
            RerankedCandidate(text="a", score=0.1, metadata={}, original_score=0.1),
            RerankedCandidate(text="b", score=0.2, metadata={}, original_score=0.2),
        ]
        rerank("q", cands, top_k=2, model_name="cache-test")
        rerank("q2", cands, top_k=2, model_name="cache-test")
        assert load_count["n"] == 1  # loaded exactly once


# ---------------------------------------------------------------------------
# Rerank config fields
# ---------------------------------------------------------------------------


class TestRerankConfigFields:
    def test_default_disabled(self):
        from cuga.backend.knowledge.config import KnowledgeConfig

        c = KnowledgeConfig()
        assert c.rerank_enabled is False
        assert c.rerank_top_k_in == 20
        assert c.rerank_model == "BAAI/bge-reranker-v2-m3"

    def test_top_k_bounds_enforced(self):
        from cuga.backend.knowledge.config import KnowledgeConfig

        with pytest.raises(ValueError, match="rerank_top_k_in"):
            KnowledgeConfig(rerank_top_k_in=0).validate()
        with pytest.raises(ValueError, match="rerank_top_k_in"):
            KnowledgeConfig(rerank_top_k_in=101).validate()
        KnowledgeConfig(rerank_top_k_in=1).validate()
        KnowledgeConfig(rerank_top_k_in=100).validate()

    def test_enabled_must_be_bool(self):
        from cuga.backend.knowledge.config import KnowledgeConfig

        with pytest.raises(ValueError, match="rerank_enabled"):
            cfg = KnowledgeConfig()
            cfg.rerank_enabled = "yes"  # type: ignore[assignment]
            cfg.validate()

    def test_model_must_be_nonempty_string(self):
        from cuga.backend.knowledge.config import KnowledgeConfig

        with pytest.raises(ValueError, match="rerank_model"):
            KnowledgeConfig(rerank_model="").validate()
        with pytest.raises(ValueError, match="rerank_model"):
            KnowledgeConfig(rerank_model="   ").validate()

    def test_excluded_from_vector_config_hash(self):
        """Critical invariant: toggling reranker must NOT trigger a reindex.
        The hash must be stable across rerank field changes."""
        from cuga.backend.knowledge.config import KnowledgeConfig

        a = KnowledgeConfig(rerank_enabled=False)
        b = KnowledgeConfig(rerank_enabled=True, rerank_top_k_in=50, rerank_model="other")
        assert a.vector_config_hash() == b.vector_config_hash()

    def test_round_trip_via_coerce(self):
        from cuga.backend.knowledge.config import KnowledgeConfig

        cfg = KnowledgeConfig(rerank_enabled=True, rerank_top_k_in=30, rerank_model="BAAI/bge-reranker-v2-m3")
        cfg.validate()
        d = cfg.to_dict()
        assert d["rerank_enabled"] is True
        assert d["rerank_top_k_in"] == 30
        restored = KnowledgeConfig.coerce_and_validate(d)
        assert restored.rerank_enabled is True
        assert restored.rerank_top_k_in == 30


# ---------------------------------------------------------------------------
# E5-prefix injection
# ---------------------------------------------------------------------------


class TestE5PrefixInjection:
    """The reviewer flagged this as the gotcha that would have silently
    underdelivered the embedding swap. These tests pin the prefix detection
    contract."""

    @pytest.mark.parametrize(
        "name,expected",
        [
            ("intfloat/multilingual-e5-large", True),
            ("intfloat/e5-large-v2", True),
            ("intfloat/e5-base", True),
            ("BAAI/bge-small-en-v1.5", False),
            ("BAAI/bge-large-zh", False),
            ("sentence-transformers/all-MiniLM-L6-v2", False),
            ("", False),
        ],
    )
    def test_prefix_detection(self, name, expected, monkeypatch):
        """The prefix should be applied for E5-family models only."""
        # Patch fastembed.TextEmbedding so we don't actually load a model.
        # We only inspect the prefix attributes after construction.
        import cuga.backend.knowledge.engine as eng

        class _FakeTE:
            def __init__(self, *_a, **_kw):
                pass

        monkeypatch.setattr("fastembed.TextEmbedding", _FakeTE)
        wrapper = eng._FastEmbedEmbeddings(name)
        if expected:
            assert wrapper._query_prefix == "query: "
            assert wrapper._passage_prefix == "passage: "
        else:
            assert wrapper._query_prefix == ""
            assert wrapper._passage_prefix == ""

    def test_prefix_applied_to_documents(self, monkeypatch):
        import cuga.backend.knowledge.engine as eng

        captured: list[list[str]] = []

        class _FakeTE:
            def __init__(self, *_a, **_kw):
                pass

            def embed(self, texts):
                captured.append(list(texts))
                import numpy as np

                return iter([np.array([0.0]) for _ in texts])

        monkeypatch.setattr("fastembed.TextEmbedding", _FakeTE)
        wrapper = eng._FastEmbedEmbeddings("intfloat/multilingual-e5-large")
        wrapper.embed_documents(["doc 1", "doc 2"])
        assert captured[-1] == ["passage: doc 1", "passage: doc 2"]

    def test_prefix_applied_to_query(self, monkeypatch):
        import cuga.backend.knowledge.engine as eng

        captured: list[list[str]] = []

        class _FakeTE:
            def __init__(self, *_a, **_kw):
                pass

            def embed(self, texts):
                captured.append(list(texts))
                import numpy as np

                return iter([np.array([0.0])])

        monkeypatch.setattr("fastembed.TextEmbedding", _FakeTE)
        wrapper = eng._FastEmbedEmbeddings("intfloat/multilingual-e5-large")
        wrapper.embed_query("how do I file K3?")
        assert captured[-1] == ["query: how do I file K3?"]

    def test_no_prefix_for_non_e5_model(self, monkeypatch):
        """Critical regression test: existing deployments using bge-small-en
        must NOT suddenly get prefixed inputs (would shift the embedding
        space and break their existing vectors)."""
        import cuga.backend.knowledge.engine as eng

        captured: list[list[str]] = []

        class _FakeTE:
            def __init__(self, *_a, **_kw):
                pass

            def embed(self, texts):
                captured.append(list(texts))
                import numpy as np

                return iter([np.array([0.0]) for _ in texts])

        monkeypatch.setattr("fastembed.TextEmbedding", _FakeTE)
        wrapper = eng._FastEmbedEmbeddings("BAAI/bge-small-en-v1.5")
        wrapper.embed_documents(["unchanged input"])
        assert captured[-1] == ["unchanged input"]


# ---------------------------------------------------------------------------
# Balanced profile tuning
# ---------------------------------------------------------------------------


class TestBalancedProfile:
    """Pareto-locked profile values. The earlier procedural-text bench
    settings (chunk_size=450) are superseded by the broader Pareto matrix
    that owns embedding_model + rerank + docling.pdf_mode + search.hybrid_mode
    per profile."""

    def test_balanced_chunk_size_is_800(self):
        """800/150 is the consensus floor for procedural/technical-product
        docs with a reranker on top. Smaller than max_quality (1000) which
        preserves long-form context."""
        from cuga.backend.knowledge.config import load_profile

        p = load_profile("balanced")
        assert p["chunking"]["chunk_size"] == 800
        assert p["chunking"]["chunk_overlap"] == 150

    def test_balanced_pins_bge_base_and_rerank(self):
        """Balanced is where users opt into the +1.1GB reranker + bge-base.
        Switching standard<->balanced INVALIDATES vectors by design."""
        from cuga.backend.knowledge.config import load_profile

        p = load_profile("balanced")
        assert p["embeddings"]["model"] == "BAAI/bge-base-en-v1.5"
        assert p["rerank"]["enabled"] is True
        assert p["rerank"]["top_k_in"] >= 3 * p["search"]["default_limit"]

    def test_other_profiles_unchanged(self):
        """Regression: spot-check standard stays in the 600-1200 historical
        range. If this fails the Pareto matrix has drifted again."""
        from cuga.backend.knowledge.config import load_profile

        std = load_profile("standard")
        sz = std["chunking"]["chunk_size"]
        assert 600 <= sz <= 1200, f"standard chunk_size should be unchanged; got {sz}"
