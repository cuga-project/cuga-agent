"""Fixtures for policy tests."""

import pytest

from cuga.backend.llm.models import LLMManager


@pytest.fixture(autouse=True)
def _clear_llm_manager_cache():
    # ChatWatsonx binds async httpx to the current event loop; cached models break
    # the next pytest loop with "Event loop is closed".
    mgr = LLMManager()
    mgr._models.clear()
    yield
    mgr._models.clear()
