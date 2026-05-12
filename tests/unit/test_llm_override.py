"""
Tests for LLM override mechanism: verifies that set_current_llm_override()
is picked up by LLMManager.get_model() at call-time, and that a bad API key
produces an authentication/connection error rather than silently using defaults.
"""

import os
from unittest.mock import patch

import pytest

from cuga.backend.llm.models import (
    LLMManager,
    get_current_llm_override,
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
    """Clear singleton cache and override before/after each test."""
    mgr = LLMManager()
    mgr._models.clear()
    mgr._pre_instantiated_model = None
    set_current_llm_override(None)
    yield
    mgr._models.clear()
    mgr._pre_instantiated_model = None
    set_current_llm_override(None)


class TestLLMOverrideMechanism:
    def test_no_override_no_key_raises(self):
        """With no override and no OPENAI_API_KEY env var, constructing the model raises
        an OpenAI config error — confirming the no-override path reaches _create_llm_instance.
        """
        import os
        import openai

        os.environ.pop("OPENAI_API_KEY", None)

        mgr = LLMManager()
        with pytest.raises(openai.OpenAIError, match="api_key"):
            mgr.get_model(BASE_MODEL_SETTINGS)

    def test_override_is_applied(self):
        """Override fields are merged into the resolved model settings."""
        set_current_llm_override(
            {
                "platform": "openai",
                "model": "gpt-3.5-turbo",
                "api_key": "sk-bad-key",
                "url": None,
            }
        )
        mgr = LLMManager()
        model = mgr.get_model(BASE_MODEL_SETTINGS)
        resolved_name = getattr(model, "model_name", None) or getattr(model, "model", None)
        assert resolved_name == "gpt-3.5-turbo"

    def test_override_cleared_after_reset(self):
        """set_current_llm_override(None) removes the override."""
        set_current_llm_override({"platform": "openai", "model": "gpt-3.5-turbo"})
        assert get_current_llm_override() is not None

        set_current_llm_override(None)
        assert get_current_llm_override() is None

    def test_bad_api_key_raises_on_invoke(self):
        """A bad API key should raise an authentication or connection error on invoke."""
        set_current_llm_override(
            {
                "platform": "openai",
                "model": "gpt-4o-mini",
                "api_key": "sk-bad-key-000000000000",
                "url": None,
            }
        )
        mgr = LLMManager()
        model = mgr.get_model(BASE_MODEL_SETTINGS)

        with pytest.raises(Exception) as exc_info:
            model.invoke([{"role": "user", "content": "ping"}])

        error_msg = str(exc_info.value).lower()
        assert any(
            keyword in error_msg
            for keyword in ("auth", "api key", "invalid", "401", "403", "incorrect", "connection")
        ), f"Expected auth/connection error, got: {exc_info.value}"

    @pytest.mark.asyncio
    async def test_bad_api_key_raises_on_ainvoke(self):
        """Async invoke with bad API key raises an authentication or connection error."""
        set_current_llm_override(
            {
                "platform": "openai",
                "model": "gpt-4o-mini",
                "api_key": "sk-bad-key-000000000000",
                "url": None,
            }
        )
        mgr = LLMManager()
        model = mgr.get_model(BASE_MODEL_SETTINGS)

        with pytest.raises(Exception) as exc_info:
            await model.ainvoke([{"role": "user", "content": "ping"}])

        error_msg = str(exc_info.value).lower()
        assert any(
            keyword in error_msg
            for keyword in ("auth", "api key", "invalid", "401", "403", "incorrect", "connection")
        ), f"Expected auth/connection error, got: {exc_info.value}"

    def test_cache_cleared_on_new_override(self):
        """Model cache is empty after clearing it, forcing re-creation on next call."""
        mgr = LLMManager()
        set_current_llm_override({"platform": "openai", "model": "gpt-3.5-turbo", "api_key": "sk-bad-key"})
        mgr.get_model(BASE_MODEL_SETTINGS)
        assert len(mgr._models) == 1

        mgr._models.clear()
        set_current_llm_override({"platform": "openai", "model": "gpt-4o-mini", "api_key": "sk-bad-key-2"})
        mgr.get_model(BASE_MODEL_SETTINGS)
        assert len(mgr._models) == 1

    def test_groq_bad_api_key_raises_on_invoke(self):
        """Groq with a bad API key should raise an auth/connection error on invoke."""
        set_current_llm_override(
            {
                "platform": "groq",
                "model": "llama3-8b-8192",
                "api_key": "gsk_bad_key_000000000000",
                "url": None,
            }
        )
        mgr = LLMManager()
        settings_for_groq = {**BASE_MODEL_SETTINGS, "platform": "groq", "model": "llama3-8b-8192"}
        model = mgr.get_model(settings_for_groq)

        with pytest.raises(Exception) as exc_info:
            model.invoke([{"role": "user", "content": "ping"}])

        error_msg = str(exc_info.value).lower()
        assert any(
            keyword in error_msg
            for keyword in ("auth", "api key", "invalid", "401", "403", "incorrect", "connection", "groq")
        ), f"Expected auth/connection error, got: {exc_info.value}"

    def test_groq_provider_with_openai_prefixed_model_creates_chatgroq(self):
        """When provider=groq and model='openai/gpt-oss-120b' (Groq's OpenAI-compat model),
        LLMManager must instantiate ChatGroq — NOT ChatOpenAI — even though the model
        name starts with 'openai/'.

        Regression test for: Groq API key sent to api.openai.com because ChatOpenAI
        was created instead of ChatGroq when the model name has an 'openai/' prefix.
        """
        from langchain_groq import ChatGroq

        set_current_llm_override(
            {
                "platform": "groq",
                "model": "openai/gpt-oss-120b",
                "api_key": "gsk_bad_key_000000000000",
                "url": "",
            }
        )
        mgr = LLMManager()
        # Base settings come from TOML (platform=openai); override switches to groq
        model = mgr.get_model(BASE_MODEL_SETTINGS)

        assert isinstance(model, ChatGroq), (
            f"Expected ChatGroq but got {type(model).__name__}. "
            "Groq API key is being sent to OpenAI's endpoint when model name starts with 'openai/'."
        )

    def test_groq_provider_openai_model_bad_key_raises_groq_error(self):
        """model='openai/gpt-oss-120b' + provider=groq + bad key → Groq auth error,
        NOT an OpenAI 'find your API key at platform.openai.com' error.
        """
        set_current_llm_override(
            {
                "platform": "groq",
                "model": "openai/gpt-oss-120b",
                "api_key": "gsk_bad_key_000000000000",
                "url": "",
            }
        )
        mgr = LLMManager()
        model = mgr.get_model(BASE_MODEL_SETTINGS)

        with pytest.raises(Exception) as exc_info:
            model.invoke([{"role": "user", "content": "ping"}])

        error_msg = str(exc_info.value)
        assert "platform.openai.com" not in error_msg, (
            "Error references OpenAI's website — ChatOpenAI was used instead of ChatGroq. "
            f"Full error: {error_msg}"
        )
        error_lower = error_msg.lower()
        assert any(
            kw in error_lower
            for kw in ("auth", "api key", "invalid", "401", "403", "incorrect", "connection")
        ), f"Expected an auth/connection error, got: {error_msg}"

    def test_watsonx_uses_default_endpoint_when_url_unset(self):
        """When WATSONX_URL is absent, ChatWatsonx should rely on its default endpoint."""
        env = {
            "WATSONX_API_KEY": "wx-api-key",
            "WATSONX_PROJECT_ID": "project-123",
        }

        with patch.dict(os.environ, env, clear=True):
            with patch("cuga.backend.llm.models.ChatWatsonx") as mock_chat_watsonx:
                mock_chat_watsonx.return_value = object()
                mgr = LLMManager()
                settings_for_watsonx = {
                    **BASE_MODEL_SETTINGS,
                    "platform": "watsonx",
                    "model": "configured-model-name",
                }

                mgr.get_model(settings_for_watsonx)

        kwargs = mock_chat_watsonx.call_args.kwargs
        assert "url" not in kwargs
        assert kwargs["model_id"] == "configured-model-name"
        assert kwargs["project_id"] == "project-123"
        assert kwargs["params"]["temperature"] == BASE_MODEL_SETTINGS["temperature"]
        assert kwargs["params"]["max_completion_tokens"] == BASE_MODEL_SETTINGS["max_tokens"]

    def test_watsonx_ignores_env_url_and_uses_only_model_settings_url(self):
        """WatsonX url should come from model settings, not WATSONX_URL."""
        env = {
            "WATSONX_API_KEY": "wx-api-key",
            "WATSONX_URL": "https://env.watsonx.ai",
            "WATSONX_PROJECT_ID": "project-123",
        }

        with patch.dict(os.environ, env, clear=True):
            with patch("cuga.backend.llm.models.ChatWatsonx") as mock_chat_watsonx:
                mock_chat_watsonx.return_value = object()
                mgr = LLMManager()
                settings_for_watsonx = {
                    **BASE_MODEL_SETTINGS,
                    "platform": "watsonx",
                    "model": "configured-model-name",
                }

                mgr.get_model(settings_for_watsonx)

        kwargs = mock_chat_watsonx.call_args.kwargs
        assert "url" not in kwargs
        assert kwargs["model_id"] == "configured-model-name"
        assert kwargs["project_id"] == "project-123"

    def test_watsonx_uses_model_settings_url_when_set(self):
        """When url is provided in model settings, it should be forwarded to ChatWatsonx."""
        env = {
            "WATSONX_API_KEY": "wx-api-key",
            "WATSONX_URL": "https://env.watsonx.ai",
            "WATSONX_PROJECT_ID": "project-123",
        }

        with patch.dict(os.environ, env, clear=True):
            with patch("cuga.backend.llm.models.ChatWatsonx") as mock_chat_watsonx:
                mock_chat_watsonx.return_value = object()
                mgr = LLMManager()
                settings_for_watsonx = {
                    **BASE_MODEL_SETTINGS,
                    "platform": "watsonx",
                    "model": "configured-model-name",
                    "url": "https://model-settings.watsonx.ai",
                }

                mgr.get_model(settings_for_watsonx)

        kwargs = mock_chat_watsonx.call_args.kwargs
        assert kwargs["url"] == "https://model-settings.watsonx.ai"
        assert kwargs["model_id"] == "configured-model-name"
        assert kwargs["project_id"] == "project-123"

    def test_watsonx_uses_model_settings_deployment_id_over_model_id(self):
        """deployment_id should come from model settings and replace model_id when provided."""
        env = {
            "WATSONX_API_KEY": "wx-api-key",
            "WATSONX_URL": "https://example.watsonx.ai",
            "WATSONX_PROJECT_ID": "project-123",
        }

        with patch.dict(os.environ, env, clear=True):
            with patch("cuga.backend.llm.models.ChatWatsonx") as mock_chat_watsonx:
                mock_chat_watsonx.return_value = object()
                mgr = LLMManager()
                settings_for_watsonx = {
                    **BASE_MODEL_SETTINGS,
                    "platform": "watsonx",
                    "model": "configured-model-name",
                    "deployment_id": "deployment-123",
                }

                mgr.get_model(settings_for_watsonx)

        kwargs = mock_chat_watsonx.call_args.kwargs
        assert "url" not in kwargs
        assert kwargs["deployment_id"] == "deployment-123"
        assert "model_id" not in kwargs
