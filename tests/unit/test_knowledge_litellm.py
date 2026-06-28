"""LiteLLM as a first-class embedding provider.

Covers the same axes as the OpenRouter integration: validation, snapshot
round-trip, model-required guard, OpenAI-key fallback for ``openai/...``
models, and an offline mock of the actual ``litellm.embedding`` call.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cuga.backend.knowledge.config import KnowledgeConfig
from cuga.backend.knowledge.engine import _LiteLLMEmbeddings, create_embeddings


def _cfg(**over):
    d = dict(
        enabled=True,
        persist_dir=Path(tempfile.mkdtemp(prefix="cuga-litellm-")),
        embedding_provider="litellm",
        embedding_model="openai/text-embedding-3-small",
    )
    d.update(over)
    return KnowledgeConfig(**d)


# ----- Validation -----


def test_litellm_provider_passes_validation():
    cfg = _cfg()
    cfg.validate()  # must not raise


def test_litellm_provider_without_model_rejected():
    with pytest.raises(ValueError, match="LiteLLM embedding provider requires"):
        _cfg(embedding_model="").validate()


def test_litellm_in_known_providers_list():
    # Anything in the typer/UI dropdown must pass validate().
    for m in ("openai/text-embedding-3-small", "cohere/embed-english-v3.0", "azure/x"):
        _cfg(embedding_model=m).validate()


# ----- Snapshot round-trip -----


def test_litellm_snapshot_roundtrip():
    cfg = _cfg(embedding_model="cohere/embed-english-v3.0", embedding_api_key="k")
    snap = cfg.to_dict()
    assert snap["embedding_provider"] == "litellm"
    assert snap["embedding_model"] == "cohere/embed-english-v3.0"
    cfg2 = KnowledgeConfig.coerce_and_validate(snap, base=cfg)
    assert cfg2.embedding_provider == "litellm"
    assert cfg2.embedding_model == "cohere/embed-english-v3.0"
    assert cfg2.embedding_api_key == "k"


# ----- Adapter behaviour with mocked litellm.embedding -----


def _mock_response(vectors):
    """Build an OpenAI-shaped EmbeddingResponse from a list of vectors."""
    resp = MagicMock()
    resp.data = [{"embedding": v, "index": i} for i, v in enumerate(vectors)]
    return resp


def test_adapter_embed_documents_returns_in_input_order():
    emb = _LiteLLMEmbeddings(model="openai/text-embedding-3-small", api_key="sk-fake")
    with patch(
        "litellm.embedding",
        return_value=_mock_response([[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]]),
    ) as m:
        vecs = emb.embed_documents(["a", "b", "c"])
    assert vecs == [[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]]
    assert m.call_args.kwargs["model"] == "openai/text-embedding-3-small"
    assert m.call_args.kwargs["api_key"] == "sk-fake"
    assert m.call_args.kwargs["input"] == ["a", "b", "c"]


def test_adapter_reorders_when_litellm_returns_out_of_order():
    """LiteLLM doesn't guarantee data ordering — the adapter must sort by index."""
    resp = MagicMock()
    # Deliberately shuffle: index=2 first, then 0, then 1.
    resp.data = [
        {"embedding": [9, 9], "index": 2},
        {"embedding": [1, 1], "index": 0},
        {"embedding": [5, 5], "index": 1},
    ]
    emb = _LiteLLMEmbeddings(model="openai/text-embedding-3-small")
    with patch("litellm.embedding", return_value=resp):
        vecs = emb.embed_documents(["a", "b", "c"])
    assert vecs == [[1, 1], [5, 5], [9, 9]]


def test_adapter_raises_on_size_mismatch():
    emb = _LiteLLMEmbeddings(model="openai/x")
    with patch("litellm.embedding", return_value=_mock_response([[0.1]])):
        with pytest.raises(RuntimeError, match="litellm returned 1 vectors for 3 inputs"):
            emb.embed_documents(["a", "b", "c"])


def test_adapter_passes_base_url_for_self_hosted_proxy():
    emb = _LiteLLMEmbeddings(
        model="openai/x",
        api_key="k",
        base_url="https://litellm-proxy.internal:4000",
    )
    with patch("litellm.embedding", return_value=_mock_response([[0.1]])) as m:
        emb.embed_documents(["a"])
    assert m.call_args.kwargs["api_base"] == "https://litellm-proxy.internal:4000"


def test_adapter_rejects_remote_http_base_url_by_default():
    with pytest.raises(ValueError, match="must use https:// for remote hosts"):
        _LiteLLMEmbeddings(
            model="openai/x",
            api_key="k",
            base_url="http://litellm-proxy.internal:4000",
        )


def test_adapter_allows_local_http_base_url():
    emb = _LiteLLMEmbeddings(
        model="openai/x",
        api_key="k",
        base_url="http://localhost:4000",
    )
    with patch("litellm.embedding", return_value=_mock_response([[0.1]])) as m:
        emb.embed_documents(["a"])
    assert m.call_args.kwargs["api_base"] == "http://localhost:4000"


def test_adapter_allows_explicit_insecure_internal_base_url():
    emb = _LiteLLMEmbeddings(
        model="openai/x",
        api_key="k",
        base_url="http://litellm-proxy.internal:4000",
        extra_params={"allow_insecure_transport": True},
    )
    with patch("litellm.embedding", return_value=_mock_response([[0.1]])) as m:
        emb.embed_documents(["a"])
    assert m.call_args.kwargs["api_base"] == "http://litellm-proxy.internal:4000"
    assert "allow_insecure_transport" not in m.call_args.kwargs


def test_adapter_strips_whitespace_from_inputs():
    """Copy-paste from docs page often leaves trailing whitespace."""
    emb = _LiteLLMEmbeddings(
        model="  openai/text-embedding-3-small  ",
        api_key="  sk-fake  ",
        base_url="  https://x.y  ",
    )
    assert emb._model == "openai/text-embedding-3-small"
    assert emb._api_key == "sk-fake"
    assert emb._base_url == "https://x.y"


def test_adapter_rejects_empty_model():
    with pytest.raises(ValueError, match="explicit model name"):
        _LiteLLMEmbeddings(model="")


# ----- create_embeddings() OpenAI-key fallback -----


def test_create_embeddings_falls_back_to_openai_env_for_openai_prefixed_model(monkeypatch):
    """User picks ``openai/text-embedding-3-small`` without setting embedding_api_key.

    We expect the same UX as the openai provider: fall back to OPENAI_API_KEY env var.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    cfg = _cfg(embedding_api_key="")
    emb = create_embeddings(cfg)
    assert isinstance(emb, _LiteLLMEmbeddings)
    assert emb._api_key == "sk-from-env"


def test_create_embeddings_does_not_fallback_for_non_openai_prefixed(monkeypatch):
    """For e.g. ``cohere/...`` we let LiteLLM read COHERE_API_KEY itself — we don't pre-fill."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-be-used")
    cfg = _cfg(embedding_model="cohere/embed-english-v3.0", embedding_api_key="")
    emb = create_embeddings(cfg)
    assert emb._api_key is None  # LiteLLM will read COHERE_API_KEY internally
