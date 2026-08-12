"""ChatOpenAI Claude/Bedrock must skip native json_schema (output_config)."""

from types import SimpleNamespace

import pytest

from cuga.backend.cuga_graph.nodes.shared.base_agent import (
    _chat_openai_skips_native_json_schema,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    "model_name,expected",
    [
        ("aws/claude-opus-4-8", True),
        ("aws/claude-opus-4-6", True),
        ("Claude-3-5-Sonnet", True),
        ("GCP-Claude", True),
        ("gpt-4o", False),
        ("openai/gpt-oss-120b", False),
    ],
)
def test_chat_openai_skips_native_json_schema(model_name, expected):
    llm = SimpleNamespace(model_name=model_name)
    assert _chat_openai_skips_native_json_schema(llm) is expected
