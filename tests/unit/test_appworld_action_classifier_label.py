"""Parse AppWorld ACTION vs QUERY classifier labels."""

import pytest

from cuga.backend.cuga_graph.nodes.answer.final_answer_agent.prompts.load_prompt import (
    is_appworld_action_label,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("ACTION", True),
        ("action", True),
        ("ACTION\n", True),
        ("QUERY", False),
        ("query", False),
        ("ACTION or QUERY", False),
        ("", False),
    ],
)
def test_is_appworld_action_label(raw, expected):
    assert is_appworld_action_label(raw) is expected
