import pytest

from cuga.backend.llm.models import LLMManager
from cuga.supervisor_utils.supervisor_config import _get_model_from_config


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
