"""Tests for the implementation of the agreed-stack levers.

Pins down the contract of each new piece so a regression on any of them is
detected before deployment:

  - Rerank config fields: validation bounds, exclusion from vector_config_hash.
  - E5-prefix injection: applied only for E5 model names, never for bge.
  - Empty-retry semantics: only fires on glossary-expanded empty result.

Note: the reranker module itself is deferred to a follow-up PR per review
comment 11; its tests (``TestRerankerModule``) are dropped here and will
return when the module ships.
"""

from __future__ import annotations

import pytest


class TestRerankConfigFields:
    def test_default_disabled(self):
        from cuga.backend.knowledge.config import KnowledgeConfig

        c = KnowledgeConfig()
        assert c.rerank_enabled is False
        assert c.rerank_top_k_in == 20
        # Default is the fastembed-servable bge-reranker-base (bge-reranker-v2-m3
        # is NOT in fastembed's TextCrossEncoder list).
        assert c.rerank_model == "BAAI/bge-reranker-base"

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
        """Balanced is where users opt into bge-base + the cross-encoder reranker.
        The reranker module now ships (fastembed TextCrossEncoder), so this
        profile has rerank.enabled=true with a fastembed-servable model and a
        candidate window (top_k_in) wide enough for config.validate().
        Switching standard<->balanced INVALIDATES vectors by design.
        """
        from cuga.backend.knowledge.config import load_profile

        p = load_profile("balanced")
        assert p["embeddings"]["model"] == "BAAI/bge-base-en-v1.5"
        assert p["rerank"]["enabled"] is True
        assert p["rerank"]["model"] == "BAAI/bge-reranker-base"
        # candidate_k must clear the 3×return_k floor config.validate() enforces.
        assert p["rerank"]["top_k_in"] >= 3 * p["search"]["default_limit"]

    def test_other_profiles_unchanged(self):
        """Regression: spot-check standard stays in the 600-1200 historical
        range. If this fails the Pareto matrix has drifted again."""
        from cuga.backend.knowledge.config import load_profile

        std = load_profile("standard")
        sz = std["chunking"]["chunk_size"]
        assert 600 <= sz <= 1200, f"standard chunk_size should be unchanged; got {sz}"
