import pytest

from cuga.backend.llm.models import LLMManager
from cuga.supervisor_utils.supervisor_config import _get_model_from_config, _model_config_from_stored_llm


@pytest.mark.unit
def test_yaml_provider_key_forwards_platform(monkeypatch):
    """The YAML provider key must be forwarded as LLMManager's platform selector."""
    seen = {}

    def fake_get_model(_manager, model_settings):
        seen.update(model_settings)
        return object()

    monkeypatch.setattr(LLMManager, "get_model", fake_get_model)
    model = _get_model_from_config({"provider": "openai", "model_name": "gpt-4o-mini"})

    assert model is not None
    assert seen["provider"] == "openai"
    assert seen["platform"] == "openai"
    assert seen["model_name"] == "gpt-4o-mini"


@pytest.mark.unit
def test_stored_member_endpoint_reaches_model_provider(monkeypatch):
    seen = {}

    def fake_get_model(_manager, model_settings):
        seen.update(model_settings)
        return object()

    monkeypatch.setattr(LLMManager, "get_model", fake_get_model)
    translated = _model_config_from_stored_llm(
        {"provider": "openai", "model": "child-model", "base_url": "https://example.invalid/v1"}
    )

    assert _get_model_from_config(translated) is not None
    assert seen["url"] == "https://example.invalid/v1"
    assert seen["model_name"] == "child-model"


@pytest.mark.unit
def test_yaml_provider_key_builds_real_openai_model(monkeypatch):
    """The real OpenAI construction path should accept provider-only YAML config."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    model = _get_model_from_config({"provider": "openai", "model_name": "gpt-4o-mini"})

    assert model is not None
