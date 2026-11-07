"""Default embedder: multilingual-e5-small as an int8 ONNX model, and knowledge bases built with
another model are re-embedded before use instead of being searched with incompatible vectors."""

import asyncio

import pytest

from cuga.backend.knowledge.config import KnowledgeConfig, load_profile
from cuga.backend.knowledge.engine import KnowledgeEngine
from cuga.backend.knowledge.fastembed_models import register

pytestmark = pytest.mark.unit

E5 = "intfloat/multilingual-e5-small"


def test_e5_small_registers_with_fastembed_once():
    from fastembed import TextEmbedding

    register(E5)
    register(E5)
    models = [m for m in TextEmbedding.list_supported_models() if m["model"] == E5]
    assert len(models) == 1 and models[0]["dim"] == 384


@pytest.mark.parametrize("profile", ["standard", "speed"])
def test_default_profiles_use_the_multilingual_model(profile):
    assert load_profile(profile)["embeddings"]["model"] == E5


def _engine(tmp_path, pinned_model, reindex_result=None):
    eng = KnowledgeEngine(KnowledgeConfig(enabled=True, persist_dir=tmp_path, embedding_model=E5))
    calls = []

    async def get_collection_config(collection):
        return {"embedding_provider": "fastembed", "embedding_model": pinned_model}

    async def reindex(collection):
        calls.append(collection)
        if reindex_result:
            return reindex_result
        eng._reindex_in_progress.add(collection)
        asyncio.get_running_loop().call_later(0.3, eng._reindex_in_progress.discard, collection)
        return {"status": "started"}

    eng._metadata.get_collection_config = get_collection_config
    eng.reindex = reindex
    return eng, calls


def test_a_collection_built_with_another_model_is_re_embedded_and_waited_for(tmp_path):
    eng, calls = _engine(tmp_path, "BAAI/bge-small-en-v1.5")
    asyncio.run(eng._reembed_if_model_changed("kb_agent_default"))
    assert calls == ["kb_agent_default"]
    assert "kb_agent_default" not in eng._reindex_in_progress


@pytest.mark.parametrize(
    ("collection", "pinned"),
    [("kb_agent_default", E5), ("kb_agent_cuga_default_29bf621b0ad3", "BAAI/bge-small-en-v1.5")],
)
def test_same_model_or_a_hashed_collection_is_left_alone(tmp_path, collection, pinned):
    eng, calls = _engine(tmp_path, pinned)
    asyncio.run(eng._reembed_if_model_changed(collection))
    assert calls == []


def test_nothing_stored_to_re_embed_does_not_wait(tmp_path):
    eng, calls = _engine(
        tmp_path, "BAAI/bge-small-en-v1.5", reindex_result={"status": "no_documents", "count": 0}
    )
    asyncio.run(asyncio.wait_for(eng._reembed_if_model_changed("kb_sess_abc"), timeout=5))
    assert calls == ["kb_sess_abc"]


@pytest.mark.parametrize(
    ("saved", "env", "expected"),
    [
        (
            {"knowledge": {"embedding_provider": "fastembed", "embedding_model": "BAAI/bge-small-en-v1.5"}},
            {},
            ("fastembed", "BAAI/bge-small-en-v1.5"),
        ),
        (
            {"knowledge": {"embedding_provider": "fastembed", "embedding_model": "BAAI/bge-small-en-v1.5"}},
            {"DYNACONF_KNOWLEDGE__EMBEDDINGS__MODEL": E5},
            None,
        ),
        (None, {}, None),
    ],
)
def test_restart_keeps_the_saved_embedder_unless_this_start_sets_one(monkeypatch, saved, env, expected):
    """Documents survive restarts, so re-seeding keeps the model they were indexed with."""
    from cuga.backend.server import config_store, demo_manage_setup

    for key in ("DYNACONF_KNOWLEDGE__EMBEDDINGS__PROVIDER", "DYNACONF_KNOWLEDGE__EMBEDDINGS__MODEL"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    async def load_config(version, agent_id):
        return saved, "1"

    monkeypatch.setattr(config_store, "load_config", load_config)
    assert asyncio.run(demo_manage_setup._saved_embedding("cuga-default")) == expected
