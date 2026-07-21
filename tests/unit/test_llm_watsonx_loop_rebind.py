"""Tests for watsonx async-client rebind across event loops (#523)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from cuga.backend.llm.models import LLMManager, _CUGA_ASYNC_LOOP_REF, set_current_llm_override


@pytest.fixture(autouse=True)
def reset_llm_state():
    mgr = LLMManager()
    mgr._models.clear()
    mgr._pre_instantiated_model = None
    set_current_llm_override(None)
    yield
    mgr._models.clear()
    mgr._pre_instantiated_model = None
    set_current_llm_override(None)


class _DummyWatsonx:
    def __init__(self, async_client: object):
        self.watsonx_client = SimpleNamespace(_async_httpx_client=async_client)
        self.watsonx_model = None


@pytest.mark.unit
def test_rebind_noop_without_running_loop():
    mgr = LLMManager()
    assert mgr.rebind_async_clients_to_running_loop() == 0


@pytest.mark.unit
def test_rebind_tags_then_replaces_async_client_across_loops():
    old_client = object()
    new_client = object()
    model = _DummyWatsonx(old_client)
    mgr = LLMManager()
    mgr._models["k"] = model
    loops_seen: list[asyncio.AbstractEventLoop] = []

    async def first_loop():
        loops_seen.append(asyncio.get_running_loop())
        with (
            patch("cuga.backend.llm.models.ChatWatsonx", _DummyWatsonx),
            patch(
                "ibm_watsonx_ai._wrappers.httpx_wrapper._get_async_httpx_client",
                return_value=new_client,
            ) as factory,
        ):
            assert mgr.rebind_async_clients_to_running_loop() == 0
            ref = getattr(model.watsonx_client, _CUGA_ASYNC_LOOP_REF)
            assert ref() is asyncio.get_running_loop()
            assert model.watsonx_client._async_httpx_client is old_client
            factory.assert_not_called()

    asyncio.run(first_loop())

    async def second_loop():
        loops_seen.append(asyncio.get_running_loop())
        with (
            patch("cuga.backend.llm.models.ChatWatsonx", _DummyWatsonx),
            patch(
                "ibm_watsonx_ai._wrappers.httpx_wrapper._get_async_httpx_client",
                return_value=new_client,
            ) as factory,
        ):
            assert mgr.rebind_async_clients_to_running_loop() == 1
            assert model.watsonx_client._async_httpx_client is new_client
            assert getattr(model.watsonx_client, _CUGA_ASYNC_LOOP_REF)() is asyncio.get_running_loop()
            factory.assert_called_once_with(model.watsonx_client)
            assert mgr.rebind_async_clients_to_running_loop() == 0
            factory.assert_called_once()

    asyncio.run(second_loop())
    assert loops_seen[0] is not loops_seen[1]
    assert "k" in mgr._models


@pytest.mark.unit
def test_rebind_skips_non_watsonx_models():
    mgr = LLMManager()
    mgr._models["plain"] = MagicMock()

    async def _run():
        assert mgr.rebind_async_clients_to_running_loop() == 0

    asyncio.run(_run())


@pytest.mark.unit
def test_clear_models_still_drops_cache():
    mgr = LLMManager()
    mgr._models["k"] = MagicMock()
    mgr._pre_instantiated_model = MagicMock()
    mgr.clear_models()
    assert mgr._models == {}
    assert mgr._pre_instantiated_model is None


@pytest.mark.unit
def test_aclose_watsonx_async_clients_on_owning_loop():
    closed = {"n": 0}

    class _AsyncClient:
        is_closed = False

        async def aclose(self):
            closed["n"] += 1
            self.is_closed = True

    async_client = _AsyncClient()
    model = _DummyWatsonx(async_client)
    mgr = LLMManager()
    mgr._models["k"] = model

    async def _run():
        with patch("cuga.backend.llm.models.ChatWatsonx", _DummyWatsonx):
            assert mgr.rebind_async_clients_to_running_loop() == 0
            assert await mgr.aclose_watsonx_async_clients() == 1
            assert closed["n"] == 1
            assert not hasattr(model.watsonx_client, _CUGA_ASYNC_LOOP_REF)

    asyncio.run(_run())
