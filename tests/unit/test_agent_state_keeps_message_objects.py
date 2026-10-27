"""``AgentState.model_dump()`` keeps message subclass fields; validation revives the classes.

Nodes pass state around as ``Command(update=state.model_dump())``. Serialising a
``List[BaseMessage]`` by the declared type dropped ``tool_calls`` and
``tool_call_id`` at every hop, so the next turn revalidated them into bare
``BaseMessage`` shells — the root cause of the flattened function-calling replay.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from cuga.backend.cuga_graph.state.agent_state import AgentState

pytestmark = pytest.mark.unit

_CALL = {"name": "echo", "args": {"value": 7}, "id": "call_1", "type": "tool_call"}


def test_model_dump_keeps_tool_calls_and_tool_call_ids():
    state = AgentState(
        chat_messages=[
            HumanMessage(content="echo 7"),
            AIMessage(content="", tool_calls=[_CALL]),
            ToolMessage(content="7", tool_call_id="call_1", name="echo"),
        ]
    )

    dumped = state.model_dump()["chat_messages"]

    assert dumped[1]["type"] == "ai" and dumped[1]["tool_calls"] == [_CALL], "dumped by runtime type"
    assert dumped[2]["type"] == "tool" and dumped[2]["tool_call_id"] == "call_1"

    # and they revalidate as themselves on the next hop
    again = AgentState(**state.model_dump())
    assert type(again.chat_messages[2]) is ToolMessage and again.chat_messages[2].tool_call_id == "call_1"
    assert type(again.chat_messages[1]) is AIMessage and again.chat_messages[1].tool_calls == [_CALL]


def test_legacy_shells_from_old_checkpoints_still_validate():
    """A tool shell written before the fields were preserved has no tool_call_id: keep it
    as the bare BaseMessage it always was rather than failing the whole state."""
    state = AgentState(
        chat_messages=[{"type": "tool", "content": "7", "additional_kwargs": {}, "response_metadata": {}}]
    )
    assert state.chat_messages[0].type == "tool" and state.chat_messages[0].content == "7"
