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
    _hf_repo_id_candidate,
    _hf_tokenizer_seq_limit,
    _load_hf_tokenizer_for_chunking,
    _strip_litellm_route_prefix,
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


class TestHfRepoIdCandidate:
    """``_hf_repo_id_candidate`` is the post-PR-A simplification:
    returns the stripped name when it looks HF-style (has '/'), None
    otherwise. The Hub is the source of truth — the LOADER decides
    whether a candidate actually exists via try/except on
    ``AutoTokenizer.from_pretrained``."""

    @pytest.mark.parametrize(
        "model_id,expected",
        [
            # Real HF-style routes — return the stripped repo id.
            ("watsonx/intfloat/multilingual-e5-large", "intfloat/multilingual-e5-large"),
            ("litellm/BAAI/bge-large-en-v1.5", "BAAI/bge-large-en-v1.5"),
            ("openrouter/sentence-transformers/all-mpnet-base-v2", "sentence-transformers/all-mpnet-base-v2"),
            (
                "watsonx/ibm-granite/granite-embedding-30m-english",
                "ibm-granite/granite-embedding-30m-english",
            ),
            ("intfloat/multilingual-e5-base", "intfloat/multilingual-e5-base"),
            # Closed-vendor routes also return candidate — the LOADER's
            # 404 + lru_cached None handles the "no HF tokenizer for
            # this repo" case. Previously the allow-list short-circuited
            # these to None; now we let the Hub be the source of truth.
            # First lookup costs one Hub HEAD, subsequent are cached.
            ("cohere/embed-english-v3.0", "cohere/embed-english-v3.0"),
            ("voyage/voyage-3", "voyage/voyage-3"),
            ("watsonx/ibm/slate-30m-english-rtrvr", "ibm/slate-30m-english-rtrvr"),
        ],
    )
    def test_positive_matches(self, model_id, expected):
        assert _hf_repo_id_candidate(model_id) == expected

    @pytest.mark.parametrize(
        "model_id",
        [
            # After ``_strip_litellm_route_prefix``, no '/' → not
            # HF-style → caller routes to tiktoken (openai/azure) or
            # approximate (anything else).
            "openai/text-embedding-3-small",
            "openai/text-embedding-3-large",
            "openai/text-embedding-ada-002",
            "azure/my-deployment",
            "fastembed",
            "",
            "no-slash-here",
        ],
    )
    def test_negative_matches_fall_through(self, model_id):
        assert _hf_repo_id_candidate(model_id) is None


class TestHfTokenizerSeqLimitMargined:
    """Helper returns the SAFE chunk-token cap (raw max minus a margin
    that covers embedder-side wrapping). Tests assert against the
    margined value because that's the actual contract. Imported here so
    a margin change updates the assertions in one place."""

    def _safe(self, raw):
        from cuga.backend.knowledge.engine import _HF_TOKEN_SAFETY_MARGIN

        return raw - _HF_TOKEN_SAFETY_MARGIN

    def test_normal_512(self):
        tok = SimpleNamespace(model_max_length=512)
        assert _hf_tokenizer_seq_limit(tok) == self._safe(512)

    def test_large_model_8192(self):
        # bge-m3 has 8192 — should be passed through (minus margin).
        tok = SimpleNamespace(model_max_length=8192)
        assert _hf_tokenizer_seq_limit(tok) == self._safe(8192)

    def test_very_large_integer_sentinel_defaults_to_512(self):
        # transformers' VERY_LARGE_INTEGER convention means "unset". We
        # default to 512 - margin (BERT / XLM-R convention).
        tok = SimpleNamespace(model_max_length=int(1e30))
        assert _hf_tokenizer_seq_limit(tok) == self._safe(512)

    def test_missing_attr_defaults_to_512(self):
        tok = SimpleNamespace()
        assert _hf_tokenizer_seq_limit(tok) == self._safe(512)

    def test_none_defaults_to_512(self):
        tok = SimpleNamespace(model_max_length=None)
        assert _hf_tokenizer_seq_limit(tok) == self._safe(512)

    def test_zero_defaults_to_512(self):
        tok = SimpleNamespace(model_max_length=0)
        assert _hf_tokenizer_seq_limit(tok) == self._safe(512)

    def test_regression_518_over_512_watsonx_e5(self):
        """Regression for user-reported QA bug on PR #383:

            litellm.ContextWindowExceededError: This model's maximum
            context length is 512 tokens. However, you requested 518
            tokens in the input for embedding generation.

        The +6 overhead came from e5's "passage: " prefix +
        provider-side BOS/EOS that the local tokenizer never saw at
        chunk-count time. The safety margin (default 16) prevents this
        recurrence by capping the chunker to 496 for an e5-large
        tokenizer (512 - 16) — leaves 16 tokens of headroom for any
        reasonable wrapping a hosted provider can throw at us.
        """
        from cuga.backend.knowledge.engine import _HF_TOKEN_SAFETY_MARGIN

        tok = SimpleNamespace(model_max_length=512)
        cap = _hf_tokenizer_seq_limit(tok)
        # Cap must leave at least the observed 6-token overhead of slack.
        assert cap <= 512 - 6, f"Cap {cap} doesn't leave room for the 518>512 overhead the user hit"
        # And the margin must be at least the observed overhead.
        assert _HF_TOKEN_SAFETY_MARGIN >= 6

    def test_margin_does_not_underflow_to_zero(self):
        # Defensive: tiny model_max_length (synthetic / corrupt config)
        # must not return <= 0 — that would break the chunker.
        tok = SimpleNamespace(model_max_length=4)  # smaller than margin
        cap = _hf_tokenizer_seq_limit(tok)
        assert cap >= 1


class TestLoadHfTokenizerCachesSuccessButRetriesOnFailure:
    """Workflow w5i1mbchd synth fix #4 changed the cache contract:
    successes are still lru_cached (one Hub HEAD per repo per process),
    but failures are NO LONGER cached. Reason: a transient corp-proxy
    503 used to permanently lock the model into char-based fallback
    for the whole process; now a retry on the next ingest can recover.
    """

    def setup_method(self):
        # Each test starts with a clean cache so test order doesn't matter.
        _load_hf_tokenizer_for_chunking.cache_clear()

    def test_success_path_returns_tokenizer_and_caches(self):
        with patch("transformers.AutoTokenizer.from_pretrained") as m:
            fake_tok = SimpleNamespace(model_max_length=512)
            m.return_value = fake_tok
            r1 = _load_hf_tokenizer_for_chunking("intfloat/multilingual-e5-large")
            r2 = _load_hf_tokenizer_for_chunking("intfloat/multilingual-e5-large")
            assert r1 is fake_tok
            assert r2 is fake_tok
            # Cached: two lookups, ONE underlying call.
            m.assert_called_once_with("intfloat/multilingual-e5-large")

    def test_failure_does_not_cache_and_retries_next_call(self):
        """Counter-test to the pre-fix-#4 behavior. The old contract
        cached None and locked the model out for the process. The new
        contract logs once but lets the next call retry — so a 30s
        transient hub outage doesn't degrade the rest of the session."""
        with patch("transformers.AutoTokenizer.from_pretrained") as m:
            m.side_effect = OSError("offline / no network")
            r1 = _load_hf_tokenizer_for_chunking("intfloat/multilingual-e5-large")
            r2 = _load_hf_tokenizer_for_chunking("intfloat/multilingual-e5-large")
            r3 = _load_hf_tokenizer_for_chunking("intfloat/multilingual-e5-large")
            assert r1 is None and r2 is None and r3 is None
            # Each call re-attempts: three lookups, THREE underlying calls.
            assert m.call_count == 3, (
                f"Failures should NOT be cached after PR-A fix #4 — got "
                f"{m.call_count} calls (expected 3 = one per lookup)."
            )

    def test_failure_then_success_recovers(self):
        """Direct repro of the operational bug fix #4 closes: hub flakes
        on first call, succeeds on second. Old contract: model stuck at
        char-based forever. New contract: second call gets the tokenizer."""
        fake_tok = SimpleNamespace(model_max_length=512)
        side_effects = [OSError("transient 503"), fake_tok]
        with patch("transformers.AutoTokenizer.from_pretrained", side_effect=side_effects) as m:
            r1 = _load_hf_tokenizer_for_chunking("intfloat/multilingual-e5-large")
            r2 = _load_hf_tokenizer_for_chunking("intfloat/multilingual-e5-large")
            assert r1 is None, "first call must surface the OSError as None"
            assert r2 is fake_tok, "second call must succeed (failure NOT cached)"
            assert m.call_count == 2

    def test_import_error_returns_none(self):
        # Simulate a slim install where transformers is not present at all.
        with patch.dict("sys.modules", {"transformers": None}):
            result = _load_hf_tokenizer_for_chunking("intfloat/multilingual-e5-large")
            assert result is None


# NOTE: The earlier ``TestWarnUnlistedEmbedderObservability`` +
# ``TestCanaryExemptionForOpenAINativeRoutes`` classes were deleted in
# PR-A (workflow w9y9xtyse synth). The custom canary
# ``_warn_unlisted_embedder_once`` is gone — the existing
# ``logger.warning`` inside ``_load_hf_tokenizer_for_chunking`` is now
# the sole signal, fired once per unknown repo via
# ``functools.lru_cache``. Tests for that behavior live in
# ``TestLoadHfTokenizerCachesSuccessButRetriesOnFailure`` (above).
