"""Unit tests for the slash → planner-input translation (soft dispatch).

A slash skill invocation is not forced on the planner: ``/<skill> args`` is
translated into a plain planner input that *suggests* the skill, and the
planner decides to call ``load_skill`` itself. These tests pin the
translation text and the dispatcher's use of it.
"""

import asyncio

import pytest

from cuga.backend.skills.registry import SkillEntry, SkillRegistry
from cuga.backend.slash_commands import build_slash_registry, parse_and_dispatch
from cuga.backend.slash_commands.translation import translate_skill_invocation

pytestmark = pytest.mark.unit


def test_translation_embeds_skill_name_and_args():
    text = translate_skill_invocation("deck", "make a 3-slide intro")
    assert text == "use the skill named 'deck' to: make a 3-slide intro"


def test_translation_empty_args_form():
    assert translate_skill_invocation("deck", "") == "use the skill named 'deck'"


def test_translation_preserves_args_verbatim_including_quotes_and_newlines():
    raw_args = "it's \"quoted\"\nand new-lined"
    text = translate_skill_invocation("echo", raw_args)
    assert text == f"use the skill named 'echo' to: {raw_args}"
    # The args substring survives untouched — no escaping, no repr().
    assert raw_args in text


def test_translation_is_plain_text_not_code():
    """Soft dispatch: no forged CodeAct turns, no code fences, no load_skill call."""
    text = translate_skill_invocation("excel", "format A1")
    assert "```" not in text
    assert "load_skill(" not in text
    assert "Execution output" not in text


def test_dispatcher_populates_planner_input_for_known_skill():
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
    assert result.planner_input == "use the skill named 'deck' to: two slides about caching"
    # The original utterance is preserved for display/history.
    assert result.raw_input == "/deck two slides about caching"
    assert result.raw_args == "two slides about caching"
    # Soft dispatch: the skill body is NOT loaded at dispatch time — the
    # planner calls load_skill itself.
    assert "MAKE SLIDES" not in result.planner_input


def test_dispatcher_skill_without_args_uses_short_form():
    skills = SkillRegistry([SkillEntry(name="deck", description="d", body="BODY", source="/p")])
    reg = build_slash_registry(skill_registry=skills)
    result = asyncio.run(parse_and_dispatch("/deck", slash_registry=reg, skill_registry=skills))
    assert result.kind == "skill"
    assert result.planner_input == "use the skill named 'deck'"
    assert result.raw_input == "/deck"


def test_dispatcher_unknown_slash_has_no_planner_input():
    skills = SkillRegistry([SkillEntry(name="deck", description="d", body="BODY", source="/p")])
    reg = build_slash_registry(skill_registry=skills)
    result = asyncio.run(parse_and_dispatch("/nope args", slash_registry=reg, skill_registry=skills))
    assert result.kind == "unknown"
    assert result.planner_input is None
