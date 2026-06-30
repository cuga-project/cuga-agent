"""Regression test for the unit-mismatch bug uncovered in PR #383 manual QA.

User's log showed:

    Capping chunk_size 800 -> 512 for embedder intfloat/multilingual-e5-large
    Loaded 38 raw chunks from 14173-LevyI (2).pdf
    Re-split into 99 chunks (chunk_size=800)
    [transformers] Token indices sequence length is longer than the
    specified maximum sequence length for this model (716 > 512). Running
    this sequence through the model will result in indexing errors

Root cause: ``_load_document`` runs a post-Docling re-split with
``RecursiveCharacterTextSplitter(chunk_size=chunk_size)`` — but the
``chunk_size`` parameter on that splitter is CHARACTERS, while the user's
``chunk_size=800`` is a TOKEN target (correctly applied by HybridChunker
upstream). For dense / multilingual content an 800-char piece becomes
600-800 XLM-RoBERTa tokens, exceeding the e5-large 512 ceiling — the
embedder silently truncates, losing tail content.

Fix:
  1. Raise the re-split trigger to an emergency threshold (100k chars) —
     don't re-split correctly-bounded HybridChunker output.
  2. When a re-split IS needed (pathological mega-chunk from Docling),
     use ``_build_text_splitter`` which token-counts via the same HF
     tokenizer the embedder uses.
  3. Apply the same token-aware splitter to the plain-text path.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cuga.backend.knowledge.config import KnowledgeConfig
from cuga.backend.knowledge.engine import (
    KnowledgeEngine,
    _load_hf_tokenizer_for_chunking,
)


def _make_engine(provider: str = "fastembed", model: str = "BAAI/bge-small-en-v1.5") -> KnowledgeEngine:
    tmp = tempfile.mkdtemp(prefix="cuga-resplit-test-")
    cfg = KnowledgeConfig(
        enabled=True,
        persist_dir=Path(tmp),
        embedding_provider=provider,
        embedding_model=model,
    )
    return KnowledgeEngine(cfg)


class TestBuildTextSplitterTokenAware:
    """When an HF tokenizer is available, ``_build_text_splitter`` returns
    a splitter that counts in TOKENS (not chars). The unit mismatch bug
    can only return if this is broken."""

    def setup_method(self):
        _load_hf_tokenizer_for_chunking.cache_clear()

    def test_litellm_watsonx_e5_uses_hf_tokenizer(self):
        # The exact #387 / #396 scenario.
        eng = _make_engine(
            provider="litellm",
            model="watsonx/intfloat/multilingual-e5-large",
        )

        fake_tok = SimpleNamespace(model_max_length=512)
        with patch("transformers.AutoTokenizer.from_pretrained", return_value=fake_tok):
            with patch(
                "langchain_text_splitters.RecursiveCharacterTextSplitter.from_huggingface_tokenizer"
            ) as m_from_hf:
                m_from_hf.return_value = "hf_splitter"
                splitter = eng._build_text_splitter(chunk_size=800, chunk_overlap=100)

        from cuga.backend.knowledge.engine import _HF_TOKEN_SAFETY_MARGIN

        assert splitter == "hf_splitter"
        # Called with model's max minus the safety margin (covers
        # provider-side wrapping that the local tokenizer doesn't see —
        # see the 518>512 regression).
        call_args = m_from_hf.call_args
        expected_cap = 512 - _HF_TOKEN_SAFETY_MARGIN
        assert call_args.kwargs["chunk_size"] == expected_cap, (
            f"splitter not capped at model_max_length-margin={expected_cap}: {call_args}"
        )
        # Tokenizer passed is the one we loaded (matches embedder).
        assert call_args.args[0] is fake_tok

    def test_openrouter_bge_uses_hf_tokenizer(self):
        eng = _make_engine(
            provider="openrouter",
            model="BAAI/bge-large-en-v1.5",
        )

        fake_tok = SimpleNamespace(model_max_length=512)
        with patch("transformers.AutoTokenizer.from_pretrained", return_value=fake_tok):
            with patch(
                "langchain_text_splitters.RecursiveCharacterTextSplitter.from_huggingface_tokenizer"
            ) as m_from_hf:
                m_from_hf.return_value = "hf_splitter"
                splitter = eng._build_text_splitter(chunk_size=800, chunk_overlap=100)

        from cuga.backend.knowledge.engine import _HF_TOKEN_SAFETY_MARGIN

        assert splitter == "hf_splitter"
        assert m_from_hf.call_args.kwargs["chunk_size"] == 512 - _HF_TOKEN_SAFETY_MARGIN

    def test_openai_text_embedding_falls_back_to_chars(self):
        # OpenAI's text-embedding-3-* is NOT on the HF allow-list (cl100k
        # is correct for it via tiktoken). The splitter falls back to the
        # char-based path — same behavior as before this fix.
        eng = _make_engine(provider="openai", model="text-embedding-3-small")
        splitter = eng._build_text_splitter(chunk_size=800, chunk_overlap=100)
        # Identity check: not the from_huggingface_tokenizer variant.
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        assert isinstance(splitter, RecursiveCharacterTextSplitter)

    def test_cohere_voyage_gemini_fall_back_to_chars(self):
        # These have no HF tokenizer repos — char-based fallback.
        for provider, model in [
            ("litellm", "cohere/embed-english-v3.0"),
            ("litellm", "voyage/voyage-3"),
            ("litellm", "gemini/text-embedding-004"),
        ]:
            eng = _make_engine(provider=provider, model=model)
            splitter = eng._build_text_splitter(chunk_size=800, chunk_overlap=100)
            from langchain_text_splitters import RecursiveCharacterTextSplitter

            assert isinstance(splitter, RecursiveCharacterTextSplitter)

    def test_fastembed_provider_falls_back_to_chars(self):
        # fastembed's _build_docling_chunker uses the ONNX tokenizer, but
        # the plain-text path doesn't have access to that. Char-based is
        # the appropriate fallback. For fastembed/bge-small, 800 chars ≈
        # 200 tokens — fits 512-token max easily, so no truncation risk.
        eng = _make_engine(provider="fastembed", model="BAAI/bge-small-en-v1.5")
        splitter = eng._build_text_splitter(chunk_size=800, chunk_overlap=100)
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        assert isinstance(splitter, RecursiveCharacterTextSplitter)

    def test_hf_load_failure_falls_back_to_chars(self):
        # AutoTokenizer raises (e.g. offline, gated repo). The splitter
        # must NOT crash; falls through to char-based behavior.
        eng = _make_engine(
            provider="litellm",
            model="watsonx/intfloat/multilingual-e5-large",
        )

        with patch(
            "transformers.AutoTokenizer.from_pretrained",
            side_effect=OSError("offline"),
        ):
            splitter = eng._build_text_splitter(chunk_size=800, chunk_overlap=100)

        from langchain_text_splitters import RecursiveCharacterTextSplitter

        assert isinstance(splitter, RecursiveCharacterTextSplitter)


class TestResplitTriggerThreshold:
    """The post-Docling re-split should NOT fire for correctly-bounded
    HybridChunker output. Only for pathological mega-chunks."""

    def test_emergency_threshold_is_100k_not_chunk_size_x2(self):
        # The pre-fix threshold was `chunk_size * 2`. For chunk_size=800
        # that's 1600 chars — easily exceeded by HybridChunker's normal
        # 512-token output in English content (~1500-2000 chars).
        # Post-fix the threshold is 100k chars — well past any real
        # HybridChunker output. We assert the constant directly by
        # reading the source rather than running a full file-load (which
        # would need Docling).
        import inspect

        from cuga.backend.knowledge import engine as eng

        src = inspect.getsource(eng.KnowledgeEngine._load_document)
        assert "_EMERGENCY_CHAR_THRESHOLD = 100_000" in src, (
            "Re-split trigger reverted to a small threshold — would re-split "
            "normal HybridChunker output and re-introduce the unit-mismatch bug."
        )

    def test_resplit_uses_token_aware_splitter(self):
        # When the emergency threshold IS exceeded, the splitter is the
        # token-aware one (via _build_text_splitter), not a raw
        # char-based RecursiveCharacterTextSplitter with chunk_size as
        # the user-config token-target.
        import inspect

        from cuga.backend.knowledge import engine as eng

        src = inspect.getsource(eng.KnowledgeEngine._load_document)
        # The block uses _build_text_splitter for the emergency case.
        assert "self._build_text_splitter(chunk_size, chunk_overlap)" in src
