"""Tests for configurable LLM HTTP timeout in LLMManager."""

import os
from unittest.mock import patch

import pytest

from cuga.backend.llm.models import (
    LLMManager,
    _DEFAULT_LLM_HTTP_TIMEOUT,
    set_current_llm_override,
)

BASE_MODEL_SETTINGS = {
    "platform": "openai",
    "model": "gpt-4o-mini",
    "max_tokens": 100,
    "temperature": 0.1,
}


@pytest.fixture(autouse=True)
def reset_llm_state():
    mgr = LLMManager()
    mgr._models.clear()
    mgr._pre_instantiated_model = None
    set_current_llm_override(None)
    for key in ("CUGA_LLM_HTTP_TIMEOUT", "LLM_HTTP_TIMEOUT"):
        os.environ.pop(key, None)
    yield
    mgr._models.clear()
    mgr._pre_instantiated_model = None
    set_current_llm_override(None)
    for key in ("CUGA_LLM_HTTP_TIMEOUT", "LLM_HTTP_TIMEOUT"):
        os.environ.pop(key, None)


class TestGetHttpTimeout:
    def test_default_timeout(self):
        mgr = LLMManager()
        assert mgr._get_http_timeout({}) == _DEFAULT_LLM_HTTP_TIMEOUT

    def test_model_settings_timeout(self):
        mgr = LLMManager()
        assert mgr._get_http_timeout({"timeout": 120}) == 120.0

    def test_model_settings_timeout_beats_env(self, monkeypatch):
        monkeypatch.setenv("CUGA_LLM_HTTP_TIMEOUT", "180")
        mgr = LLMManager()
        assert mgr._get_http_timeout({"timeout": 90}) == 90.0

    def test_cuga_env_var(self, monkeypatch):
        monkeypatch.setenv("CUGA_LLM_HTTP_TIMEOUT", "180")
        mgr = LLMManager()
        assert mgr._get_http_timeout({}) == 180.0

    def test_llm_http_timeout_env_var(self, monkeypatch):
        monkeypatch.setenv("LLM_HTTP_TIMEOUT", "240")
        mgr = LLMManager()
        assert mgr._get_http_timeout({}) == 240.0

    def test_cuga_env_beats_llm_http_timeout_env(self, monkeypatch):
        monkeypatch.setenv("CUGA_LLM_HTTP_TIMEOUT", "150")
        monkeypatch.setenv("LLM_HTTP_TIMEOUT", "240")
        mgr = LLMManager()
        assert mgr._get_http_timeout({}) == 150.0

    def test_invalid_timeout_values_fall_back_to_default(self, monkeypatch):
        mgr = LLMManager()
        for invalid in ("not-a-number", 0, -5, "NaN", "inf", "Infinity"):
            assert mgr._get_http_timeout({"timeout": invalid}) == _DEFAULT_LLM_HTTP_TIMEOUT
        for env_val in ("NaN", "inf"):
            monkeypatch.setenv("CUGA_LLM_HTTP_TIMEOUT", env_val)
            assert mgr._get_http_timeout({}) == _DEFAULT_LLM_HTTP_TIMEOUT


class TestHttpTimeoutPassedToClients:
    def test_openai_client_receives_timeout(self):
        with patch("cuga.backend.llm.models.ReasoningChatOpenAI") as mock_openai:
            with patch.object(LLMManager, "_get_auth_headers", return_value={"Authorization": "Bearer x"}):
                mock_openai.return_value = object()
                mgr = LLMManager()
                mgr._create_llm_instance({**BASE_MODEL_SETTINGS, "timeout": 120})

        assert mock_openai.call_args.kwargs["timeout"] == 120.0

    def test_openrouter_client_receives_timeout(self):
        with patch("cuga.backend.llm.models.resolve_secret", return_value="dummy"):
            with patch("cuga.backend.llm.models.ReasoningChatOpenAI") as mock_openai:
                mock_openai.return_value = object()
                mgr = LLMManager()
                mgr._create_llm_instance(
                    {
                        **BASE_MODEL_SETTINGS,
                        "platform": "openrouter",
                        "model": "anthropic/claude-3.5-sonnet",
                        "timeout": 200,
                    }
                )

        assert mock_openai.call_args.kwargs["timeout"] == 200.0

    def test_azure_client_receives_timeout(self):
        with patch("cuga.backend.llm.models.AzureChatOpenAI") as mock_azure:
            mock_azure.return_value = object()
            mgr = LLMManager()
            mgr._create_llm_instance(
                {
                    **BASE_MODEL_SETTINGS,
                    "platform": "azure",
                    "model": "gpt-4o",
                    "api_version": "2024-08-06",
                    "timeout": 300,
                }
            )

        assert mock_azure.call_args.kwargs["timeout"] == 300.0
