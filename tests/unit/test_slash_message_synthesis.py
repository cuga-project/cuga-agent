import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from cuga.backend.skills.registry import SkillEntry, SkillRegistry
from cuga.backend.slash_commands import build_slash_registry, parse_and_dispatch
from cuga.backend.slash_commands.message_synthesis import synthesize_skill_invocation

pytestmark = pytest.mark.unit


def test_three_message_structure_with_args():
    messages = synthesize_skill_invocation(
        raw_input="/deck make a 3-slide intro",
        raw_args="make a 3-slide intro",
        resolved_name="deck",
        wrapped_body="WRAPPED BODY",
    )
    assert len(messages) == 3
    h0, a, h1 = messages
    assert isinstance(h0, HumanMessage)
    assert h0.content == "/deck make a 3-slide intro"
    assert isinstance(a, AIMessage)
    assert isinstance(h1, HumanMessage)
    assert h1.content.startswith("Execution output:\n")
    assert "WRAPPED BODY" in h1.content


def test_three_message_structure_when_args_empty():
    messages = synthesize_skill_invocation(
        raw_input="/deck",
        raw_args="",
        resolved_name="deck",
        wrapped_body="WRAPPED BODY",
    )
    assert len(messages) == 3
    h0, a, h1 = messages
    assert isinstance(h0, HumanMessage)
    assert h0.content == "/deck"
    assert isinstance(a, AIMessage)
    assert isinstance(h1, HumanMessage)
    assert h1.content == "Execution output:\nWRAPPED BODY"


def test_ai_message_contains_code_block_calling_load_skill():
    messages = synthesize_skill_invocation(
        raw_input="/excel format A1",
        raw_args="format A1",
        resolved_name="excel",
        wrapped_body="BODY",
    )
    ai_msg = messages[1]
    content = ai_msg.content
    assert content.startswith("```python\n")
    assert content.endswith("\n```")
    assert "await load_skill('excel', 'format A1')" in content
    assert "print(result)" in content
    # AIMessage with code-block content must not carry structured tool_calls
    # (cuga_agent_core's shared_nodes.py only emits role/content; the planner
    # never speaks tool_use).
    assert not ai_msg.tool_calls


def test_third_message_is_execution_output_with_wrapped_body_verbatim():
    messages = synthesize_skill_invocation(
        raw_input="/x",
        raw_args="",
        resolved_name="x",
        wrapped_body="line one\nline two",
    )
    h1 = messages[2]
    assert h1.content == "Execution output:\nline one\nline two"


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


def test_args_with_quotes_and_backslashes_are_safely_escaped_in_code_block():
    """The forged code block uses ``repr()`` so args containing quotes,
    backslashes, or newlines do not break the Python literal. The model
    receives a syntactically valid code block regardless of input."""
    messages = synthesize_skill_invocation(
        raw_input="/echo it's \"quoted\"\nand new-lined",
        raw_args="it's \"quoted\"\nand new-lined",
        resolved_name="echo",
        wrapped_body="BODY",
    )
    code = messages[1].content
    # The repr-quoted literal must reproduce the original string when eval'd.
    import re

    match = re.search(r"await load_skill\('echo', (.+)\)\nprint", code, re.DOTALL)
    assert match, f"code block shape unexpected: {code!r}"
    arg_literal = match.group(1)
    assert eval(arg_literal) == "it's \"quoted\"\nand new-lined"


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
    # Always 3 messages: Human(raw_input) → AI(code) → Human(execution output).
    assert len(result.injected_messages) == 3
    ai_msg = result.injected_messages[1]
    assert "await load_skill('deck', 'two slides about caching')" in ai_msg.content
    exec_msg = result.injected_messages[2]
    assert exec_msg.content.startswith("Execution output:\n")
    # Raw args appear inside the wrapped skill body via $ARGUMENTS substitution.
    assert "ARGUMENTS: two slides about caching" in exec_msg.content
    # The wrapped body comes from registry.load_skill (full wrapping).
    assert "STEP 1 — SKILL INSTRUCTIONS" in exec_msg.content
    assert "MAKE SLIDES" in exec_msg.content


def test_dispatcher_skill_without_args_still_produces_codeact_trio():
    skills = SkillRegistry([SkillEntry(name="deck", description="d", body="BODY", source="/p")])
    reg = build_slash_registry(skill_registry=skills)
    result = asyncio.run(parse_and_dispatch("/deck", slash_registry=reg, skill_registry=skills))
    assert result.kind == "skill"
    assert len(result.injected_messages) == 3
    ai_msg = result.injected_messages[1]
    assert "await load_skill('deck', '')" in ai_msg.content
