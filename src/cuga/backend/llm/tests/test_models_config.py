from cuga.backend.llm import models


class DummyModel:
    max_tokens = 0
    max_completion_tokens = 0
    temperature = 0.0


def test_create_llm_from_config_respects_max_tokens(monkeypatch):
    captured = {}

    def fake_create_llm_instance(self, model_settings):
        captured["max_tokens"] = model_settings.get("max_tokens")
        return DummyModel()

    monkeypatch.setattr(models, "is_mock_llm_enabled", lambda: False)
    monkeypatch.setattr(models.LLMManager, "_create_llm_instance", fake_create_llm_instance)

    model = models.create_llm_from_config({"max_tokens": 2000, "temperature": 0.2})

    assert captured["max_tokens"] == 2000
    assert model.max_tokens == 2000
    assert model.max_completion_tokens == 2000
    assert model.temperature == 0.2
