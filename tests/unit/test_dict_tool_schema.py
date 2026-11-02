"""LangChain tools may expose JSON Schema dictionaries instead of Pydantic models."""

import pytest
from langchain_core.tools import StructuredTool
from types import SimpleNamespace

from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.arg_warning import make_arg_warning_callable
from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.prepare_node import _tool_param_names
from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import PromptUtils


@pytest.mark.unit
@pytest.mark.anyio
async def test_dam_reply_dict_schema_is_described_and_callable() -> None:
    received = {}

    async def reply(threadTs: str, text: str) -> str:
        """Reply to a Slack thread."""
        received.update(threadTs=threadTs, text=text)
        return "posted"

    schema = {
        "type": "object",
        "properties": {
            "threadTs": {"type": "string", "description": "The Slack thread timestamp"},
            "text": {"type": "string", "description": "The reply text"},
        },
        "required": ["threadTs", "text"],
    }
    tool = StructuredTool.from_function(
        coroutine=reply,
        name="mcp__service__reply",
        description="Reply in Slack",
        args_schema=schema,
    )

    assert PromptUtils.get_tool_params_str(tool) == "threadTs: str, text: str"
    params_doc, _ = PromptUtils.get_tool_docs(tool)
    assert "`threadTs`: str (required)" in params_doc
    assert "`text`: str (required)" in params_doc
    assert PromptUtils._build_shortlister_payload([tool], [])[0][tool.name]["args_schema"] == schema
    assert _tool_param_names(tool) == ["threadTs", "text"]

    wrapped = make_arg_warning_callable(tool.coroutine, tool.args_schema, enable=True)
    assert await wrapped(threadTs="123.456", text="Hello") == "posted"
    assert received == {"threadTs": "123.456", "text": "Hello"}


@pytest.mark.unit
@pytest.mark.anyio
async def test_nullable_dict_schema_formats_and_checks_non_null_type() -> None:
    schema = {
        "type": "object",
        "properties": {
            "labels": {"type": ["array", "null"], "items": {"type": "string"}},
            "attempts": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
        },
    }
    tool = SimpleNamespace(name="mcp_example", args_schema=schema)
    assert PromptUtils.get_tool_params_str(tool) == "labels: list = None, attempts: int = None"

    async def accept(labels=None, attempts=None):
        return labels, attempts

    wrapped = make_arg_warning_callable(accept, schema, enable=True)
    assert await wrapped(labels=["one"], attempts=2) == (["one"], 2)
