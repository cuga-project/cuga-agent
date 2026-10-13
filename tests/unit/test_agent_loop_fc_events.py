"""Function-calling steps stream to the UI like code steps.

``get_event_message`` maps subgraph nodes to StreamEvents. A native tool-call turn
is shown as the code a block would have shown, and the sandbox step that answers it
appends ToolMessages instead of an "Execution output:" message — without the
tool-result branch the UI showed nothing between the question and the answer.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from cuga.backend.cuga_graph.utils.agent_loop import AgentLoop

pytestmark = pytest.mark.unit

_CALL = {"name": "echo", "args": {"value": 7}, "id": "c1", "type": "tool_call"}


_BLOCK = 'try:\n    tool_result_c1 = _fc_keep(await echo(**{"value": 7}))\n'


def _event(node, messages, script=None):
    return (
        ("CugaLiteSubgraph",),
        {node: {"chat_messages": messages, "script": script, "variables_storage": {}}},
    )


def test_native_tool_calls_stream_as_a_code_step_never_the_internal_block():
    """A function-calling turn carries the translated block in ``script``; the UI shows the calls."""
    messages = [HumanMessage(content="q"), AIMessage(content="", tool_calls=[_CALL])]
    ev = AgentLoop.get_event_message(MagicMock(), _event("call_model", messages, script=_BLOCK))
    assert ev.name == "CodeAgent"
    data = json.loads(ev.data)
    assert (
        data["code"] == 'echo({"value": 7})' and data["summary"] == "Tool calls issued, preparing to execute"
    )
    assert "_fc_keep" not in ev.data


def test_a_codeact_script_still_streams_as_generated_code():
    messages = [HumanMessage(content="q"), AIMessage(content="```python\nprint(1)\n```")]
    ev = AgentLoop.get_event_message(MagicMock(), _event("call_model", messages, script="print(1)"))
    data = json.loads(ev.data)
    assert data["code"] == "print(1)" and data["summary"] == "Code generated, preparing to execute"


def test_tool_results_from_the_sandbox_stream_as_execution_output():
    messages = [
        HumanMessage(content="q"),
        AIMessage(content="", tool_calls=[_CALL, {**_CALL, "id": "c2", "args": {"value": 8}}]),
        ToolMessage(content="7", tool_call_id="c1", name="echo"),
        ToolMessage(content="8", tool_call_id="c2", name="echo"),
    ]
    ev = AgentLoop.get_event_message(MagicMock(), _event("sandbox", messages))
    assert ev.name == "CodeAgent"
    data = json.loads(ev.data)
    assert data["execution_output"] == "echo: 7\necho: 8" and data["summary"] == "Tool calls completed"


def test_codeact_sandbox_output_is_unchanged():
    messages = [
        HumanMessage(content="q"),
        AIMessage(content="```python\nprint(1)\n```"),
        HumanMessage(content="Execution output:\n1"),
    ]
    ev = AgentLoop.get_event_message(MagicMock(), _event("sandbox", messages))
    assert ev.name == "CodeAgent"
    data = json.loads(ev.data)
    assert data["execution_output"] == "1" and data["summary"] == "Code execution completed"


def test_sandbox_step_without_results_is_an_empty_event():
    ev = AgentLoop.get_event_message(MagicMock(), _event("sandbox", [HumanMessage(content="q")]))
    assert ev.name == "" and ev.data == ""
