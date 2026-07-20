"""Unit tests for Manage-page advanced LLM params (max_tokens, top_p, etc.)."""

from unittest.mock import MagicMock, patch

import pytest

from cuga.backend.llm.models import LLMManager, create_llm_from_config, set_current_llm_override

# Placeholder credential values for unit tests only (not real secrets).
_TEST_CRED = "test-key"  # pragma: allowlist secret
_LEAK_CRED = "should-not-leak"  # pragma: allowlist secret


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


def _openai_cfg(**overrides):
    cfg = {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "api_key": _TEST_CRED,
        "temperature": 0.2,
    }
    cfg.update(overrides)
    return cfg


@pytest.mark.unit
class TestCreateLlmFromConfigMaxTokens:
    def test_uses_llm_cfg_max_tokens_over_toml(self):
        with patch("cuga.backend.llm.models.settings") as mock_settings:
            mock_settings.agent.code.model = {"max_tokens": 16000}
            mock_settings.secrets = None
            with patch("cuga.backend.llm.models.is_mock_llm_enabled", return_value=False):
                with patch.object(LLMManager, "_create_llm_instance") as mock_create:
                    mock_create.return_value = MagicMock()
                    with patch.object(LLMManager, "_update_model_parameters", side_effect=lambda m, **kw: m):
                        create_llm_from_config(_openai_cfg(max_tokens=4096))

        settings_dict = mock_create.call_args.args[0]
        assert settings_dict.get("max_tokens") == 4096

    def test_unset_max_tokens_falls_back_to_toml(self):
        with patch("cuga.backend.llm.models.settings") as mock_settings:
            mock_settings.agent.code.model = {"max_tokens": 8000}
            mock_settings.secrets = None
            with patch("cuga.backend.llm.models.is_mock_llm_enabled", return_value=False):
                with patch.object(LLMManager, "_create_llm_instance") as mock_create:
                    mock_create.return_value = MagicMock()
                    with patch.object(LLMManager, "_update_model_parameters", side_effect=lambda m, **kw: m):
                        create_llm_from_config(_openai_cfg())

        settings_dict = mock_create.call_args.args[0]
        assert settings_dict.get("max_tokens") == 8000

    def test_passes_optional_sampling_params_when_set(self):
        with patch("cuga.backend.llm.models.settings") as mock_settings:
            mock_settings.agent.code.model = {"max_tokens": 16000}
            mock_settings.secrets = None
            with patch("cuga.backend.llm.models.is_mock_llm_enabled", return_value=False):
                with patch.object(LLMManager, "_create_llm_instance") as mock_create:
                    mock_create.return_value = MagicMock()
                    with patch.object(LLMManager, "_update_model_parameters", side_effect=lambda m, **kw: m):
                        create_llm_from_config(
                            _openai_cfg(
                                top_p=0.9,
                                frequency_penalty=0.5,
                                presence_penalty=0.25,
                                stop=["END"],
                                extra_params={"seed": 7},
                            )
                        )

        settings_dict = mock_create.call_args.args[0]
        assert settings_dict.get("top_p") == 0.9
        assert settings_dict.get("frequency_penalty") == 0.5
        assert settings_dict.get("presence_penalty") == 0.25
        assert settings_dict.get("stop") == ["END"]
        assert settings_dict.get("extra_params") == {"seed": 7}

    def test_omits_optional_sampling_params_when_unset(self):
        with patch("cuga.backend.llm.models.settings") as mock_settings:
            mock_settings.agent.code.model = {"max_tokens": 16000}
            mock_settings.secrets = None
            with patch("cuga.backend.llm.models.is_mock_llm_enabled", return_value=False):
                with patch.object(LLMManager, "_create_llm_instance") as mock_create:
                    mock_create.return_value = MagicMock()
                    with patch.object(LLMManager, "_update_model_parameters", side_effect=lambda m, **kw: m):
                        create_llm_from_config(_openai_cfg())

        settings_dict = mock_create.call_args.args[0]
        assert "top_p" not in settings_dict
        assert "frequency_penalty" not in settings_dict
        assert "presence_penalty" not in settings_dict
        assert "stop" not in settings_dict
        assert "extra_params" not in settings_dict


@pytest.mark.unit
class TestCreateLlmInstanceProviderParams:
    def test_openai_receives_explicit_top_p_and_penalties(self):
        with patch("cuga.backend.llm.models.ReasoningChatOpenAI") as mock_openai:
            mock_openai.return_value = object()
            with patch.object(LLMManager, "_get_auth_headers", return_value={}):
                mgr = LLMManager()
                mgr._create_llm_instance(
                    {
                        "platform": "openai",
                        "model": "gpt-4o-mini",
                        "max_tokens": 2048,
                        "temperature": 0.1,
                        "api_key": _TEST_CRED,
                        "top_p": 0.8,
                        "frequency_penalty": 0.1,
                        "presence_penalty": 0.2,
                        "stop": ["STOP"],
                    }
                )

        kwargs = mock_openai.call_args.kwargs
        assert kwargs["max_tokens"] == 2048
        assert kwargs["top_p"] == 0.8
        assert kwargs["frequency_penalty"] == 0.1
        assert kwargs["presence_penalty"] == 0.2
        assert kwargs["stop"] == ["STOP"]

    def test_openai_does_not_send_default_top_p(self):
        with patch("cuga.backend.llm.models.ReasoningChatOpenAI") as mock_openai:
            mock_openai.return_value = object()
            with patch.object(LLMManager, "_get_auth_headers", return_value={}):
                mgr = LLMManager()
                mgr._create_llm_instance(
                    {
                        "platform": "openai",
                        "model": "gpt-4o-mini",
                        "max_tokens": 100,
                        "temperature": 0.1,
                        "api_key": _TEST_CRED,
                    }
                )

        kwargs = mock_openai.call_args.kwargs
        assert "top_p" not in kwargs

    def test_openai_strips_sensitive_extra_params(self):
        with patch("cuga.backend.llm.models.ReasoningChatOpenAI") as mock_openai:
            mock_openai.return_value = object()
            with patch.object(LLMManager, "_get_auth_headers", return_value={}):
                mgr = LLMManager()
                mgr._create_llm_instance(
                    {
                        "platform": "openai",
                        "model": "gpt-4o-mini",
                        "max_tokens": 100,
                        "temperature": 0.1,
                        "api_key": _TEST_CRED,
                        "extra_params": {
                            "seed": 42,
                            "api_key": _LEAK_CRED,
                            "openai_api_key": _LEAK_CRED,
                            "default_headers": {"X-Injected": "nope"},
                        },
                    }
                )

        kwargs = mock_openai.call_args.kwargs
        assert kwargs.get("seed") == 42
        assert kwargs.get("openai_api_key") == _TEST_CRED
        assert kwargs.get("api_key") != _LEAK_CRED
        assert "default_headers" not in kwargs

    def test_groq_receives_max_tokens_and_top_p(self):
        with patch("cuga.backend.llm.models.ChatGroq") as mock_groq:
            mock_groq.return_value = object()
            with patch("cuga.backend.llm.models.resolve_secret", return_value=_TEST_CRED):
                mgr = LLMManager()
                mgr._create_llm_instance(
                    {
                        "platform": "groq",
                        "model": "llama-3.3-70b-versatile",
                        "max_tokens": 1024,
                        "temperature": 0.3,
                        "api_key": _TEST_CRED,
                        "top_p": 0.95,
                    }
                )

        kwargs = mock_groq.call_args.kwargs
        assert kwargs["max_tokens"] == 1024
        assert kwargs["temperature"] == 0.3
        assert kwargs["top_p"] == 0.95

    def test_watsonx_nests_max_tokens_and_top_p_in_params(self, monkeypatch):
        monkeypatch.setenv("WATSONX_PROJECT_ID", "test-project")
        with patch("cuga.backend.llm.models.ChatWatsonx") as mock_wx:
            mock_wx.return_value = MagicMock()
            with patch("cuga.backend.llm.models.ensure_model_context_profile"):
                mgr = LLMManager()
                mgr._create_llm_instance(
                    {
                        "platform": "watsonx",
                        "model": "ibm/granite-3-8b-instruct",
                        "max_tokens": 4096,
                        "temperature": 0.4,
                        "top_p": 0.85,
                        "top_k": 50,
                    }
                )

        kwargs = mock_wx.call_args.kwargs
        params = kwargs["params"]
        assert params["max_completion_tokens"] == 4096
        assert params["temperature"] == 0.4
        assert params["top_p"] == 0.85
        assert params["top_k"] == 50

    def test_litellm_receives_sampling_params(self):
        with patch("cuga.backend.llm.models.ReasoningChatLiteLLM") as mock_lite:
            mock_lite.return_value = object()
            with patch("cuga.backend.llm.models.resolve_secret", return_value=_TEST_CRED):
                mgr = LLMManager()
                mgr._create_llm_instance(
                    {
                        "platform": "litellm",
                        "model": "gpt-4o-mini",
                        "max_tokens": 512,
                        "temperature": 0.1,
                        "api_key": _TEST_CRED,
                        "url": "http://localhost:4000",
                        "top_p": 0.7,
                        "frequency_penalty": 0.1,
                    }
                )

        kwargs = mock_lite.call_args.kwargs
        assert kwargs["max_tokens"] == 512
        assert kwargs["top_p"] == 0.7
        assert kwargs["frequency_penalty"] == 0.1
