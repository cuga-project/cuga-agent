"""Server-mode warm-up: embed the catalogue at boot and when tools change.

Lazy loading is right for the SDK. In server mode it means the first
``find_tools`` after boot silently falls back to the LLM, so the server warms
the cache at startup and after a registry reload.
"""

from unittest.mock import AsyncMock, patch

import numpy as np
import pytest
from langchain_core.tools import StructuredTool

from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister import (
    clear_instance_cache,
    warm_tool_vectors,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister import embedding as embedding_module

pytestmark = pytest.mark.unit

MODEL = "warm-test-model"


class FakeBackend:
    def __init__(self):
        self.calls = []

    async def aembed(self, texts, *, as_query):
        self.calls.append(list(texts))
        return np.ones((len(list(texts)), 4), dtype=np.float32)


@pytest.fixture(autouse=True)
def _clean():
    clear_instance_cache()
    embedding_module.reset_caches()
    yield
    embedding_module.reset_caches()
    clear_instance_cache()


def _tool(name: str) -> StructuredTool:
    def fn(**kwargs):
        return name

    fn.__name__ = name
    return StructuredTool.from_function(func=fn, name=name, description=name)


def _settings(**kw):
    from types import SimpleNamespace

    kw.setdefault("embedding_model", MODEL)
    kw.setdefault("embedding_provider", "local")
    return SimpleNamespace(shortlister=SimpleNamespace(**kw))


def _install_backend():
    backend = FakeBackend()
    embedding_module._MODELS[embedding_module.backend_key("local", MODEL)] = backend
    return backend


@pytest.mark.asyncio
async def test_warm_is_a_noop_on_the_default_llm_strategy():
    """The common deployment must pay nothing for a feature it has not enabled."""
    with patch("cuga.config.settings", _settings(strategy="llm")):
        embedded = await warm_tool_vectors([_tool("a"), _tool("b")])
    assert embedded == 0
    assert not embedding_module._VECTORS


@pytest.mark.asyncio
async def test_warm_embeds_the_catalogue_for_cosine():
    backend = _install_backend()
    with patch("cuga.config.settings", _settings(strategy="embedding")):
        embedded = await warm_tool_vectors([_tool("a"), _tool("b"), _tool("c")])
    assert embedded == 3
    assert backend.calls, "documents were never embedded"


@pytest.mark.asyncio
async def test_rewarm_only_embeds_what_changed():
    """A tools update must cost the new tools, not the whole catalogue."""
    _install_backend()
    tools = [_tool("a"), _tool("b")]
    with patch("cuga.config.settings", _settings(strategy="embedding")):
        first = await warm_tool_vectors(tools)
        added = await warm_tool_vectors(tools + [_tool("c")])
    assert first == 2
    assert added == 1, "re-warm re-embedded unchanged tools"


@pytest.mark.asyncio
async def test_warm_never_raises_when_the_model_is_unavailable():
    """Neither boot nor a tools update may fail on an embedding problem."""
    with (
        patch("cuga.config.settings", _settings(strategy="embedding")),
        patch.object(embedding_module, "_build_backend", side_effect=OSError("offline")),
    ):
        assert await warm_tool_vectors([_tool("a")]) == 0


@pytest.mark.asyncio
async def test_warm_covers_hybrid_too():
    backend = _install_backend()
    with patch("cuga.config.settings", _settings(strategy="hybrid")):
        embedded = await warm_tool_vectors([_tool("a")])
    assert embedded == 1
    assert backend.calls


@pytest.mark.asyncio
async def test_server_helper_warms_from_the_registry():
    """The lifespan/reload helper pulls the catalogue and hands it to the warmer."""
    from cuga.backend.server.main import warm_shortlister_catalogue

    tools = [_tool("a"), _tool("b")]
    provider = AsyncMock()
    provider.get_all_tools = AsyncMock(return_value=tools)

    with (
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.providers.registry.ToolRegistryProvider",
            return_value=provider,
        ),
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.warm_tool_vectors",
            AsyncMock(return_value=len(tools)),
        ) as warmer,
    ):
        embedded = await warm_shortlister_catalogue()

    assert embedded == 2
    warmer.assert_awaited_once()


@pytest.mark.asyncio
async def test_server_helper_never_breaks_startup():
    from cuga.backend.server.main import warm_shortlister_catalogue

    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.providers.registry.ToolRegistryProvider",
        side_effect=RuntimeError("registry down"),
    ):
        assert await warm_shortlister_catalogue() == 0
