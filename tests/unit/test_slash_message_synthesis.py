import asyncio

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from cuga.backend.skills.registry import SkillEntry, SkillRegistry
from cuga.backend.slash_commands import build_slash_registry, parse_and_dispatch
from cuga.backend.slash_commands.message_synthesis import (
    new_tool_call_id,
    synthesize_skill_invocation,
)


def test_four_message_structure_with_args():
    messages = synthesize_skill_invocation(
        raw_input="/deck make a 3-slide intro",
        raw_args="make a 3-slide intro",
        resolved_name="deck",
        wrapped_body="WRAPPED BODY",
    )
    assert len(messages) == 4
    h0, a, t, h1 = messages
    assert isinstance(h0, HumanMessage)
    assert h0.content == "/deck make a 3-slide intro"
    assert isinstance(a, AIMessage)
    assert isinstance(t, ToolMessage)
    assert isinstance(h1, HumanMessage)
    assert h1.content == "make a 3-slide intro"


def test_three_message_structure_when_args_empty():
    messages = synthesize_skill_invocation(
        raw_input="/deck",
        raw_args="",
        resolved_name="deck",
        wrapped_body="WRAPPED BODY",
    )
    assert len(messages) == 3
    assert all(not isinstance(m, HumanMessage) or m.content == "/deck" for m in messages)


def test_tool_call_id_round_trips_between_ai_and_tool_messages():
    messages = synthesize_skill_invocation(
        raw_input="/deck",
        raw_args="",
        resolved_name="deck",
        wrapped_body="BODY",
    )
    ai_msg = messages[1]
    tool_msg = messages[2]
    assert isinstance(ai_msg, AIMessage)
    assert isinstance(tool_msg, ToolMessage)
    assert len(ai_msg.tool_calls) == 1
    assert ai_msg.tool_calls[0]["id"] == tool_msg.tool_call_id


def test_tool_call_targets_load_skill_with_resolved_name():
    messages = synthesize_skill_invocation(
        raw_input="/excel format A1",
        raw_args="format A1",
        resolved_name="excel",
        wrapped_body="BODY",
    )
    ai_msg = messages[1]
    tc = ai_msg.tool_calls[0]
    assert tc["name"] == "load_skill"
    assert tc["args"] == {"name": "excel"}


def test_additional_kwargs_carry_invoked_via_and_raw_input():
    messages = synthesize_skill_invocation(
        raw_input="/excel A1:B2",
        raw_args="A1:B2",
        resolved_name="excel",
        wrapped_body="BODY",
    )
    ai_msg = messages[1]
    assert ai_msg.additional_kwargs.get("invoked_via") == "slash"
    assert ai_msg.additional_kwargs.get("raw_input") == "/excel A1:B2"
    assert ai_msg.additional_kwargs.get("resolved_name") == "excel"


def test_tool_call_id_override_is_used():
    messages = synthesize_skill_invocation(
        raw_input="/x",
        raw_args="",
        resolved_name="x",
        wrapped_body="B",
        tool_call_id="fixed-id-123",
    )
    assert messages[1].tool_calls[0]["id"] == "fixed-id-123"
    assert messages[2].tool_call_id == "fixed-id-123"


def test_new_tool_call_id_is_unique_and_namespaced():
    ids = {new_tool_call_id() for _ in range(50)}
    assert len(ids) == 50
    assert all(i.startswith("slash_load_skill_") for i in ids)


def test_dispatcher_populates_injected_messages_for_known_skill():
    skills = SkillRegistry(
        [
            SkillEntry(
                name="deck",
                description="Build slide decks",
                body="MAKE SLIDES",
                source="/p/deck/SKILL.md",
            )
        ]
    )
    reg = build_slash_registry(skill_registry=skills)
    result = asyncio.run(
        parse_and_dispatch(
            "/deck two slides about caching",
            slash_registry=reg,
            skill_registry=skills,
        )
    )
    assert result.kind == "skill"
    assert result.resolved_name == "deck"
    assert result.injected_messages
    # 4 messages because args are non-empty
    assert len(result.injected_messages) == 4
    ai_msg = result.injected_messages[1]
    assert ai_msg.tool_calls[0]["args"] == {"name": "deck"}
    # Slice #17: args appended verbatim to the wrapped body
    tool_msg = result.injected_messages[2]
    assert "ARGUMENTS: two slides about caching" in tool_msg.content
    # Slice #17: wrapped body comes from registry.load_skill (full wrapping)
    assert "STEP 2 — SKILL INSTRUCTIONS" in tool_msg.content
    assert "MAKE SLIDES" in tool_msg.content


def test_dispatcher_skill_without_args_omits_trailing_human_message():
    skills = SkillRegistry([SkillEntry(name="deck", description="d", body="BODY", source="/p")])
    reg = build_slash_registry(skill_registry=skills)
    result = asyncio.run(parse_and_dispatch("/deck", slash_registry=reg, skill_registry=skills))
    assert result.kind == "skill"
    assert len(result.injected_messages) == 3
