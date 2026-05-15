"""Regression guard for the slice #17 tool_calls / ToolMessage round-trip bug.

The setup that broke:

  1. Slice #17's ``synthesize_skill_invocation`` puts an AIMessage with
     ``tool_calls=[load_skill(...)]`` and a paired ToolMessage with
     ``tool_call_id`` into ``AgentState.chat_messages``.
  2. ``AgentState.chat_messages`` was annotated ``Optional[List[BaseMessage]]``.
  3. Pydantic v2 serialized each item by the *declared* type (BaseMessage),
     which doesn't expose ``tool_calls`` or ``tool_call_id`` — those fields
     silently disappeared from ``model_dump()`` output (and therefore from
     LangGraph's checkpoint blob).
  4. On rehydration, Pydantic created plain ``BaseMessage`` instances rather
     than ``AIMessage``/``ToolMessage`` subclasses, so the cuga_lite reader's
     ``isinstance`` checks failed and silently dropped the ToolMessage.
  5. Net effect: a slash-dispatched skill's load_skill stanza vanished on
     the next turn — the model would say "I have not called any tools yet"
     when asked about the prior skill.

The fix combines ``SerializeAsAny[BaseMessage]`` on the field annotation
(forces runtime-subclass serialization) with a ``mode="before"`` field
validator that rebuilds the proper subclasses on rehydration.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from cuga.backend.cuga_graph.state.agent_state import AgentState, rehydrate_messages


def _make_slash_messages():
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


# --- model_dump (write-side) preserves subclass-specific fields --------------


def test_model_dump_preserves_ai_tool_calls():
    state = AgentState(input="x", url="", chat_messages=_make_slash_messages())
    dumped = state.model_dump()["chat_messages"]
    assert dumped[1]["tool_calls"] == [
        {"id": "abc123", "name": "load_skill", "args": {"name": "kwargs-check"}, "type": "tool_call"}
    ]


def test_model_dump_preserves_tool_message_id():
    state = AgentState(input="x", url="", chat_messages=_make_slash_messages())
    dumped = state.model_dump()["chat_messages"]
    assert dumped[2]["tool_call_id"] == "abc123"


# --- field_validator (read-side) restores proper subclasses ------------------


def test_round_trip_restores_subclasses_and_fields():
    state = AgentState(input="x", url="", chat_messages=_make_slash_messages())
    dumped = state.model_dump()

    restored = AgentState(**dumped)
    msgs = restored.chat_messages or []
    assert len(msgs) == 3
    assert isinstance(msgs[0], HumanMessage)
    assert isinstance(msgs[1], AIMessage)
    assert isinstance(msgs[2], ToolMessage)
    # The whole point: tool_calls + tool_call_id survive the cycle.
    assert msgs[1].tool_calls == [
        {"id": "abc123", "name": "load_skill", "args": {"name": "kwargs-check"}, "type": "tool_call"}
    ]
    assert msgs[2].tool_call_id == "abc123"
    # additional_kwargs (slice #17 audit metadata) survives too.
    assert msgs[1].additional_kwargs["invoked_via"] == "slash"


def test_rehydrate_messages_promotes_known_types():
    """The helper itself is pure — verify each supported type promotes
    correctly from dict form."""
    promoted = rehydrate_messages(
        [
            {"type": "human", "content": "hi"},
            {"type": "ai", "content": "", "tool_calls": []},
            {"type": "tool", "content": "out", "tool_call_id": "id"},
            {"type": "system", "content": "sys"},
        ]
    )
    assert isinstance(promoted[0], HumanMessage)
    assert isinstance(promoted[1], AIMessage)
    assert isinstance(promoted[2], ToolMessage)
    assert isinstance(promoted[3], SystemMessage)


def test_rehydrate_messages_passes_through_existing_subclasses():
    """If the caller already gave us proper subclass instances (the normal
    construction path), leave them alone."""
    original = _make_slash_messages()
    promoted = rehydrate_messages(original)
    # Same objects, not copies.
    assert promoted[0] is original[0]
    assert promoted[1] is original[1]
    assert promoted[2] is original[2]


def test_rehydrate_messages_demotes_plain_base_messages():
    """Pydantic's older ``List[BaseMessage]`` rehydration produced plain
    ``BaseMessage`` instances. The validator must re-promote those, not
    accept them as-is."""
    raw = BaseMessage(type="ai", content="hi")
    promoted = rehydrate_messages([raw])
    assert isinstance(promoted[0], AIMessage)


def test_rehydrate_messages_handles_none_and_empty():
    assert rehydrate_messages(None) is None
    assert rehydrate_messages([]) == []
