"""Function-calling steps stream to the UI like code steps.

``get_event_message`` maps subgraph nodes to StreamEvents; without these branches a
native tool-call turn and its results were an empty ``tool_exec`` event and the UI
showed nothing between the question and the answer.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from cuga.backend.cuga_graph.utils.agent_loop import AgentLoop

pytestmark = pytest.mark.unit

_CALL = {"name": "echo", "args": {"value": 7}, "id": "c1", "type": "tool_call"}


def _event(node, messages):
    return (
        ("CugaLiteSubgraph",),
        {node: {"chat_messages": messages, "script": None, "variables_storage": {}}},
    )


def test_native_tool_calls_stream_as_a_code_step():
    messages = [HumanMessage(content="q"), AIMessage(content="", tool_calls=[_CALL])]
    ev = AgentLoop.get_event_message(MagicMock(), _event("call_model", messages))
    assert ev.name == "CodeAgent"
    assert json.loads(ev.data)["code"] == 'echo({"value": 7})'


def test_tool_results_stream_as_execution_output():
    messages = [
        HumanMessage(content="q"),
        AIMessage(content="", tool_calls=[_CALL, {**_CALL, "id": "c2", "args": {"value": 8}}]),
        ToolMessage(content="7", tool_call_id="c1", name="echo"),
        ToolMessage(content="8", tool_call_id="c2", name="echo"),
    ]
    ev = AgentLoop.get_event_message(MagicMock(), _event("tool_exec", messages))
    assert ev.name == "CodeAgent"
    assert json.loads(ev.data)["execution_output"] == "echo: 7\necho: 8"


def test_tool_exec_without_results_is_an_empty_event():
    ev = AgentLoop.get_event_message(MagicMock(), _event("tool_exec", [HumanMessage(content="q")]))
    assert ev.name == "" and ev.data == ""
