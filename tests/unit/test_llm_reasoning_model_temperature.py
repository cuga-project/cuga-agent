"""Reasoning models must skip non-default temperature (Azure/LiteLLM gpt-5*)."""

import pytest

from cuga.backend.llm.models import LLMManager


@pytest.mark.unit
@pytest.mark.parametrize(
    "model_name,expected",
    [
        ("azure/gpt-5.6-terra", True),
        ("azure/gpt-5.6-sol", True),
        ("azure/gpt-5.5", True),
        ("gpt-5.6-terra", True),
        ("gpt-5", True),
        ("o3-mini", True),
        ("o4-mini", True),
        ("gpt-4o", False),
        ("azure/gpt-4o", False),
        ("claude-opus-4-6", False),
    ],
)
def test_is_reasoning_model(model_name, expected):
    assert LLMManager()._is_reasoning_model(model_name) is expected
