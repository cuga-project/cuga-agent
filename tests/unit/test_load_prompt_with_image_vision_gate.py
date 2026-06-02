"""Regression tests for issue #214: hybrid web agent fails with 400
``messages[1].content must be a string`` when the resolved LLM endpoint
rejects multimodal content (e.g. WatsonX via openai-compatible).

The browser planner's prompt is built via ``load_prompt_with_image``. With
``advanced_features.use_vision = true`` (the default), the human message
becomes a list-typed multimodal block ``[image_url, text]`` regardless of
whether the underlying model accepts multimodal content. These tests
exercise the per-model ``enable_vision`` gate that controls that.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from cuga.backend.llm.utils.helpers import load_prompt_with_image, model_supports_vision
from cuga.config import settings


PROMPT_DIR = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "src",
        "cuga",
        "backend",
        "cuga_graph",
        "nodes",
        "browser",
        "browser_planner_agent",
        "prompts",
    )
)


FORMAT_VARS = dict(
    use_vision=True,
    elements_as_string="aria",
    previous_steps=[],
    stm_steps_history=["step1"],
    sub_task="subtask",
    task_analyzer_output={},
    url="http://x",
    img="data:image/png;base64,abc",
    variables_history="",
)


@pytest.fixture
def vision_setting_on():
    prev = settings.advanced_features.use_vision
    settings.advanced_features.use_vision = True
    try:
        yield
    finally:
        settings.advanced_features.use_vision = prev


def _format(prompt_template):
    return prompt_template.format_messages(**FORMAT_VARS)


def test_model_supports_vision_defaults_true_when_no_config():
    assert model_supports_vision(None) is True


def test_model_supports_vision_defaults_true_when_field_missing():
    assert model_supports_vision(SimpleNamespace()) is True


def test_model_supports_vision_returns_false_when_disabled():
    assert model_supports_vision(SimpleNamespace(enable_vision=False)) is False


def test_model_supports_vision_returns_true_when_enabled():
    assert model_supports_vision(SimpleNamespace(enable_vision=True)) is True


def test_human_message_is_string_when_model_disables_vision(vision_setting_on):
    """Reproduces issue #214: when the model is marked as not supporting
    multimodal content, the human message must be a plain string even if
    the global ``use_vision`` setting is enabled."""
    model_config = SimpleNamespace(enable_vision=False, enable_format=False)
    pt = load_prompt_with_image(
        os.path.join(PROMPT_DIR, "system.jinja2"),
        os.path.join(PROMPT_DIR, "user.jinja2"),
        model_config=model_config,
        relative_to_caller=False,
    )

    messages = _format(pt)

    assert len(messages) == 2
    assert isinstance(messages[0].content, str)
    assert isinstance(messages[1].content, str), (
        "messages[1].content must be a string when the model does not support vision (issue #214)"
    )


def test_human_message_is_multimodal_when_model_supports_vision(vision_setting_on):
    model_config = SimpleNamespace(enable_vision=True, enable_format=False)
    pt = load_prompt_with_image(
        os.path.join(PROMPT_DIR, "system.jinja2"),
        os.path.join(PROMPT_DIR, "user.jinja2"),
        model_config=model_config,
        relative_to_caller=False,
    )

    messages = _format(pt)

    assert len(messages) == 2
    assert isinstance(messages[1].content, list)
    types = [c.get("type") for c in messages[1].content]
    assert "image_url" in types and "text" in types


def test_human_message_is_string_when_global_vision_disabled(monkeypatch):
    monkeypatch.setattr(settings.advanced_features, "use_vision", False)
    model_config = SimpleNamespace(enable_vision=True, enable_format=False)
    pt = load_prompt_with_image(
        os.path.join(PROMPT_DIR, "system.jinja2"),
        os.path.join(PROMPT_DIR, "user.jinja2"),
        model_config=model_config,
        relative_to_caller=False,
    )

    messages = _format(pt)

    assert isinstance(messages[1].content, str)
