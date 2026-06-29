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
from cuga.backend.knowledge.engine import (
    KnowledgeEngine,
    _warn_unlisted_embedder_once,
)


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


class TestCanaryLogParity:
    """Splitter's char-fallback fires the same one-shot WARNING the
    chunker fires, so an unlisted-HF-style model on litellm/openrouter
    is observable via operator logs even when documents flow through
    plain-text or emergency-resplit paths instead of HybridChunker."""

    def setup_method(self):
        _warn_unlisted_embedder_once.cache_clear()

    def _capture_warns(self, monkeypatch):
        from cuga.backend.knowledge import engine as eng

        calls: list[str] = []
        monkeypatch.setattr(eng.logger, "warning", lambda msg, *a, **k: calls.append(msg))
        return calls

    def test_canary_fires_for_unlisted_litellm_slash_model(self, monkeypatch):
        eng = _make_engine(provider="litellm", model="mistralai/mistral-embed-x")
        calls = self._capture_warns(monkeypatch)
        eng._build_text_splitter(chunk_size=800, chunk_overlap=100)
        canary_msgs = [c for c in calls if "allow-list" in c]
        assert len(canary_msgs) == 1, f"expected 1 canary, got {canary_msgs}"
        assert "mistralai/mistral-embed-x" in canary_msgs[0]

    def test_canary_does_not_fire_for_openai_via_litellm(self, monkeypatch):
        # The tiktoken branch handles this correctly — no canary noise.
        eng = _make_engine(provider="litellm", model="openai/text-embedding-3-small")
        calls = self._capture_warns(monkeypatch)
        eng._build_text_splitter(chunk_size=800, chunk_overlap=100)
        canary_msgs = [c for c in calls if "allow-list" in c]
        assert len(canary_msgs) == 0

    def test_canary_does_not_fire_for_azure_via_litellm(self, monkeypatch):
        eng = _make_engine(provider="litellm", model="azure/my-deployment")
        calls = self._capture_warns(monkeypatch)
        eng._build_text_splitter(chunk_size=800, chunk_overlap=100)
        canary_msgs = [c for c in calls if "allow-list" in c]
        assert len(canary_msgs) == 0

    def test_canary_does_not_fire_for_hf_listed(self, monkeypatch):
        # HF allow-list took the splitter via from_huggingface_tokenizer.
        eng = _make_engine(provider="litellm", model="watsonx/intfloat/multilingual-e5-large")
        calls = self._capture_warns(monkeypatch)
        from types import SimpleNamespace

        with patch(
            "transformers.AutoTokenizer.from_pretrained",
            return_value=SimpleNamespace(model_max_length=512),
        ):
            eng._build_text_splitter(chunk_size=800, chunk_overlap=100)
        canary_msgs = [c for c in calls if "allow-list" in c]
        assert len(canary_msgs) == 0

    def test_canary_does_not_fire_for_fastembed(self, monkeypatch):
        # fastembed is a local provider — char-based fallback is fine,
        # no operator action required.
        eng = _make_engine(provider="fastembed", model="BAAI/bge-small-en-v1.5")
        calls = self._capture_warns(monkeypatch)
        eng._build_text_splitter(chunk_size=800, chunk_overlap=100)
        canary_msgs = [c for c in calls if "allow-list" in c]
        assert len(canary_msgs) == 0

    def test_canary_dedup_across_repeated_splitter_builds(self, monkeypatch):
        # Multiple _load_document calls under the same unlisted embedder
        # must produce ONE canary, not one-per-document.
        eng = _make_engine(provider="litellm", model="snowflake/arctic-embed-l")
        calls = self._capture_warns(monkeypatch)
        for _ in range(5):
            eng._build_text_splitter(chunk_size=800, chunk_overlap=100)
        canary_msgs = [c for c in calls if "allow-list" in c]
        assert len(canary_msgs) == 1, f"expected dedup to 1, got {len(canary_msgs)}"

    def test_canary_separates_chunker_and_splitter_origins(self, monkeypatch):
        # Same (provider, model) but different encoding-name keys: chunker
        # fires once with "cl100k_base", splitter fires once with
        # "char_based_split". Both ONCE.
        from cuga.backend.knowledge.engine import _warn_unlisted_embedder_once

        _warn_unlisted_embedder_once.cache_clear()
        calls = self._capture_warns(monkeypatch)
        _warn_unlisted_embedder_once("litellm", "orgX/embedder", "cl100k_base")
        _warn_unlisted_embedder_once("litellm", "orgX/embedder", "char_based_split")
        # Second call to either sentinel: deduped.
        _warn_unlisted_embedder_once("litellm", "orgX/embedder", "cl100k_base")
        _warn_unlisted_embedder_once("litellm", "orgX/embedder", "char_based_split")
        canary_msgs = [c for c in calls if "allow-list" in c]
        assert len(canary_msgs) == 2, (
            f"chunker + splitter should each fire ONCE per process; got {canary_msgs}"
        )
