"""Default embedder: multilingual-e5-small as an int8 ONNX model. Knowledge bases built with
another model keep it, and a restart keeps a saved agent's model unless this start names another."""

import asyncio

import pytest

from cuga.backend.knowledge.config import KnowledgeConfig, load_profile
from cuga.backend.knowledge.engine import KnowledgeEngine
from cuga.backend.knowledge.fastembed_models import register

pytestmark = pytest.mark.unit

E5 = "intfloat/multilingual-e5-small"
SAVED_BGE = {"knowledge": {"embedding_provider": "fastembed", "embedding_model": "BAAI/bge-small-en-v1.5"}}


def test_e5_small_registers_with_fastembed_once():
    from fastembed import TextEmbedding

    register(E5)
    register(E5)
    models = [m for m in TextEmbedding.list_supported_models() if m["model"] == E5]
    assert len(models) == 1 and models[0]["dim"] == 384


@pytest.mark.parametrize("profile", ["standard", "speed"])
def test_default_profiles_use_the_multilingual_model(profile):
    assert load_profile(profile)["embeddings"]["model"] == E5


def test_a_collection_built_with_another_model_keeps_that_model(tmp_path, monkeypatch):
    """A knowledge base built before the default changed is still searched with its own model."""
    from cuga.backend.knowledge import engine as engine_mod

    eng = KnowledgeEngine(KnowledgeConfig(enabled=True, persist_dir=tmp_path, embedding_model=E5))
    monkeypatch.setattr(eng, "_ensure_embeddings", lambda: None)

    async def get_collection_config(collection):
        return {"embedding_provider": "fastembed", "embedding_model": "BAAI/bge-small-en-v1.5"}

    monkeypatch.setattr(eng._metadata, "get_collection_config", get_collection_config)
    monkeypatch.setattr(engine_mod, "create_embeddings", lambda cfg: cfg.embedding_model)
    assert asyncio.run(eng._resolve_embeddings_for_collection("kb_agent_default")) == "BAAI/bge-small-en-v1.5"


@pytest.mark.parametrize(
    ("saved", "env", "expected"),
    [
        (SAVED_BGE, {}, ("fastembed", "BAAI/bge-small-en-v1.5")),
        (
            SAVED_BGE,
            {"DYNACONF_KNOWLEDGE__EMBEDDINGS__PROVIDER": "fastembed"},
            ("fastembed", "BAAI/bge-small-en-v1.5"),
        ),
        (SAVED_BGE, {"DYNACONF_KNOWLEDGE__EMBEDDINGS__PROVIDER": "huggingface"}, None),
        (SAVED_BGE, {"DYNACONF_KNOWLEDGE__EMBEDDINGS__MODEL": E5}, None),
        (SAVED_BGE, {"DYNACONF_KNOWLEDGE__EMBEDDINGS__MODEL": "  "}, ("fastembed", "BAAI/bge-small-en-v1.5")),
        (None, {}, None),
    ],
)
def test_restart_keeps_the_saved_embedder_unless_this_start_sets_one(monkeypatch, saved, env, expected):
    """Documents survive restarts, so re-seeding keeps the model they were indexed with. Naming
    the same provider keeps the saved model; another provider or a model replaces it."""
    from cuga.backend.server import config_store, demo_manage_setup

    for key in ("DYNACONF_KNOWLEDGE__EMBEDDINGS__PROVIDER", "DYNACONF_KNOWLEDGE__EMBEDDINGS__MODEL"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    async def load_config(version, agent_id):
        return saved, "1"

    monkeypatch.setattr(config_store, "load_config", load_config)
    assert asyncio.run(demo_manage_setup._saved_embedding("cuga-default")) == expected
