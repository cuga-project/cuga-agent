"""Presence checks for general prompt guidance restored after PR #1102 (issue #1172).

PR #1102 removed these sections from the agent and reflection prompts as a
simplification; the first evaluation runs after the change showed the failure
modes they guard against coming back. These tests make the sections' presence
explicit so that a future simplification pass has to delete a test on purpose.
They check headlines only: the wording may change, the rule must stay.
"""

from __future__ import annotations

from pathlib import Path

import jinja2
import pytest

_PROMPTS = Path(__file__).resolve().parents[1]
_REFLECTION_PROMPT = _PROMPTS / "reflection" / "prompts" / "reflection_system.jinja2"
_AGENT_PROMPT = _PROMPTS / "prompts" / "mcp_prompt.jinja2"


def _collapsed(text: str) -> str:
    """Whitespace-collapsed text, so a re-wrapped line is not a failure."""
    return " ".join(text.split())


def _reflection_prompt_text() -> str:
    return _collapsed(_REFLECTION_PROMPT.read_text(encoding="utf-8"))


def _rendered_agent_prompt(**context) -> str:
    template = jinja2.Environment().from_string(_AGENT_PROMPT.read_text(encoding="utf-8"))
    return _collapsed(template.render(**context))


# ── Reflection prompt ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_reflection_checks_multi_operation_completeness():
    text = _reflection_prompt_text()
    assert "**Multi-operation completeness**" in text
    assert "Partial completion is not completion" in text


@pytest.mark.unit
def test_reflection_inspects_api_responses_for_errors_before_judging_success():
    text = _reflection_prompt_text()
    assert "**API-level error check (mandatory):**" in text
    assert '`"status": "exception"`' in text


@pytest.mark.unit
def test_reflection_keeps_recommendations_within_task_scope():
    text = _reflection_prompt_text()
    assert "**Scope Discipline**" in text
    assert "Never recommend an action the `Current Task` did not ask for" in text


@pytest.mark.unit
def test_reflection_separates_discovery_from_selection():
    text = _reflection_prompt_text()
    assert "**Discovery vs. Selection**" in text
    for bullet in (
        "**Type or category**",
        "**Qualifiers that identify a specific record**",
        "**Ordinal and superlative wording**",
        "**Preserve attributes when moving or copying**",
    ):
        assert bullet in text, bullet


# ── Agent (CodeAct) prompt ─────────────────────────────────────────────────


@pytest.mark.unit
def test_agent_prompt_treats_obstacles_as_per_resource():
    rendered = _rendered_agent_prompt(enable_find_tools=False)
    assert "### Obstacle Handling & Recovery" in rendered
    assert "**OBSTACLES ARE PER-RESOURCE, NOT PER-TASK:**" in rendered
    assert "**Resume, do not restart:**" in rendered
    assert "Only declare failure after stating which alternatives you tried" in rendered


@pytest.mark.unit
def test_agent_prompt_capability_check_follows_find_tools_flag():
    bullet = "**Verify a claimed missing capability before relying on it:**"
    assert bullet in _rendered_agent_prompt(enable_find_tools=True)
    assert bullet not in _rendered_agent_prompt(enable_find_tools=False)
