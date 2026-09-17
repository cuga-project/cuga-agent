"""Tests for RITS ChatOpenAI configuration."""

from unittest.mock import patch

import pytest

from cuga.backend.llm.models import LLMManager


@pytest.mark.unit
def test_rits_chat_openai_uses_canonical_kwargs(monkeypatch):
    monkeypatch.setenv("RITS_API_KEY", "test-rits-key")

    with patch("cuga.backend.llm.models.resolve_secret", return_value=None):
        with patch("langchain_openai.ChatOpenAI") as mock_chat_openai:
            mock_chat_openai.return_value = object()

            LLMManager()._create_llm_instance(
                {
                    "platform": "rits",
                    "model": "openai/gpt-oss-120b-a100",
                    "base_url": "https://rits.example.test",
                    "apikey_name": "RITS_API_KEY",
                    "max_tokens": 100,
                    "temperature": 0.1,
                    "top_p": 0.9,
                }
            )

    kwargs = mock_chat_openai.call_args.kwargs
    assert kwargs["api_key"] == "dummy"
    assert kwargs["base_url"] == "https://rits.example.test"
    assert kwargs["model"] == "openai/gpt-oss-120b-a100"
    assert kwargs["default_headers"] == {"RITS_API_KEY": "test-rits-key"}
    assert kwargs["temperature"] == 0.1
    assert kwargs["top_p"] == 0.9

    assert "openai_api_key" not in kwargs
    assert "openai_api_base" not in kwargs
    assert "model_name" not in kwargs
