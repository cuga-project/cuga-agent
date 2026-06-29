"""Regression tests for issue #387: HybridChunker tokenizer mismatch for
litellm/openrouter-routed HF-style embedders.

Bug recap: chunker fell back to ``tiktoken.cl100k_base`` for any non-fastembed,
non-huggingface provider. For ``litellm + watsonx/intfloat/multilingual-e5-large``
that means counting chunks in OpenAI BPE tokens against an XLM-RoBERTa
sentencepiece embedder with max_seq_length=512. 800 cl100k tokens easily
becomes 1500–2400 XLM-RoBERTa tokens for multilingual content, the embedder
silently truncates, retrieval quality degrades.

Fix: detect HF-style model ids via a curated org allow-list, load the model's
real tokenizer via ``AutoTokenizer.from_pretrained``, wrap in
``docling_core.transforms.chunker.tokenizer.huggingface.HuggingFaceTokenizer``
with ``max_tokens = min(chunk_size, model_max_length)``. Failure falls
through to the existing tiktoken path — net effect strictly improves quality,
never regresses ingest.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cuga.backend.knowledge.engine import (
    _hf_repo_id_for_chunk_sizing,
    _hf_tokenizer_seq_limit,
    _load_hf_tokenizer_for_chunking,
    _strip_litellm_route_prefix,
    _warn_unlisted_embedder_once,
)


class TestStripLitellmRoutePrefix:
    def test_watsonx_prefix(self):
        assert (
            _strip_litellm_route_prefix("watsonx/intfloat/multilingual-e5-large")
            == "intfloat/multilingual-e5-large"
        )

    def test_litellm_prefix_strips_one_layer_only(self):
        # ``litellm/openai/x`` → ``openai/x``, NOT ``x``. Single-strip.
        assert (
            _strip_litellm_route_prefix("litellm/openai/text-embedding-3-small")
            == "openai/text-embedding-3-small"
        )

    def test_openai_prefix(self):
        assert _strip_litellm_route_prefix("openai/text-embedding-3-small") == "text-embedding-3-small"

    def test_case_preserved_on_output(self):
        # The match is case-insensitive but the stripped tail keeps original
        # casing — important because HF repo ids are case-sensitive on lookup.
        assert _strip_litellm_route_prefix("WATSONX/BAAI/bge-base-en-v1.5") == "BAAI/bge-base-en-v1.5"

    def test_no_prefix_returns_input(self):
        assert _strip_litellm_route_prefix("BAAI/bge-base-en-v1.5") == "BAAI/bge-base-en-v1.5"

    def test_empty_string(self):
        assert _strip_litellm_route_prefix("") == ""

    def test_none_safe(self):
        # Defensive: a None at this layer shouldn't crash the chunker.
        assert _strip_litellm_route_prefix(None) == ""  # type: ignore[arg-type]


class TestHfRepoIdForChunkSizing:
    @pytest.mark.parametrize(
        "model_id,expected",
        [
            # The actual bug repro:
            ("watsonx/intfloat/multilingual-e5-large", "intfloat/multilingual-e5-large"),
            # Other real-world litellm routes that hit allow-listed orgs:
            ("litellm/BAAI/bge-large-en-v1.5", "BAAI/bge-large-en-v1.5"),
            ("openrouter/sentence-transformers/all-mpnet-base-v2", "sentence-transformers/all-mpnet-base-v2"),
            (
                "watsonx/ibm-granite/granite-embedding-30m-english",
                "ibm-granite/granite-embedding-30m-english",
            ),
            # Already-stripped HF id (someone wired the right one directly):
            ("intfloat/multilingual-e5-base", "intfloat/multilingual-e5-base"),
        ],
    )
    def test_positive_matches(self, model_id, expected):
        assert _hf_repo_id_for_chunk_sizing(model_id) == expected

    @pytest.mark.parametrize(
        "model_id",
        [
            # OpenAI text-embedding-* — tiktoken handles these correctly; must
            # NOT be misrouted to AutoTokenizer.
            "openai/text-embedding-3-small",
            "openai/text-embedding-3-large",
            "openai/text-embedding-ada-002",
            # Proprietary closed models — no HF tokenizer exists.
            "cohere/embed-english-v3.0",
            "voyage/voyage-3",
            "gemini/text-embedding-004",
            # IBM proprietary slate model — no public HF mirror.
            "watsonx/ibm/slate-30m-english-rtrvr",
            # Local fastembed model — never goes through litellm.
            "fastembed",
            # Empty / malformed.
            "",
            "/",
            "no-slash-here",
        ],
    )
    def test_negative_matches_fall_through(self, model_id):
        assert _hf_repo_id_for_chunk_sizing(model_id) is None


class TestHfTokenizerSeqLimit:
    def test_normal_512(self):
        tok = SimpleNamespace(model_max_length=512)
        assert _hf_tokenizer_seq_limit(tok) == 512

    def test_large_model_8192(self):
        # bge-m3 has 8192 — make sure we don't accidentally clamp it.
        tok = SimpleNamespace(model_max_length=8192)
        assert _hf_tokenizer_seq_limit(tok) == 8192

    def test_very_large_integer_sentinel_defaults_to_512(self):
        # transformers' VERY_LARGE_INTEGER convention means "unset". We must
        # NOT pass that to the chunker as a real ceiling — it'd produce
        # gargantuan chunks the embedder would then truncate.
        tok = SimpleNamespace(model_max_length=int(1e30))
        assert _hf_tokenizer_seq_limit(tok) == 512

    def test_missing_attr_defaults_to_512(self):
        tok = SimpleNamespace()
        assert _hf_tokenizer_seq_limit(tok) == 512

    def test_none_defaults_to_512(self):
        tok = SimpleNamespace(model_max_length=None)
        assert _hf_tokenizer_seq_limit(tok) == 512

    def test_zero_defaults_to_512(self):
        tok = SimpleNamespace(model_max_length=0)
        assert _hf_tokenizer_seq_limit(tok) == 512


class TestLoadHfTokenizerCachesFailure:
    """Critical: a transient corp-proxy outage at process start must NOT
    re-fire the hub call on every doc ingested in the session."""

    def setup_method(self):
        # Each test starts with a clean cache so test order doesn't matter.
        _load_hf_tokenizer_for_chunking.cache_clear()

    def test_success_path_returns_tokenizer(self):
        with patch("transformers.AutoTokenizer.from_pretrained") as m:
            fake_tok = SimpleNamespace(model_max_length=512)
            m.return_value = fake_tok
            result = _load_hf_tokenizer_for_chunking("intfloat/multilingual-e5-large")
            assert result is fake_tok
            m.assert_called_once_with("intfloat/multilingual-e5-large")

    def test_failure_caches_none_and_does_not_retry(self):
        with patch("transformers.AutoTokenizer.from_pretrained") as m:
            m.side_effect = OSError("offline / no network")
            r1 = _load_hf_tokenizer_for_chunking("intfloat/multilingual-e5-large")
            r2 = _load_hf_tokenizer_for_chunking("intfloat/multilingual-e5-large")
            r3 = _load_hf_tokenizer_for_chunking("intfloat/multilingual-e5-large")
            assert r1 is None and r2 is None and r3 is None
            # The whole point: ONE call across three lookups.
            assert m.call_count == 1

    def test_import_error_returns_none(self):
        # Simulate a slim install where transformers is not present at all.
        with patch.dict("sys.modules", {"transformers": None}):
            result = _load_hf_tokenizer_for_chunking("intfloat/multilingual-e5-large")
            assert result is None


class TestWarnUnlistedEmbedderObservability:
    """Operator canary for the silent-degradation failure mode that the HF
    allow-list doesn't cover. The WARNING converts an invisible bug (chunks
    silently sized in cl100k against a 512-token e5 window) into a single
    log line operators can grep for. Deduped per (provider, model, encoding)
    via lru_cache so it never spams per-document."""

    def setup_method(self):
        _warn_unlisted_embedder_once.cache_clear()

    def test_warns_once_per_unique_tuple(self, monkeypatch):
        # Three calls with the same args -> exactly ONE log line.
        # Loguru's logger doesn't integrate with caplog, so monkeypatch it.
        from cuga.backend.knowledge import engine as eng

        calls: list[str] = []
        monkeypatch.setattr(eng.logger, "warning", lambda msg, *a, **k: calls.append(msg))
        _warn_unlisted_embedder_once("litellm", "mistralai/mistral-embed-x", "cl100k_base")
        _warn_unlisted_embedder_once("litellm", "mistralai/mistral-embed-x", "cl100k_base")
        _warn_unlisted_embedder_once("litellm", "mistralai/mistral-embed-x", "cl100k_base")
        msgs = [m for m in calls if "allow-list" in m]
        assert len(msgs) == 1, f"expected 1 warning, got {len(msgs)}: {msgs}"
        assert "mistralai/mistral-embed-x" in msgs[0]
        assert "litellm" in msgs[0]

    def test_warns_separately_for_distinct_models(self, monkeypatch):
        # Different models from the same provider each get their own warning.
        from cuga.backend.knowledge import engine as eng

        calls: list[str] = []
        monkeypatch.setattr(eng.logger, "warning", lambda msg, *a, **k: calls.append(msg))
        _warn_unlisted_embedder_once("litellm", "orgA/embedder-x", "cl100k_base")
        _warn_unlisted_embedder_once("litellm", "orgB/embedder-y", "cl100k_base")
        msgs = [m for m in calls if "allow-list" in m]
        assert len(msgs) == 2
        assert any("orgA/embedder-x" in m for m in msgs)
        assert any("orgB/embedder-y" in m for m in msgs)
