"""Tests for the Requesty LLM provider branch in LLMManager.

Requesty is OpenAI-compatible, so it reuses ``ReasoningChatOpenAI`` with a fixed
default base URL and the ``REQUESTY_API_KEY`` env var, mirroring the OpenRouter
branch. These tests lock the model-name/base-url resolution and the client
wiring in ``_create_llm_instance``.
"""

from unittest.mock import patch

import pytest

from cuga.backend.llm.models import LLMManager, set_current_llm_override
from cuga.backend.secrets.seed import _PLATFORM_API_KEY_SLUG, _STATIC_ENV_SEED_MAP

pytestmark = pytest.mark.unit

BASE_MODEL_SETTINGS = {
    "platform": "requesty",
    "max_tokens": 100,
    "temperature": 0.1,
}

REQUESTY_DEFAULT_BASE_URL = "https://router.requesty.ai/v1"


@pytest.fixture(autouse=True)
def reset_llm_state(monkeypatch):
    mgr = LLMManager()
    mgr._models.clear()
    mgr._pre_instantiated_model = None
    set_current_llm_override(None)
    for key in ("REQUESTY_BASE_URL", "MODEL_NAME"):
        monkeypatch.delenv(key, raising=False)
    yield
    mgr._models.clear()
    mgr._pre_instantiated_model = None
    set_current_llm_override(None)


class TestRequestyModelName:
    def test_default_model_name(self):
        mgr = LLMManager()
        assert mgr._get_model_name({}, "requesty") == "openai/gpt-4o-mini"

    def test_toml_model_name_wins_over_default(self):
        mgr = LLMManager()
        assert mgr._get_model_name({"model_name": "openai/gpt-4.1"}, "requesty") == "openai/gpt-4.1"

    def test_env_model_name_wins(self, monkeypatch):
        monkeypatch.setenv("MODEL_NAME", "anthropic/claude-sonnet-4-5")
        mgr = LLMManager()
        assert mgr._get_model_name({"model_name": "openai/gpt-4.1"}, "requesty") == (
            "anthropic/claude-sonnet-4-5"
        )


class TestRequestyBaseUrl:
    def test_default_base_url(self):
        mgr = LLMManager()
        assert mgr._get_base_url({}, "requesty") == REQUESTY_DEFAULT_BASE_URL

    def test_toml_url_wins_over_default(self):
        mgr = LLMManager()
        assert mgr._get_base_url({"url": "https://proxy.internal/v1"}, "requesty") == (
            "https://proxy.internal/v1"
        )

    def test_env_base_url_wins(self, monkeypatch):
        monkeypatch.setenv("REQUESTY_BASE_URL", "https://router.eu.requesty.ai/v1")
        mgr = LLMManager()
        assert mgr._get_base_url({}, "requesty") == "https://router.eu.requesty.ai/v1"


class TestRequestyCreateInstance:
    def test_client_receives_key_base_url_and_timeout(self, monkeypatch):
        monkeypatch.setenv("REQUESTY_API_KEY", "test-key")
        with patch("cuga.backend.llm.models.resolve_secret", return_value=None):
            with patch("cuga.backend.llm.models._get_reasoning_chat_openai") as mock_factory:
                mock_openai = mock_factory.return_value
                mock_openai.return_value = object()
                mgr = LLMManager()
                mgr._create_llm_instance({**BASE_MODEL_SETTINGS, "timeout": 200})

        kwargs = mock_openai.call_args.kwargs
        assert kwargs["openai_api_key"] == "test-key"
        assert kwargs["openai_api_base"] == REQUESTY_DEFAULT_BASE_URL
        assert kwargs["model_name"] == "openai/gpt-4o-mini"
        assert kwargs["timeout"] == 200.0

    @pytest.mark.parametrize("ref_field", ["api_key", "apikey_name"])
    def test_configured_key_reference_wins_over_env(self, monkeypatch, ref_field):
        monkeypatch.setenv("REQUESTY_API_KEY", "env-key")
        resolved = {"vault://requesty/team-key": "team-key"}
        with patch("cuga.backend.llm.models.resolve_secret", side_effect=resolved.get):
            with patch("cuga.backend.llm.models._get_reasoning_chat_openai") as mock_factory:
                mock_openai = mock_factory.return_value
                mock_openai.return_value = object()
                mgr = LLMManager()
                mgr._create_llm_instance({**BASE_MODEL_SETTINGS, ref_field: "vault://requesty/team-key"})

        assert mock_openai.call_args.kwargs["openai_api_key"] == "team-key"

    def test_unresolvable_reference_falls_back_to_env(self, monkeypatch):
        monkeypatch.setenv("REQUESTY_API_KEY", "env-key")
        with patch("cuga.backend.llm.models.resolve_secret", return_value=None):
            with patch("cuga.backend.llm.models._get_reasoning_chat_openai") as mock_factory:
                mock_openai = mock_factory.return_value
                mock_openai.return_value = object()
                mgr = LLMManager()
                mgr._create_llm_instance({**BASE_MODEL_SETTINGS, "apikey_name": "MISSING_KEY"})

        assert mock_openai.call_args.kwargs["openai_api_key"] == "env-key"

    def test_missing_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("REQUESTY_API_KEY", raising=False)
        with patch("cuga.backend.llm.models.resolve_secret", return_value=None):
            mgr = LLMManager()
            with pytest.raises(ValueError, match="REQUESTY_API_KEY"):
                mgr._create_llm_instance({**BASE_MODEL_SETTINGS})


class TestRequestySecretSeed:
    def test_env_var_and_platform_map_to_same_slug(self):
        assert _STATIC_ENV_SEED_MAP["REQUESTY_API_KEY"] == "requesty-api-key"
        assert _PLATFORM_API_KEY_SLUG["requesty"] == "requesty-api-key"
