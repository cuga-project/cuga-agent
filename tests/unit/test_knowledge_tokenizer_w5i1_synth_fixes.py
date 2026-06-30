"""Regression tests for the six fixes from workflow w5i1mbchd synth.

The workflow ran three RAG researchers (libraries / papers / hosted
providers) + a synthesizer + an adversarial verify. Verdict:
``SOLUTION_IS_CORRECT_BUT_MISSING_X``. The three ship-able fixes are:

  #1 Off-by-one + missing margin in the tiktoken (openai/azure) branch.
  #2 ``ChunkingTokenizer.recommended_chunk_tokens`` for retrieval-quality
     defaults on ≥2K-ctx embedders.
  #3 Static HF alias map (jina/nomic) to skip wasted Hub HEADs.

Plus three polishes:

  #4 Split lru_cache so transient HF Hub 503s aren't permanently cached.
  #5 (comment-only, no test target).
  #6 Promote dispatch log DEBUG → INFO (no test target).

These tests pin the new contracts.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cuga.backend.knowledge.config import KnowledgeConfig
from cuga.backend.knowledge.engine import (
    _HF_REPO_ALIASES,
    _HF_TOKEN_SAFETY_MARGIN,
    _OPENAI_TIKTOKEN_HARD_CAP,
    _TIKTOKEN_SAFE_MAX,
    KnowledgeEngine,
    _hf_repo_id_candidate,
    _load_hf_tokenizer_for_chunking,
    _recommended_chunk_tokens,
)


def _make_engine(provider: str, model: str) -> KnowledgeEngine:
    tmp = tempfile.mkdtemp(prefix="cuga-w5i1-test-")
    cfg = KnowledgeConfig(
        enabled=True,
        persist_dir=Path(tmp),
        embedding_provider=provider,
        embedding_model=model,
    )
    return KnowledgeEngine(cfg)


# ---------------------------------------------------------------------
# Fix #1: tiktoken off-by-one
# ---------------------------------------------------------------------


class TestTiktokenOffByOneFix:
    """Pre-fix the tiktoken branch returned safe_max_tokens=8192 with
    zero margin while the HF branch returned model_max_length-16. The
    OpenAI API rejects at >8191. Two researchers flagged independently."""

    def test_hard_cap_constant_is_openai_actual(self):
        # 8191 is the documented OpenAI text-embedding-3-* limit.
        # Magic numbers are bad; the named constant should reflect reality.
        assert _OPENAI_TIKTOKEN_HARD_CAP == 8191

    def test_tiktoken_safe_max_subtracts_margin(self):
        # Internal consistency with the HF branch: same margin policy.
        assert _TIKTOKEN_SAFE_MAX == _OPENAI_TIKTOKEN_HARD_CAP - _HF_TOKEN_SAFETY_MARGIN
        assert _TIKTOKEN_SAFE_MAX == 8175

    def test_litellm_openai_dispatch_uses_safe_max_not_8192(self):
        eng = _make_engine(provider="litellm", model="openai/text-embedding-3-large")
        tok = eng.get_chunking_tokenizer()
        assert tok.kind == "tiktoken"
        # Critical: the off-by-one fix. Pre-fix this was 8192 (an exact
        # API-reject at maximum fill).
        assert tok.safe_max_tokens == _TIKTOKEN_SAFE_MAX, (
            f"tiktoken branch returned {tok.safe_max_tokens}; "
            f"expected {_TIKTOKEN_SAFE_MAX} (= 8191 - 16). The previous "
            f"_DEFAULT_APPROXIMATE_CAP=8192 would silently OOM-reject "
            f"the last chunk on a maximum-fill document."
        )

    def test_litellm_azure_dispatch_also_uses_safe_max(self):
        eng = _make_engine(provider="litellm", model="azure/my-deployment")
        tok = eng.get_chunking_tokenizer()
        assert tok.kind == "tiktoken"
        assert tok.safe_max_tokens == _TIKTOKEN_SAFE_MAX

    def test_native_openai_dispatch_uses_safe_max(self):
        eng = _make_engine(provider="openai", model="text-embedding-3-small")
        tok = eng.get_chunking_tokenizer()
        assert tok.kind == "tiktoken"
        assert tok.safe_max_tokens == _TIKTOKEN_SAFE_MAX


# ---------------------------------------------------------------------
# Fix #2: recommended_chunk_tokens for retrieval quality
# ---------------------------------------------------------------------


class TestRecommendedChunkTokens:
    """Long-context embedders (bge-m3, jina-v3, voyage-3) retrieve better
    at 256-512 tokens than at their full context. The new field exposes
    this recommendation without forcing it."""

    @pytest.mark.parametrize(
        "safe_max,expected_recommended",
        [
            # ≥2K-ctx embedders capped at 512 regardless.
            (8176, 512),  # bge-m3, jina-v3, nomic-v1.5
            (32752, 512),  # voyage-3-large
            (8175, 512),  # text-embedding-3-large (post-fix-#1 cap)
            (2048, 512),  # boundary
            # <2K-ctx embedders use full safe_max (no quality reason to chunk smaller).
            (496, 496),  # bge-small / e5 with margin
            (1024, 1024),  # mistral-embed without margin
            (2047, 2047),  # just below the boundary
        ],
    )
    def test_recommended_helper(self, safe_max, expected_recommended):
        assert _recommended_chunk_tokens(safe_max) == expected_recommended

    def test_dispatch_populates_recommended_field(self):
        # The accessor must compute recommended_chunk_tokens at every
        # return site. Spot-check the tiktoken (long-ctx) branch.
        eng = _make_engine(provider="openai", model="text-embedding-3-large")
        tok = eng.get_chunking_tokenizer()
        assert tok.recommended_chunk_tokens == 512
        assert tok.safe_max_tokens == _TIKTOKEN_SAFE_MAX

    def test_dispatch_short_ctx_keeps_full_recommended(self):
        # fastembed bge-small (512-ctx) should NOT be artificially capped.
        eng = _make_engine(provider="fastembed", model="BAAI/bge-small-en-v1.5")
        tok = eng.get_chunking_tokenizer()
        # For a 512-ctx model the recommendation equals the safe cap —
        # no reason to chunk smaller than the embedder's own window.
        assert tok.recommended_chunk_tokens == tok.safe_max_tokens


# ---------------------------------------------------------------------
# Fix #3: static HF alias map
# ---------------------------------------------------------------------


class TestHfRepoAliases:
    """Skip wasted Hub HEAD misses for known-good redirects. Jina ships
    embeddings under ``jinaai/<model>``; Nomic under ``nomic-ai/<model>``;
    users (and litellm) often use the bare model name."""

    def test_jina_v3_resolves_to_canonical(self):
        assert _hf_repo_id_candidate("jina-embeddings-v3") == "jinaai/jina-embeddings-v3"

    def test_jina_v2_base_en_resolves(self):
        assert _hf_repo_id_candidate("jina-embeddings-v2-base-en") == "jinaai/jina-embeddings-v2-base-en"

    def test_nomic_v15_resolves(self):
        assert _hf_repo_id_candidate("nomic-embed-text-v1.5") == "nomic-ai/nomic-embed-text-v1.5"

    def test_litellm_prefix_strips_then_alias(self):
        # litellm/jina-embeddings-v3 → strip litellm/ → look up alias.
        assert _hf_repo_id_candidate("litellm/jina-embeddings-v3") == "jinaai/jina-embeddings-v3"

    def test_already_canonical_hf_path_unchanged(self):
        # If the user types the canonical name, alias map is a no-op.
        assert _hf_repo_id_candidate("jinaai/jina-embeddings-v3") == "jinaai/jina-embeddings-v3"

    def test_unknown_name_no_alias(self):
        # Models not in the alias map fall back to the slash-detection
        # logic (returns the stripped name if it has '/', else None).
        # The Hub HEAD then decides whether the repo exists.
        assert _hf_repo_id_candidate("intfloat/multilingual-e5-large") == ("intfloat/multilingual-e5-large")
        assert _hf_repo_id_candidate("text-embedding-3-large") is None

    def test_alias_map_is_lowercase_keyed(self):
        # The lookup uses .lower() so user-typed mixed case still resolves.
        assert _hf_repo_id_candidate("Jina-Embeddings-V3") == "jinaai/jina-embeddings-v3"

    def test_alias_map_keys_are_canonical_form(self):
        # Defensive: keys must be lowercase (the lookup .lower()s the input).
        for key in _HF_REPO_ALIASES:
            assert key == key.lower(), (
                f"_HF_REPO_ALIASES key {key!r} must be lowercase — the "
                f"lookup .lower()s the input, so a non-lowercase key "
                f"would silently never match."
            )


# ---------------------------------------------------------------------
# Fix #4: lru_cache split (success cached, failure retried)
# Covered in test_knowledge_chunker_hf_tokenizer.py (updated). Spot-check
# the cache_clear forwarder so existing tests don't silently regress.
# ---------------------------------------------------------------------


class TestCacheClearForwarder:
    def test_cache_clear_forwards_to_inner(self):
        """The lru_cache moved from _load_hf_tokenizer_for_chunking to
        _load_hf_tokenizer_cached. Existing tests call
        ``_load_hf_tokenizer_for_chunking.cache_clear()`` — the
        forwarder keeps them working."""
        _load_hf_tokenizer_for_chunking.cache_clear()

        fake_tok = SimpleNamespace(model_max_length=512)
        with patch("transformers.AutoTokenizer.from_pretrained", return_value=fake_tok) as m:
            r1 = _load_hf_tokenizer_for_chunking("intfloat/multilingual-e5-large")
            r2 = _load_hf_tokenizer_for_chunking("intfloat/multilingual-e5-large")
            assert r1 is fake_tok and r2 is fake_tok
            assert m.call_count == 1  # cached
            _load_hf_tokenizer_for_chunking.cache_clear()
            r3 = _load_hf_tokenizer_for_chunking("intfloat/multilingual-e5-large")
            assert r3 is fake_tok
            assert m.call_count == 2  # cleared, re-fetched
