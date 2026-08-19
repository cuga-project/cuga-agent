"""The rendered plan must present itself as intent, not as truth (issue #676).

The old wording — "use this list as the source of truth" — produced failures in both
directions on AppWorld: a stale `in_progress` made the agent re-run work it had already
finished (07bb666_1: 6 cart fetches and 5 moves for one pass of work), and an
all-`completed` plan let it report success for actions that never happened (12 of 17
failing tasks ended all-`completed`, versus 9 of 21 passing ones).
"""

from __future__ import annotations

import pytest

from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.todos import (
    format_current_plan_section,
    format_task_todos_system_block,
)

TODOS = [
    {"text": "Fetch all items in Amazon cart", "status": "in_progress"},
    {"text": "Move low-rated items to wish list", "status": "pending"},
]


@pytest.mark.unit
@pytest.mark.parametrize("render", [format_task_todos_system_block, format_current_plan_section])
def test_plan_is_never_called_the_source_of_truth(render):
    block = render(TODOS)
    assert "source of truth" not in block.lower()


@pytest.mark.unit
@pytest.mark.parametrize("render", [format_task_todos_system_block, format_current_plan_section])
def test_plan_states_execution_output_wins(render):
    block = render(TODOS)
    assert "the execution output wins" in block
    # Both failure directions must be named, not just the optimistic one.
    assert "not evidence the action succeeded" in block
    assert "may already be done" in block


@pytest.mark.unit
@pytest.mark.parametrize("render", [format_task_todos_system_block, format_current_plan_section])
def test_items_and_statuses_still_render(render):
    block = render(TODOS)
    for item in TODOS:
        assert item["text"] in block
        assert f"[{item['status']}]" in block


@pytest.mark.unit
@pytest.mark.parametrize(
    "steps,expected",
    [
        (0, "You updated it during the last execution."),
        (1, "You last updated it 1 step ago."),
        (4, "You last updated it 4 steps ago."),
    ],
)
def test_staleness_is_reported(steps, expected):
    assert expected in format_task_todos_system_block(TODOS, steps)
    assert expected in format_current_plan_section(TODOS, steps)


@pytest.mark.unit
def test_staleness_omitted_when_unknown():
    """Callers that cannot compute age (e.g. the supervisor adapter) must still render,
    without claiming an age they don't know."""
    block = format_task_todos_system_block(TODOS)
    assert "updated it" not in block
    assert "the execution output wins" in block


@pytest.mark.unit
def test_negative_staleness_is_not_rendered():
    assert "ago" not in format_task_todos_system_block(TODOS, -2)


@pytest.mark.unit
def test_empty_todos_render_nothing():
    assert format_task_todos_system_block([]) == ""
