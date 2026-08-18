"""EmbeddingShortlister: ranking, selection, query blending, cold start.

Uses a deterministic bag-of-words fake embedder rather than a real model, so
these run offline and assert exact ordering. Real-model behavior (does cosine
actually pick the right tool) is a separate, slower concern — see the CRUD
sibling harness.
"""

from unittest.mock import patch

import numpy as np
import pytest
from langchain_core.tools import StructuredTool

from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister import (
    ShortlistRequest,
    ShortlisterUnavailableError,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister import embedding as embedding_module
from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.embedding import (
    EmbeddingShortlister,
    _is_asymmetric,
    is_ready,
    reset_caches,
)

pytestmark = pytest.mark.unit

VOCAB = ["contact", "account", "email", "delete", "create", "list", "weather", "flight"]
MODEL = "test-model"


class FakeBackend:
    """Bag-of-words backend: one dimension per vocabulary word."""

    def __init__(self):
        self.embed_calls = []
        self.query_calls = []
        self.passage_calls = []

    async def aembed(self, texts, *, as_query):
        items = list(texts)
        self.embed_calls.append(items)
        (self.query_calls if as_query else self.passage_calls).append(items)
        return np.array(
            [np.array([float((t or "").lower().count(w)) for w in VOCAB], dtype=np.float32) for t in items],
            dtype=np.float32,
        )


class FakeTextEmbedding:
    """Stand-in for fastembed's TextEmbedding, to test the local backend."""

    def __init__(self):
        self.embed_calls = []
        self.query_calls = []
        self.passage_calls = []

    def _vectors(self, texts):
        return [np.array([float((t or "").lower().count(w)) for w in VOCAB], dtype=np.float32) for t in texts]

    def embed(self, texts):
        self.embed_calls.append(list(texts))
        return self._vectors(texts)

    def query_embed(self, texts):
        self.query_calls.append(list(texts))
        return self._vectors(texts)

    def passage_embed(self, texts):
        self.passage_calls.append(list(texts))
        return self._vectors(texts)


@pytest.fixture(autouse=True)
def _clean():
    reset_caches()
    yield
    reset_caches()


@pytest.fixture
def fake_model():
    backend = FakeBackend()
    embedding_module._MODELS[embedding_module.backend_key("local", MODEL)] = backend
    return backend


def _tool(name: str, description: str = "") -> StructuredTool:
    def fn(**kwargs):
        return name

    fn.__name__ = name
    return StructuredTool.from_function(func=fn, name=name, description=description)


def _request(query: str, tools, **kwargs) -> ShortlistRequest:
    return ShortlistRequest(query=query, tools=tools, apps=[], **kwargs)


# --- ranking ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_ranks_by_cosine_similarity(fake_model):
    tools = [
        _tool("weather_lookup", "weather forecast"),
        _tool("contact_finder", "find a contact by email"),
        _tool("flight_booker", "book a flight"),
    ]
    strategy = EmbeddingShortlister(MODEL, min_score=0.0)
    result = (await strategy.shortlist(_request("find the contact email", tools))).candidates

    assert result[0].name == "contact_finder"
    assert result[0].score > result[-1].score


@pytest.mark.asyncio
async def test_reasoning_is_populated_for_rendering(fake_model):
    strategy = EmbeddingShortlister(MODEL, min_score=0.0)
    result = (await strategy.shortlist(_request("contact", [_tool("contact_finder", "contact")]))).candidates
    assert "Cosine similarity" in result[0].reasoning


@pytest.mark.asyncio
async def test_never_returns_empty_even_when_nothing_clears_min_score(fake_model):
    """An empty result is a dead end for the agent and makes the bind cap raise."""
    tools = [_tool("weather_lookup", "weather"), _tool("flight_booker", "flight")]
    strategy = EmbeddingShortlister(MODEL, min_score=0.99)
    result = (await strategy.shortlist(_request("contact email", tools))).candidates

    assert result, "must fall back to best-guess rather than returning nothing"
    assert len(result) <= 3


@pytest.mark.asyncio
async def test_min_score_filters_when_some_clear_it(fake_model):
    tools = [_tool("contact_finder", "contact email"), _tool("weather_lookup", "weather")]
    strategy = EmbeddingShortlister(MODEL, min_score=0.5)
    result = (await strategy.shortlist(_request("contact email", tools))).candidates

    assert [c.name for c in result] == ["contact_finder"]


@pytest.mark.asyncio
async def test_top_k_and_max_results_cap_the_result(fake_model):
    tools = [_tool(f"contact_{i}", "contact email") for i in range(10)]
    strategy = EmbeddingShortlister(MODEL, min_score=0.0)

    assert len((await strategy.shortlist(_request("contact", tools, top_k=3))).candidates) == 3
    assert len((await strategy.shortlist(_request("contact", tools, max_results=2))).candidates) == 2
    # The tighter of the two wins.
    capped = await strategy.shortlist(_request("contact", tools, top_k=8, max_results=4))
    assert len(capped.candidates) == 4


@pytest.mark.asyncio
async def test_top_k_zero_returns_nothing(fake_model):
    strategy = EmbeddingShortlister(MODEL)
    result = (await strategy.shortlist(_request("contact", [_tool("contact_finder")], top_k=0))).candidates
    assert result == []


@pytest.mark.asyncio
async def test_empty_tools_returns_empty(fake_model):
    strategy = EmbeddingShortlister(MODEL)
    assert (await strategy.shortlist(_request("contact", []))).candidates == []


# --- query blending ---------------------------------------------------------


@pytest.mark.asyncio
async def test_query_and_task_context_are_embedded_separately(fake_model):
    """Not concatenated — a long context must not swamp a short step query."""
    strategy = EmbeddingShortlister(MODEL, min_score=0.0)
    await strategy.shortlist(
        _request("list contacts", [_tool("contact_finder", "contact")], task_context="book a flight")
    )
    assert fake_model.query_calls[-1] == ["list contacts", "book a flight"]


@pytest.mark.asyncio
async def test_query_weight_shifts_ranking_toward_the_step_query(fake_model):
    """The whole point of vector blending: the weighting is explicit, not a
    side effect of how long each string happens to be."""
    tools = [_tool("contact_finder", "contact"), _tool("flight_booker", "flight")]
    request = dict(task_context="book a flight flight flight flight flight")

    query_heavy = EmbeddingShortlister(MODEL, query_weight=1.0, min_score=0.0)
    context_heavy = EmbeddingShortlister(MODEL, query_weight=0.0, min_score=0.0)

    top_query = (await query_heavy.shortlist(_request("contact", tools, **request))).candidates[0]
    top_context = (await context_heavy.shortlist(_request("contact", tools, **request))).candidates[0]

    assert top_query.name == "contact_finder"
    assert top_context.name == "flight_booker"


@pytest.mark.asyncio
async def test_missing_task_context_uses_query_alone(fake_model):
    strategy = EmbeddingShortlister(MODEL, min_score=0.0)
    await strategy.shortlist(_request("list contacts", [_tool("contact_finder", "contact")]))
    assert fake_model.query_calls[-1] == ["list contacts"]


@pytest.mark.asyncio
async def test_blank_query_returns_nothing_rather_than_claiming_unavailability(fake_model):
    """An empty query is not a broken backend.

    Raising ShortlisterUnavailableError here would wrongly trigger the fallback
    strategy, which would receive the same empty query and fare no better.
    """
    strategy = EmbeddingShortlister(MODEL)
    assert (
        await strategy.shortlist(_request("   ", [_tool("contact_finder")], task_context="  "))
    ).candidates == []


# --- caching ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_vectors_are_cached_across_calls(fake_model):
    tools = [_tool("contact_finder", "contact"), _tool("weather_lookup", "weather")]
    strategy = EmbeddingShortlister(MODEL, min_score=0.0)

    await strategy.shortlist(_request("contact", tools))
    documents_embedded_first = sum(len(c) for c in fake_model.passage_calls)

    await strategy.shortlist(_request("weather", tools))

    # Second run embeds only the query; the two tool documents come from cache.
    assert documents_embedded_first == 2
    assert sum(len(c) for c in fake_model.passage_calls) == 2, "documents were re-embedded"
    assert fake_model.query_calls[-1] == ["weather"]


@pytest.mark.asyncio
async def test_changed_description_reembeds(fake_model):
    strategy = EmbeddingShortlister(MODEL, min_score=0.0)
    await strategy.shortlist(_request("contact", [_tool("t", "contact")]))
    docs_before = sum(len(c) for c in fake_model.passage_calls)
    await strategy.shortlist(_request("contact", [_tool("t", "totally different email text")]))
    assert sum(len(c) for c in fake_model.passage_calls) == docs_before + 1


# --- cold start -------------------------------------------------------------


@pytest.mark.asyncio
async def test_unloaded_model_raises_unavailable_and_starts_background_load():
    """A user query must never block on a model download."""
    strategy = EmbeddingShortlister("not-loaded-model")
    with patch.object(embedding_module, "ensure_loading") as mock_loading:
        with pytest.raises(ShortlisterUnavailableError):
            await strategy.shortlist(_request("contact", [_tool("contact_finder")]))
    mock_loading.assert_called_once_with("local", "not-loaded-model")


def test_is_ready_reflects_residency(fake_model):
    assert is_ready("local", MODEL) is True
    assert is_ready("local", "some-other-model") is False


def test_failed_load_sets_a_retry_cooldown():
    """An airgapped deploy must not hammer the network on every call."""
    with patch.object(embedding_module, "_build_backend", side_effect=OSError("offline")):
        assert embedding_module.prewarm("local", "missing-model") is False
    assert embedding_module.backend_key("local", "missing-model") in embedding_module._RETRY_AFTER

    with patch.object(embedding_module, "_build_backend") as builder:
        embedding_module.ensure_loading("local", "missing-model")
        builder.assert_not_called()


# --- symmetric vs asymmetric ------------------------------------------------


@pytest.mark.parametrize(
    "model_name,expected",
    [
        ("sentence-transformers/all-MiniLM-L6-v2", False),
        ("all-MiniLM-L6-v2", False),
        ("BAAI/bge-small-en-v1.5", True),
        ("BAAI/bge-reranker-base", False),
        ("some-unknown-model", False),
    ],
)
def test_asymmetric_detection(model_name, expected):
    """bge wants a query prefix; MiniLM does not, and applying one degrades it.
    Unknown models default to symmetric."""
    assert _is_asymmetric(model_name) is expected


def _local_backend_with(model_name: str, fake: "FakeTextEmbedding"):
    backend = embedding_module._LocalBackend.__new__(embedding_module._LocalBackend)
    backend._model_name = model_name
    backend._asymmetric = embedding_module._is_asymmetric(model_name)
    backend._model = fake
    return backend


@pytest.mark.asyncio
async def test_bge_backend_uses_query_and_passage_encoders():
    fake = FakeTextEmbedding()
    backend = _local_backend_with("BAAI/bge-small-en-v1.5", fake)

    await backend.aembed(["a document"], as_query=False)
    await backend.aembed(["a query"], as_query=True)

    assert fake.passage_calls == [["a document"]]
    assert fake.query_calls == [["a query"]]
    assert not fake.embed_calls


@pytest.mark.asyncio
async def test_minilm_backend_uses_the_symmetric_encoder():
    """Applying bge's query prefix to MiniLM degrades it, so it must not be used."""
    fake = FakeTextEmbedding()
    backend = _local_backend_with(MODEL, fake)

    await backend.aembed(["a document"], as_query=False)
    await backend.aembed(["a query"], as_query=True)

    assert fake.embed_calls == [["a document"], ["a query"]]
    assert not fake.query_calls and not fake.passage_calls


@pytest.mark.asyncio
async def test_unknown_provider_is_unavailable_not_silently_ignored():
    """A provider typo must surface, not quietly fall back to local."""
    with pytest.raises(ShortlisterUnavailableError, match="unknown shortlister.embedding_provider"):
        embedding_module._build_backend("gpt4all", MODEL)


def test_provider_is_part_of_the_backend_identity():
    """Two providers serving the same model name are different vector spaces."""
    assert embedding_module.backend_key("local", MODEL) != embedding_module.backend_key("openai", MODEL)
