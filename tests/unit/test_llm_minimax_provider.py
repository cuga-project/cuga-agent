"""Tests for the MiniMax LLM provider branch in LLMManager.

MiniMax is OpenAI-compatible, so it reuses ``ReasoningChatOpenAI`` with a fixed
default base URL and the ``MINIMAX_API_KEY`` env var — mirroring the OpenRouter
branch. These tests lock the model-name/base-url resolution and the client
wiring in ``_create_llm_instance``.
"""

import os
from unittest.mock import patch

import pytest

from cuga.backend.llm.models import LLMManager, set_current_llm_override

BASE_MODEL_SETTINGS = {
    "platform": "minimax",
    "max_tokens": 100,
    "temperature": 0.1,
}

MINIMAX_DEFAULT_BASE_URL = "https://api.minimax.io/v1"


@pytest.fixture(autouse=True)
def reset_llm_state():
    mgr = LLMManager()
    mgr._models.clear()
    mgr._pre_instantiated_model = None
    set_current_llm_override(None)
    os.environ.pop("MINIMAX_BASE_URL", None)
    yield
    mgr._models.clear()
    mgr._pre_instantiated_model = None
    set_current_llm_override(None)
    os.environ.pop("MINIMAX_BASE_URL", None)


class TestMinimaxModelName:
    def test_default_model_name(self):
        mgr = LLMManager()
        assert mgr._get_model_name({}, "minimax") == "MiniMax-M3"

    def test_toml_model_name_wins_over_default(self):
        mgr = LLMManager()
        assert mgr._get_model_name({"model_name": "minimax-M2.5"}, "minimax") == "minimax-M2.5"

    def test_config_model_wins_over_toml(self):
        mgr = LLMManager()
        settings = {"model": "MiniMax-M3", "model_name": "minimax-M2.5"}
        assert mgr._get_model_name(settings, "minimax") == "MiniMax-M3"


class TestMinimaxBaseUrl:
    def test_default_base_url(self):
        mgr = LLMManager()
        assert mgr._get_base_url({}, "minimax") == MINIMAX_DEFAULT_BASE_URL

    def test_toml_url_wins_over_default(self):
        mgr = LLMManager()
        assert mgr._get_base_url({"url": "https://proxy.internal/v1"}, "minimax") == (
            "https://proxy.internal/v1"
        )

    def test_env_base_url_wins(self, monkeypatch):
        monkeypatch.setenv("MINIMAX_BASE_URL", "https://env.minimax/v1")
        mgr = LLMManager()
        assert mgr._get_base_url({}, "minimax") == "https://env.minimax/v1"


class TestMinimaxCreateInstance:
    def test_client_receives_key_base_url_and_timeout(self, monkeypatch):
        monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
        with patch("cuga.backend.llm.models.resolve_secret", return_value=None):
            with patch("cuga.backend.llm.models.ReasoningChatOpenAI") as mock_openai:
                mock_openai.return_value = object()
                mgr = LLMManager()
                mgr._create_llm_instance({**BASE_MODEL_SETTINGS, "timeout": 200})

        kwargs = mock_openai.call_args.kwargs
        assert kwargs["openai_api_key"] == "test-key"
        assert kwargs["openai_api_base"] == MINIMAX_DEFAULT_BASE_URL
        assert kwargs["model_name"] == "MiniMax-M3"
        assert kwargs["timeout"] == 200.0

    def test_missing_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        with patch("cuga.backend.llm.models.resolve_secret", return_value=None):
            mgr = LLMManager()
            with pytest.raises(ValueError, match="MINIMAX_API_KEY"):
                mgr._create_llm_instance({**BASE_MODEL_SETTINGS})
