import pytest

from cuga.supervisor_utils.supervisor_config import _get_model_from_config


@pytest.mark.unit
def test_yaml_provider_key_builds_a_model(monkeypatch):
    # A dummy key is enough: building the model object makes no network call.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    model = _get_model_from_config({"provider": "openai", "model_name": "gpt-4o-mini"})
    assert model is not None
