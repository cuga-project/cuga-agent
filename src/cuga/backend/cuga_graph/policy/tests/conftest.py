"""Fixtures for policy tests."""

import pytest_asyncio

from cuga.backend.llm.models import LLMManager


@pytest_asyncio.fixture(autouse=True)
async def _rebind_llm_async_clients():
    # ChatWatsonx caches an httpx.AsyncClient bound to the pytest event loop.
    # Rebind that client to the current loop instead of dropping the whole
    # LLMManager cache (which re-auths to IAM on every test). See #523.
    mgr = LLMManager()
    mgr.rebind_async_clients_to_running_loop()
    yield
    # Close on this loop before pytest tears it down so the next test does not leak.
    await mgr.aclose_watsonx_async_clients()
