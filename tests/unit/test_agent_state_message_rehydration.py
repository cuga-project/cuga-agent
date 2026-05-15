"""Contract tests for ``ChatHistoryMessage`` — the message type used in
CUGA's persisted chat-history fields (``AgentState.chat_messages`` and
friends, ``CugaLiteState.chat_messages``).

Verifies that the LangGraph checkpoint cycle (Pydantic dump → dict →
rehydrate) preserves ``AIMessage.tool_calls`` and ``ToolMessage.tool_call_id``,
that dict inputs route to the right subclass via the ``type`` discriminator,
and that streaming ``*Chunk`` variants are rejected at construction.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
    HumanMessageChunk,
    ToolMessage,
    ToolMessageChunk,
)

from cuga.backend.cuga_graph.state.agent_state import AgentState


def _make_tool_call_messages():
    """A realistic Human → AI(tool_calls) → Tool sequence, the shape that
    requires subclass fields to survive the checkpoint round-trip."""
    return [
        HumanMessage(content="/kwargs-check"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "abc123",
                    "name": "load_skill",
                    "args": {"name": "kwargs-check"},
                    "type": "tool_call",
                }
            ],
            additional_kwargs={"invoked_via": "slash"},
        ),
        ToolMessage(content="WRAPPED BODY", tool_call_id="abc123"),
    ]


def test_model_dump_preserves_ai_tool_calls():
    state = AgentState(input="x", url="", chat_messages=_make_tool_call_messages())
    dumped = state.model_dump()["chat_messages"]
    assert dumped[1]["tool_calls"] == [
        {"id": "abc123", "name": "load_skill", "args": {"name": "kwargs-check"}, "type": "tool_call"}
    ]


def test_model_dump_preserves_tool_message_id():
    state = AgentState(input="x", url="", chat_messages=_make_tool_call_messages())
    dumped = state.model_dump()["chat_messages"]
    assert dumped[2]["tool_call_id"] == "abc123"


def test_model_dump_emits_discriminator_type_literals():
    """Each variant must dump with its non-chunk ``type`` literal so the
    discriminator can pick the right class on the next read."""
    state = AgentState(input="x", url="", chat_messages=_make_tool_call_messages())
    dumped = state.model_dump()["chat_messages"]
    assert [m["type"] for m in dumped] == ["human", "ai", "tool"]


def test_round_trip_restores_subclasses_and_fields():
    state = AgentState(input="x", url="", chat_messages=_make_tool_call_messages())
    restored = AgentState(**state.model_dump())
    msgs = restored.chat_messages or []
    assert [type(m) for m in msgs] == [HumanMessage, AIMessage, ToolMessage]
    assert msgs[1].tool_calls == [
        {"id": "abc123", "name": "load_skill", "args": {"name": "kwargs-check"}, "type": "tool_call"}
    ]
    assert msgs[2].tool_call_id == "abc123"
    assert msgs[1].additional_kwargs["invoked_via"] == "slash"


def test_validation_accepts_dict_inputs_via_discriminator():
    """LangGraph hands back dicts on checkpoint reads; the discriminator must
    pick each subclass from the dict's ``type`` field."""
    state = AgentState(
        input="x",
        url="",
        chat_messages=[
            {"type": "human", "content": "hi"},
            {
                "type": "ai",
                "content": "",
                "tool_calls": [
                    {"id": "1", "name": "f", "args": {}, "type": "tool_call"}
                ],
            },
            {"type": "tool", "content": "out", "tool_call_id": "1"},
        ],
    )
    msgs = state.chat_messages
    assert [type(m) for m in msgs] == [HumanMessage, AIMessage, ToolMessage]
    assert msgs[1].tool_calls[0]["id"] == "1"
    assert msgs[2].tool_call_id == "1"


@pytest.mark.parametrize(
    "chunk",
    [
        AIMessageChunk(content="x", tool_calls=[]),
        HumanMessageChunk(content="x"),
        ToolMessageChunk(content="x", tool_call_id="t1"),
    ],
)
def test_chunks_rejected_at_construction(chunk):
    """Chunks declare a distinct ``type`` literal that isn't in the union.
    Loud rejection is the contract — see ``ChatHistoryMessage``."""
    with pytest.raises(ValidationError):
        AgentState(input="x", url="", chat_messages=[chunk])


def test_none_and_empty_chat_messages():
    assert AgentState(input="x", url="", chat_messages=None).chat_messages is None
    assert AgentState(input="x", url="", chat_messages=[]).chat_messages == []
