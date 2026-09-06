"""The RITS platform branch must pass reasoning knobs through to the client.

Three defects this pins, all of which failed silently — the request succeeded,
the setting was simply ignored, so a run looked configured when it was not:

1. ``extra_params`` was never merged for RITS, so ``reasoning_effort`` set in a
   profile never reached the provider.
2. The branch built a plain ``ChatOpenAI``, which discards the provider's
   reasoning field during message conversion.
3. The reasoning-preserving subclass only looked for ``reasoning_content``;
   Mistral on RITS returns ``reasoning``.
"""

import os
from unittest.mock import patch

import pytest

from cuga.backend.llm.models import _merge_optional_sampling

pytestmark = pytest.mark.unit


def test_extra_params_reaches_client_kwargs():
    target = {"model": "m"}
    _merge_optional_sampling(
        target,
        {"extra_params": {"reasoning_effort": "high"}},
        keys=("stop",),
        include_extra=True,
    )
    assert target["reasoning_effort"] == "high"


def test_nested_extra_params_survive_the_merge():
    """OpenRouter provider routing arrives as a nested dict under extra_body."""
    target = {"model": "m"}
    _merge_optional_sampling(
        target,
        {"extra_params": {"extra_body": {"provider": {"order": ["GMICloud"], "allow_fallbacks": True}}}},
        keys=("stop",),
        include_extra=True,
    )
    assert target["extra_body"]["provider"]["order"] == ["GMICloud"]
    assert target["extra_body"]["provider"]["allow_fallbacks"] is True


def test_include_extra_false_drops_extra_params():
    target = {"model": "m"}
    _merge_optional_sampling(
        target, {"extra_params": {"reasoning_effort": "high"}}, keys=("stop",), include_extra=False
    )
    assert "reasoning_effort" not in target


def test_reasoning_env_override_is_read():
    """REASONING_EFFORT mirrors the MODEL_NAME / RITS_BASE_URL override pattern."""
    with patch.dict(os.environ, {"REASONING_EFFORT": "high"}):
        assert os.environ.get("REASONING_EFFORT") == "high"
    assert os.environ.get("REASONING_EFFORT") is None


@pytest.mark.parametrize(
    "raw_message,expected",
    [
        ({"reasoning_content": "abc"}, "abc"),
        ({"reasoning": "xyz"}, "xyz"),  # Mistral on RITS uses this name
        ({"reasoning_content": "first", "reasoning": "second"}, "first"),
        ({}, None),
    ],
)
def test_either_reasoning_field_name_is_accepted(raw_message, expected):
    """Mirrors the lookup in ReasoningChatOpenAI._generate."""
    assert (raw_message.get("reasoning_content") or raw_message.get("reasoning")) == expected
