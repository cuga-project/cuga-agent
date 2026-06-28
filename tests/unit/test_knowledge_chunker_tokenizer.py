"""Lock the tiktoken chunker tokenizer behavior for OpenAI-family providers.

Verifies the consensus from the 3-RAG-expert review:
  - tokenizer-match: chunker uses the SAME tokenizer family as the embedder
  - no MiniLM HF download for cloud providers
  - graceful fallback for unrecognized models (cl100k_base)
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cuga.backend.knowledge.config import KnowledgeConfig
from cuga.backend.knowledge.engine import (
    KnowledgeEngine,
    _resolve_tiktoken_encoding,
)


def _cfg(**over):
    d = dict(
        enabled=True,
        persist_dir=Path(tempfile.mkdtemp(prefix="cuga-tok-")),
        embedding_provider="openai",
        embedding_model="text-embedding-3-small",
        embedding_api_key="sk-test",
    )
    d.update(over)
    return KnowledgeConfig(**d)


# ============================================================
# _resolve_tiktoken_encoding — picks the right encoding per model
# ============================================================


def test_resolve_encoding_for_openai_models():
    enc = _resolve_tiktoken_encoding("text-embedding-3-small")
    assert enc.name == "cl100k_base"


def test_resolve_encoding_strips_provider_prefix():
    """LiteLLM-style names like 'openai/text-embedding-3-small' must resolve."""
    enc = _resolve_tiktoken_encoding("openai/text-embedding-3-small")
    assert enc.name == "cl100k_base"


def test_resolve_encoding_strips_azure_prefix():
    """IBM proxy uses Azure/ prefix (note: case-stripped by .lower()) — must resolve."""
    enc = _resolve_tiktoken_encoding("Azure/text-embedding-3-small-1")
    # Model not in tiktoken registry → fallback to cl100k_base, which is the
    # right encoding for Azure-routed text-embedding-3-* anyway.
    assert enc.name == "cl100k_base"


def test_resolve_encoding_fallback_for_unknown_model():
    """Unknown models (e.g. cohere via openrouter) must fall back, not crash."""
    enc = _resolve_tiktoken_encoding("cohere/embed-english-v3.0")
    assert enc.name == "cl100k_base"


def test_resolve_encoding_empty_model_falls_back():
    """Empty model name (provider default) must fall back cleanly."""
    enc = _resolve_tiktoken_encoding("")
    assert enc.name == "cl100k_base"


# ============================================================
# _build_docling_chunker — wires the right tokenizer per provider
# ============================================================


@pytest.mark.parametrize("provider", ["openai", "openrouter", "litellm"])
def test_chunker_uses_tiktoken_for_openai_family(provider):
    """For openai/openrouter/litellm the chunker MUST use tiktoken, not MiniLM."""
    model_cfg = {
        "openai": "text-embedding-3-small",
        "openrouter": "openai/text-embedding-3-small",
        "litellm": "openai/text-embedding-3-small",
    }[provider]
    eng = KnowledgeEngine(
        _cfg(
            embedding_provider=provider,
            embedding_model=model_cfg,
            embedding_api_key="k",  # required for openrouter
        )
    )
    # Patch HybridChunker so we can inspect what tokenizer it gets
    with patch("docling_core.transforms.chunker.HybridChunker") as MockHC:
        eng._build_docling_chunker(chunk_size=1000)
    assert MockHC.called
    # Must be tokenizer=<our tiktoken wrapper>, NOT max_tokens=fallback path
    call = MockHC.call_args
    assert "tokenizer" in call.kwargs, (
        f"Expected tiktoken tokenizer wired in; got call={call!r}. "
        "Provider must not fall back to MiniLM download path."
    )
    tok = call.kwargs["tokenizer"]
    # The tokenizer's count_tokens must be backed by tiktoken
    assert hasattr(tok, "encoding")
    assert tok.encoding.name == "cl100k_base"
    assert tok.max_tokens == 1000


def test_chunker_token_count_matches_tiktoken_directly():
    """Sanity: our wrapper's count_tokens must match tiktoken.encode(text).len."""
    import tiktoken
    from cuga.backend.knowledge.engine import _tiktoken_docling_tokenizer_cls

    encoding = tiktoken.get_encoding("cl100k_base")
    tok = _tiktoken_docling_tokenizer_cls()(encoding=encoding, max_tokens=512)
    text = "The quick brown fox jumps over the lazy dog. " * 20
    assert tok.count_tokens(text) == len(encoding.encode(text))


def test_chunker_for_fastembed_still_uses_fastembed_tokenizer():
    """fastembed path must remain unchanged — uses the ONNX tokenizer that
    matches its embedding model exactly."""
    eng = KnowledgeEngine(
        _cfg(
            embedding_provider="fastembed",
            embedding_model="",
        )
    )
    # The fastembed branch calls _ensure_embeddings(). Stub it.
    from cuga.backend.knowledge.engine import _FastEmbedEmbeddings

    fake_emb = MagicMock(spec=_FastEmbedEmbeddings)
    inner = MagicMock()
    inner.tokenizer = MagicMock()
    fake_emb._model = MagicMock(model=inner, model_name="BAAI/bge-small-en-v1.5")
    eng._default_embeddings = fake_emb
    eng._ensure_embeddings = MagicMock()

    with patch("docling_core.transforms.chunker.HybridChunker") as MockHC:
        eng._build_docling_chunker(chunk_size=1000)
    assert MockHC.called
    # fastembed branch uses tokenizer=<fastembed wrapper>, also tokenizer kw
    assert "tokenizer" in MockHC.call_args.kwargs


def test_chunker_for_ollama_uses_tiktoken_zero_download():
    """Ollama path was downloading MiniLM (~90 MB). Now it uses tiktoken (0 MB).
    Approximation, but free."""
    eng = KnowledgeEngine(_cfg(embedding_provider="ollama", embedding_model="nomic-embed-text"))
    with patch("docling_core.transforms.chunker.HybridChunker") as MockHC:
        eng._build_docling_chunker(chunk_size=1000)
    assert MockHC.called
    call = MockHC.call_args
    assert "tokenizer" in call.kwargs, (
        "ollama path must NOT fall back to MiniLM download — should use tiktoken"
    )
    tok = call.kwargs["tokenizer"]
    assert tok.encoding.name == "cl100k_base"


def test_chunker_for_huggingface_uses_own_tokenizer():
    """When provider=huggingface, reuse the loaded HF tokenizer from
    _PyTorchEmbeddings — perfect match, zero extra cost (already in memory)."""
    eng = KnowledgeEngine(
        _cfg(
            embedding_provider="huggingface",
            embedding_model="BAAI/bge-small-en-v1.5",
        )
    )
    fake_tokenizer = MagicMock()
    fake_emb = MagicMock()
    fake_emb._tokenizer = fake_tokenizer
    eng._default_embeddings = fake_emb
    eng._ensure_embeddings = MagicMock()

    with patch("docling_core.transforms.chunker.HybridChunker") as MockHC:
        with patch("docling_core.transforms.chunker.tokenizer.huggingface.HuggingFaceTokenizer") as MockHFTok:
            eng._build_docling_chunker(chunk_size=1000)

    MockHFTok.assert_called_once()
    # The wrapper must be constructed with the SAME tokenizer the embedding
    # wrapper already loaded — no new download / model load.
    assert MockHFTok.call_args.kwargs.get("tokenizer") is fake_tokenizer
    assert MockHC.called
    assert "tokenizer" in MockHC.call_args.kwargs


def test_chunker_never_triggers_minilm_download():
    """Hard guarantee: for EVERY production provider, the chunker constructs
    HybridChunker with an explicit tokenizer= argument. The default ``HybridChunker()``
    path (which silently triggers a 90 MB MiniLM download) must never be hit."""
    for provider, model in [
        ("openai", "text-embedding-3-small"),
        ("openrouter", "openai/text-embedding-3-small"),
        ("litellm", "openai/text-embedding-3-small"),
        ("ollama", "nomic-embed-text"),
    ]:
        eng = KnowledgeEngine(
            _cfg(
                embedding_provider=provider,
                embedding_model=model,
                embedding_api_key="k",
            )
        )
        with patch("docling_core.transforms.chunker.HybridChunker") as MockHC:
            eng._build_docling_chunker(chunk_size=1000)
        assert "tokenizer" in MockHC.call_args.kwargs, (
            f"provider={provider} fell through to default HybridChunker() — would trigger MiniLM download"
        )
