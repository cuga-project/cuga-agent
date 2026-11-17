"""Presence checks for the task-conditions check in the reflection prompt (issue #1171).

The reflection step lists each condition the task uses to pick out a person,
record, set or value, with the tool result that shows it, and holds a write
back while one has no evidence. These tests check the rule and the output
line in the rendered prompt: the wording may change, the rule must stay.
"""

from __future__ import annotations

from pathlib import Path

import jinja2
import pytest

_REFLECTION_PROMPT = (
    Path(__file__).resolve().parents[1] / "reflection" / "prompts" / "reflection_system.jinja2"
)


def _reflection_prompt_text() -> str:
    template = jinja2.Environment().from_string(_REFLECTION_PROMPT.read_text(encoding="utf-8"))
    return " ".join(template.render().split())


@pytest.mark.unit
def test_reflection_requires_evidence_for_each_task_condition():
    text = _reflection_prompt_text()
    assert "**Task conditions and their evidence**" in text
    assert "or a statement of it by the user or in the instructions you were given" in text
    for bullet in (
        "**A condition describes a thing, not a step.**",
        "**A value the task gives directly is not a condition.**",
        "**A role or relationship needs a record that states it.**",
        "**A stated value is not an observed value.**",
        "**A search hit is not a match.**",
        "**A filter must be tested per item.**",
        "**Resolve before writing.**",
        "**After a write.**",
    ):
        assert bullet in text, bullet


@pytest.mark.unit
def test_reflection_output_has_a_conditions_line_between_status_and_next():
    text = _reflection_prompt_text()
    assert "exactly three labelled lines" in text
    status, conditions, nxt = (text.index(label) for label in ("STATUS: One", "CONDITIONS: One", "NEXT: One"))
    assert status < conditions < nxt
    assert "`<condition> -> no evidence`" in text
    assert "CONDITIONS: not applicable." in text


@pytest.mark.unit
def test_reflection_does_not_take_the_users_own_details_as_evidence_for_someone_else():
    text = _reflection_prompt_text()
    assert "identify the user and nobody else" in text
    assert "so is a role or relationship with no record stating it" in text
    assert "no actions, no progress, no values the task gives directly" in text


@pytest.mark.unit
def test_reflection_does_not_endorse_a_write_on_an_unresolved_condition():
    text = _reflection_prompt_text()
    assert "NEXT must be the read that resolves it, not the write" in text
    assert "no condition is marked `no evidence`, write exactly: NEXT: task complete." in text
