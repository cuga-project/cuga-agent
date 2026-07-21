"""Ollama / gpt-oss:20b support: colon model-name normalization + parse-error retry predicate."""

from cuga.backend.cuga_graph.nodes.cuga_lite.model_runtime_profile import (
    _normalized_model_key,
    runtime_defaults_for_model,
)
from cuga.backend.llm.errors import (
    is_ollama_tool_call_parse_error,
    is_tool_choice_none_tool_use_failed,
)

OLLAMA_PARSE_ERROR = (
    "Error code: 500 - {'error': {'message': \"error parsing tool call: "
    "raw='import json, re\\n', err=invalid character 'i' looking for beginning of value\"}}"
)


def test_colon_name_normalizes_like_slash_prefix():
    assert _normalized_model_key("gpt-oss:20b") == "gpt-oss-20b"
    assert _normalized_model_key("openai/gpt-oss-20b") == "gpt-oss-20b"


def test_runtime_profile_activates_for_ollama_colon_name():
    assert runtime_defaults_for_model("gpt-oss:20b") == runtime_defaults_for_model("gpt-oss-20b")
    assert runtime_defaults_for_model("gpt-oss:20b")  # non-empty


def test_ollama_parse_error_is_detected():
    assert is_ollama_tool_call_parse_error(OLLAMA_PARSE_ERROR)


def test_ollama_parse_error_predicate_is_specific():
    assert not is_ollama_tool_call_parse_error("some unrelated 500")
    # Must not collide with the existing tool_choice-none predicate.
    assert not is_tool_choice_none_tool_use_failed(OLLAMA_PARSE_ERROR)
