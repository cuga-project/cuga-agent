"""Pins the contract of ``KnowledgeEngine.get_chunking_tokenizer()`` — the
unified dispatch added in PR #400's consolidation pass.

Before the refactor, ``_build_docling_chunker`` and ``_build_text_splitter``
each had their own copy of provider-branching logic (fastembed / huggingface
/ litellm-HF / tiktoken / approximate). After the refactor, both call
``get_chunking_tokenizer()`` and switch on ``ChunkingTokenizer.kind``.

These tests pin the accessor's dispatch table directly — independent of
either consumer. If a future provider gets added, this is the test file
that documents what kind/encoder/name/safe_max_tokens we promise.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cuga.backend.knowledge.config import KnowledgeConfig
from cuga.backend.knowledge.engine import (
    ChunkingTokenizer,
    KnowledgeEngine,
    _HF_TOKEN_SAFETY_MARGIN,
    _load_hf_tokenizer_for_chunking,
)


def _make_engine(provider: str, model: str) -> KnowledgeEngine:
    tmp = tempfile.mkdtemp(prefix="cuga-accessor-test-")
    cfg = KnowledgeConfig(
        enabled=True,
        persist_dir=Path(tmp),
        embedding_provider=provider,
        embedding_model=model,
    )
    return KnowledgeEngine(cfg)


class TestChunkingTokenizerDispatch:
    """One test per provider → expected ``ChunkingTokenizer`` shape."""

    def setup_method(self):
        _load_hf_tokenizer_for_chunking.cache_clear()

    def test_litellm_watsonx_e5_returns_hf_kind(self):
        # The exact #387 / #396 / 518>512 user-bug-triple repro.
        eng = _make_engine(provider="litellm", model="watsonx/intfloat/multilingual-e5-large")
        with patch(
            "transformers.AutoTokenizer.from_pretrained",
            return_value=SimpleNamespace(model_max_length=512),
        ):
            tok = eng.get_chunking_tokenizer()
        assert tok.kind == "hf"
        assert tok.name == "intfloat/multilingual-e5-large"
        # Already margined — chunker / splitter inherit safety automatically.
        assert tok.safe_max_tokens == 512 - _HF_TOKEN_SAFETY_MARGIN

    def test_openrouter_bge_returns_hf_kind(self):
        eng = _make_engine(provider="openrouter", model="BAAI/bge-large-en-v1.5")
        with patch(
            "transformers.AutoTokenizer.from_pretrained",
            return_value=SimpleNamespace(model_max_length=512),
        ):
            tok = eng.get_chunking_tokenizer()
        assert tok.kind == "hf"
        assert tok.name == "BAAI/bge-large-en-v1.5"

    def test_openai_native_returns_tiktoken_kind(self):
        eng = _make_engine(provider="openai", model="text-embedding-3-small")
        tok = eng.get_chunking_tokenizer()
        assert tok.kind == "tiktoken"
        assert tok.name == "cl100k_base"

    def test_litellm_openai_returns_tiktoken_kind(self):
        # litellm/openai/text-embedding-3-* — strip litellm/, see openai/.
        eng = _make_engine(provider="litellm", model="openai/text-embedding-3-large")
        tok = eng.get_chunking_tokenizer()
        assert tok.kind == "tiktoken"
        assert tok.name == "cl100k_base"

    def test_litellm_azure_returns_tiktoken_kind(self):
        eng = _make_engine(provider="litellm", model="azure/my-deployment")
        tok = eng.get_chunking_tokenizer()
        assert tok.kind == "tiktoken"
        assert tok.name == "cl100k_base"

    def test_cohere_returns_approximate_kind(self):
        # Cohere has no HF tokenizer repo + no tiktoken-correct path —
        # ``approximate`` is the explicit "no precise tokenizer" signal.
        eng = _make_engine(provider="litellm", model="cohere/embed-english-v3.0")
        tok = eng.get_chunking_tokenizer()
        assert tok.kind == "approximate"
        assert tok.encoder is None
        assert tok.name == "char-based"

    def test_voyage_returns_approximate_kind(self):
        eng = _make_engine(provider="litellm", model="voyage/voyage-3")
        tok = eng.get_chunking_tokenizer()
        assert tok.kind == "approximate"

    def test_gemini_returns_approximate_kind(self):
        eng = _make_engine(provider="litellm", model="gemini/text-embedding-004")
        tok = eng.get_chunking_tokenizer()
        assert tok.kind == "approximate"

    def test_hf_repo_priority_over_tiktoken(self):
        # litellm + watsonx/intfloat/* — must take HF branch first, not
        # fall through to tiktoken just because the prefix is one we
        # also check for routing detection.
        eng = _make_engine(provider="litellm", model="watsonx/intfloat/multilingual-e5-large")
        with patch(
            "transformers.AutoTokenizer.from_pretrained",
            return_value=SimpleNamespace(model_max_length=512),
        ):
            tok = eng.get_chunking_tokenizer()
        assert tok.kind == "hf"  # NOT "tiktoken"

    def test_hf_load_failure_falls_through(self):
        # AutoTokenizer raises (offline / gated repo). Accessor must not
        # crash; falls back to whatever the next branch matches. For
        # watsonx/intfloat the next branch is the tiktoken check which
        # ALSO fails (intfloat not openai/azure), then approximate.
        eng = _make_engine(provider="litellm", model="watsonx/intfloat/multilingual-e5-large")
        with patch("transformers.AutoTokenizer.from_pretrained", side_effect=OSError("offline")):
            tok = eng.get_chunking_tokenizer()
        assert tok.kind == "approximate"

    def test_ollama_returns_approximate(self):
        # ollama is a valid provider (KnowledgeConfig.validate whitelist)
        # but has no tokenizer the chunker can use — falls into the
        # ``approximate`` bucket so callers handle it via the single
        # ``kind`` switch instead of an ad-hoc unknown-provider branch.
        eng = _make_engine(provider="ollama", model="nomic-embed-text")
        tok = eng.get_chunking_tokenizer()
        assert tok.kind == "approximate"


class TestChunkingTokenizerDataclass:
    """Hashable, immutable, type-stable — small dataclass invariants
    that prevent future regressions to a mutable dict-shaped contract."""

    def test_is_frozen(self):
        tok = ChunkingTokenizer(
            kind="tiktoken",
            encoder=None,
            name="cl100k_base",
            safe_max_tokens=8175,
            recommended_chunk_tokens=512,
        )
        import dataclasses

        try:
            dataclasses.replace(tok, kind="hf")  # via replace is fine
        except Exception:
            assert False, "replace must work on frozen dataclass"
        # Direct mutation must fail.
        try:
            tok.kind = "hf"  # type: ignore[misc]
        except dataclasses.FrozenInstanceError:
            return
        assert False, "direct attribute mutation should have raised"

    def test_hashable(self):
        # frozen dataclasses with hashable fields are hashable — lets
        # callers cache by (provider, model) → ChunkingTokenizer if
        # they want.
        tok = ChunkingTokenizer(
            kind="approximate",
            encoder=None,
            name="char-based",
            safe_max_tokens=8192,
            recommended_chunk_tokens=512,
        )
        assert hash(tok) is not None
