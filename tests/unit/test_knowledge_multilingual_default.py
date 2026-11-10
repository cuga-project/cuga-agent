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
    """A knowledge base built before the default changed is still searched with its own model,
    without loading the default one: offline, with only the old model cached, that load fails."""
    from cuga.backend.knowledge import engine as engine_mod

    eng = KnowledgeEngine(KnowledgeConfig(enabled=True, persist_dir=tmp_path, embedding_model=E5))

    def default_model_not_cached():
        raise ValueError(f"Could not load model {E5} from any source.")

    monkeypatch.setattr(eng, "_ensure_embeddings", default_model_not_cached)

    async def get_collection_config(collection):
        return {"embedding_provider": "fastembed", "embedding_model": "BAAI/bge-small-en-v1.5"}

    monkeypatch.setattr(eng._metadata, "get_collection_config", get_collection_config)
    monkeypatch.setattr(engine_mod, "create_embeddings", lambda cfg: cfg.embedding_model)
    assert asyncio.run(eng._resolve_embeddings_for_collection("kb_agent_default")) == "BAAI/bge-small-en-v1.5"
    asyncio.run(eng._ensure_collection_config("kb_agent_default"))  # an upload into it: no default load


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


def test_a_saved_config_applied_before_the_first_load_still_gets_its_dimension(tmp_path, monkeypatch):
    """At server start the saved config is applied before warmup loads anything. Applying it builds
    the agent's embedder without its dimension; the first load must measure that embedder, not skip
    it, or every new collection is created without a dimension."""
    from cuga.backend.knowledge import engine as engine_mod

    class _Embedder:
        def embed_query(self, text):
            return [0.0] * 7

    built = []
    monkeypatch.setattr(
        engine_mod, "create_embeddings", lambda cfg: built.append(cfg.embedding_model) or _Embedder()
    )
    eng = KnowledgeEngine(KnowledgeConfig(enabled=True, persist_dir=tmp_path, embedding_model=E5))
    eng.apply_knowledge_config(
        {"embedding_provider": "fastembed", "embedding_model": "BAAI/bge-small-en-v1.5"}
    )

    eng._ensure_embeddings()

    assert eng._default_embedding_dim == 7
    assert built == ["BAAI/bge-small-en-v1.5"], "the applied embedder was rebuilt or the default was loaded"


def test_an_upload_into_a_pinned_collection_is_chunked_for_that_model(tmp_path, monkeypatch):
    """Chunks are sized by the model that embeds them. Sized for the default model, a chunk can run
    past the pinned model's token limit, and the text past it is never embedded."""
    pytest.importorskip("sqlite_vec")
    from cuga.backend.knowledge import engine as engine_mod

    class _Words:  # a tokenizer whose tokens are words
        def __init__(self, limit):
            self.model_max_length = limit

        def tokenize(self, text):
            return text.split()

    embedded = []

    class _Embedder:
        def __init__(self, limit):
            self._tokenizer = _Words(limit)

        def embed_documents(self, texts):
            embedded.extend(texts)
            return [[1.0, 0.0, 0.0, 0.0] for _ in texts]

        def embed_query(self, text):
            return [1.0, 0.0, 0.0, 0.0]

    limits = {"default-model": 10_000, "pinned-model": 32}
    built = []
    monkeypatch.setattr(
        engine_mod,
        "create_embeddings",
        lambda cfg: built.append(cfg.embedding_model) or _Embedder(limits[cfg.embedding_model]),
    )
    monkeypatch.setattr(
        engine_mod, "get_storage_connection_params", lambda: ("local", str(tmp_path / "s.db"), "")
    )
    eng = KnowledgeEngine(
        KnowledgeConfig(
            enabled=True,
            persist_dir=tmp_path,
            embedding_provider="huggingface",
            embedding_model="default-model",
            chunk_size=400,
            chunk_overlap=0,
        )
    )
    doc = tmp_path / "doc.txt"
    doc.write_text(" ".join(f"word{i}" for i in range(600)))

    async def upload():
        await eng._ensure_metadata_ready()
        await eng._metadata.set_collection_config("kb", "huggingface", "pinned-model", 4)
        return await eng.ingest("kb", doc)

    assert asyncio.run(upload())["status"] == "completed"
    assert max(len(text.split()) for text in embedded) <= limits["pinned-model"]
    assert built == ["pinned-model"], "the default model was loaded, or the pinned one twice"


def test_server_warmup_waits_for_the_saved_knowledge_config():
    """Warmup loads the model the saved config selects: it starts only after startup applied it."""
    from types import SimpleNamespace

    from cuga.backend.server.main import wait_for_saved_knowledge_config

    async def scenario():
        state = SimpleNamespace(knowledge_startup_config_applied=asyncio.Event())
        warm = asyncio.create_task(wait_for_saved_knowledge_config(state))
        await asyncio.sleep(0.05)
        waiting = not warm.done()
        state.knowledge_startup_config_applied.set()
        await asyncio.wait_for(warm, 1)
        await wait_for_saved_knowledge_config(SimpleNamespace(), timeout_s=0.01)  # no lifespan: no wait
        await wait_for_saved_knowledge_config(
            SimpleNamespace(knowledge_startup_config_applied=asyncio.Event()), timeout_s=0.01
        )  # never applied: warms anyway after the timeout
        return waiting

    assert asyncio.run(scenario())
