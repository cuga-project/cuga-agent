"""A config change to an embedding model that fails to load (e.g. a large local
model still downloading) must raise the typed EmbeddingModelLoadError from
prepare_knowledge_update — so the publish route returns an actionable 400 instead
of an opaque 500."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

import cuga.backend.knowledge.engine as E
from cuga.backend.knowledge.config import KnowledgeConfig
from cuga.backend.knowledge.engine import EmbeddingModelLoadError, KnowledgeEngine


def test_prepare_knowledge_update_wraps_embedder_load_failure(monkeypatch):
    cfg = KnowledgeConfig(
        enabled=True,
        persist_dir=Path(tempfile.mkdtemp(prefix="cuga-emb-")),
        embedding_provider="fastembed",
        embedding_model="BAAI/bge-small-en-v1.5",
    )
    eng = KnowledgeEngine(cfg)

    def _boom(_validated):
        # mimic the ONNX external-data failure seen for multilingual-e5-large mid-download
        raise RuntimeError("[ONNXRuntimeError] external initializer ... Not a directory (value: 20)")

    monkeypatch.setattr(E, "create_embeddings", _boom)

    with pytest.raises(EmbeddingModelLoadError) as ei:
        eng.prepare_knowledge_update({"embedding_model": "intfloat/multilingual-e5-large"})

    assert ei.value.provider == "fastembed"
    assert "multilingual-e5-large" in str(ei.value)
    assert isinstance(ei.value.cause, RuntimeError)


def test_prepare_knowledge_update_ok_when_model_unchanged(monkeypatch):
    # No embedding change → create_embeddings is never called, so a broken loader
    # can't affect an unrelated config edit.
    cfg = KnowledgeConfig(
        enabled=True,
        persist_dir=Path(tempfile.mkdtemp(prefix="cuga-emb-")),
        embedding_provider="fastembed",
        embedding_model="BAAI/bge-small-en-v1.5",
    )
    eng = KnowledgeEngine(cfg)
    monkeypatch.setattr(E, "create_embeddings", lambda _v: (_ for _ in ()).throw(AssertionError("called")))
    prepared = eng.prepare_knowledge_update({"default_limit": 7})  # search-only change
    assert prepared.embedding_changed is False
