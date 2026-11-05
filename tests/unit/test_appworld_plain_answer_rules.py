"""The AppWorld plain final-answer prompt must carry its formatting rules.

These rules are the only thing standing between a correct computation and a
rejected answer: AppWorld compares the completion literally, so `Yes.` fails
where `Yes` passes, and picking the wrong figure from several candidates fails
outright. A prompt edit that drops them is silent, hence this test.
"""

import pytest

from cuga.backend.cuga_graph.nodes.answer.final_answer_agent.prompts.load_prompt import (
    load_appworld_plain_final_answer_prompt,
)

pytestmark = pytest.mark.unit


def _system_text() -> str:
    template = load_appworld_plain_final_answer_prompt()
    return "\n".join(str(getattr(m, "prompt", m).template) for m in template.messages if hasattr(m, "prompt"))


def test_yes_no_rule_is_present_and_forbids_a_trailing_period():
    text = _system_text()
    assert "Yes/no answers" in text
    assert "no trailing period" in text


def test_candidate_figure_rule_is_present():
    text = _system_text()
    assert "Choosing between candidate figures" in text
    assert "Do **not** default to the last figure" in text


def test_general_punctuation_rule_is_retained():
    """The yes/no carve-out must not have inverted the rule it qualifies.

    Note and message tasks legitimately end in a period, so the general
    'preserve punctuation' guidance has to survive alongside the exception.
    """
    text = _system_text()
    assert "Do **not** remove punctuation that is part of the answer" in text
    assert "Notes, messages, and arbitrary strings" in text
