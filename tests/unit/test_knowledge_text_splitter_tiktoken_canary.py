"""Tests for PR B (#383 follow-up): extend ``_build_text_splitter`` with

1. ``from_tiktoken_encoder`` branch for openai/azure-native routes —
   including ``litellm/openai/*`` and ``litellm/azure/*``. cl100k_base
   is the EXACT correct unit for ``text-embedding-3-*`` and
   ``text-embedding-ada-002``; the prior char-based fallback wasted
   the precision we already had.

2. ``_warn_unlisted_embedder_once`` canary on the splitter's
   char-based fallback path. Mirrors the canary already firing in
   ``_build_docling_chunker``'s tiktoken-fallback branch — silent
   degradation in the splitter was a parallel surface that #387's
   follow-up didn't cover.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from cuga.backend.knowledge.config import KnowledgeConfig
from cuga.backend.knowledge.engine import KnowledgeEngine


def _make_engine(provider: str, model: str) -> KnowledgeEngine:
    tmp = tempfile.mkdtemp(prefix="cuga-tiktoken-canary-test-")
    cfg = KnowledgeConfig(
        enabled=True,
        persist_dir=Path(tmp),
        embedding_provider=provider,
        embedding_model=model,
    )
    return KnowledgeEngine(cfg)


class TestTiktokenBranch:
    """openai-native + azure routes (incl. litellm/openrouter-routed) now
    use ``from_tiktoken_encoder`` with cl100k_base — the exact unit, not
    a char-based approximation."""

    def setup_method(self):
        # Clear the HF-tokenizer cache so a leaked tokenizer from a
        # previous test doesn't make the HF branch shortcut and bypass
        # the tiktoken path we're verifying.
        from cuga.backend.knowledge.engine import _load_hf_tokenizer_for_chunking

        _load_hf_tokenizer_for_chunking.cache_clear()

    def test_openai_native_uses_tiktoken(self):
        eng = _make_engine(provider="openai", model="text-embedding-3-small")
        with patch("langchain_text_splitters.RecursiveCharacterTextSplitter.from_tiktoken_encoder") as m:
            m.return_value = "tiktoken_splitter"
            result = eng._build_text_splitter(chunk_size=800, chunk_overlap=100)
        assert result == "tiktoken_splitter"
        assert m.call_args.kwargs["encoding_name"] == "cl100k_base"
        assert m.call_args.kwargs["chunk_size"] == 800
        assert m.call_args.kwargs["chunk_overlap"] == 100

    def test_litellm_openai_uses_tiktoken(self):
        # litellm/openai/text-embedding-3-* — strip litellm/, see openai/,
        # route to tiktoken.
        eng = _make_engine(provider="litellm", model="openai/text-embedding-3-large")
        with patch("langchain_text_splitters.RecursiveCharacterTextSplitter.from_tiktoken_encoder") as m:
            m.return_value = "tiktoken_splitter"
            result = eng._build_text_splitter(chunk_size=800, chunk_overlap=100)
        assert result == "tiktoken_splitter"
        assert m.call_args.kwargs["encoding_name"] == "cl100k_base"

    def test_litellm_azure_uses_tiktoken(self):
        eng = _make_engine(provider="litellm", model="azure/my-deployment")
        with patch("langchain_text_splitters.RecursiveCharacterTextSplitter.from_tiktoken_encoder") as m:
            m.return_value = "tiktoken_splitter"
            result = eng._build_text_splitter(chunk_size=800, chunk_overlap=100)
        assert result == "tiktoken_splitter"

    def test_openrouter_openai_uses_tiktoken(self):
        eng = _make_engine(provider="openrouter", model="openai/text-embedding-3-small")
        with patch("langchain_text_splitters.RecursiveCharacterTextSplitter.from_tiktoken_encoder") as m:
            m.return_value = "tiktoken_splitter"
            result = eng._build_text_splitter(chunk_size=800, chunk_overlap=100)
        assert result == "tiktoken_splitter"

    def test_hf_listed_takes_priority_over_tiktoken(self):
        # litellm + watsonx/intfloat/multilingual-e5-large is on the HF
        # allow-list — must NOT be misrouted to tiktoken even though it
        # also goes through the same provider==litellm branch.
        eng = _make_engine(provider="litellm", model="watsonx/intfloat/multilingual-e5-large")
        with patch("transformers.AutoTokenizer.from_pretrained") as m_hf:
            from types import SimpleNamespace

            m_hf.return_value = SimpleNamespace(model_max_length=512)
            with patch(
                "langchain_text_splitters.RecursiveCharacterTextSplitter.from_huggingface_tokenizer"
            ) as m_hf_splitter:
                m_hf_splitter.return_value = "hf_splitter"
                with patch(
                    "langchain_text_splitters.RecursiveCharacterTextSplitter.from_tiktoken_encoder"
                ) as m_tiktoken:
                    result = eng._build_text_splitter(chunk_size=800, chunk_overlap=100)
        assert result == "hf_splitter"
        m_tiktoken.assert_not_called()

    def test_tiktoken_failure_falls_through_to_chars(self):
        # Defensive: if tiktoken's encoder isn't loadable for some reason,
        # the splitter must not crash.
        eng = _make_engine(provider="openai", model="text-embedding-3-small")
        with patch(
            "langchain_text_splitters.RecursiveCharacterTextSplitter.from_tiktoken_encoder",
            side_effect=RuntimeError("simulated tiktoken failure"),
        ):
            splitter = eng._build_text_splitter(chunk_size=800, chunk_overlap=100)
        # Falls through to char-based.
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        assert isinstance(splitter, RecursiveCharacterTextSplitter)


# NOTE: ``TestCanaryLogParity`` was deleted in PR-A (workflow w9y9xtyse
# synth). The custom canary ``_warn_unlisted_embedder_once`` is gone —
# the existing ``logger.warning`` inside
# ``_load_hf_tokenizer_for_chunking`` (engine.py) is now the sole
# unknown-model signal, fired once per process per repo via
# ``functools.lru_cache(maxsize=8)``. Coverage for that lives in
# ``test_knowledge_chunker_hf_tokenizer.py::TestLoadHfTokenizerCachesFailure``.
