"""
Tests for the per-task tool-call cap (advanced_features.max_tool_calls).
"""

from types import SimpleNamespace

import pytest
from langchain_core.tools import StructuredTool

from cuga.backend.activity_tracker.tracker import ActivityTracker
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common.call_api_helper import CallApiHelper
from cuga.backend.cuga_graph.nodes.cuga_lite.tracking import tracker as tracker_module
from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import ToolCallTracker


def _set_cap(monkeypatch, cap):
    """Pin the cap via cuga.config.settings (read inside enforce_call_budget),
    immune to dynaconf state left behind by other tests in the full suite."""
    monkeypatch.setattr(
        "cuga.config.settings",
        SimpleNamespace(advanced_features=SimpleNamespace(max_tool_calls=cap)),
    )


def test_enforce_raises_clear_error_past_cap(monkeypatch):
    _set_cap(monkeypatch, 3)

    ToolCallTracker.seed_call_budget(0)
    for _ in range(3):
        ToolCallTracker.enforce_call_budget()

    with pytest.raises(RuntimeError, match="final answer from the data already retrieved"):
        ToolCallTracker.enforce_call_budget()


def test_budget_carries_over_from_prior_steps(monkeypatch):
    """Seeding with the count from earlier steps makes the cap per task, not per block."""
    _set_cap(monkeypatch, 100)

    ToolCallTracker.seed_call_budget(99)
    ToolCallTracker.enforce_call_budget()  # 100th call — still allowed
    assert ToolCallTracker.get_call_budget_used() == 100

    with pytest.raises(RuntimeError, match="Tool call limit reached"):
        ToolCallTracker.enforce_call_budget()


def test_unseeded_context_and_zero_cap_are_not_enforced(monkeypatch):
    _set_cap(monkeypatch, 1)

    # Outside a seeded sandbox context the budget is a no-op.
    tracker_module._tool_call_budget_context.set(None)
    for _ in range(5):
        ToolCallTracker.enforce_call_budget()

    # max_tool_calls = 0 disables the cap entirely.
    _set_cap(monkeypatch, 0)
    ToolCallTracker.seed_call_budget(0)
    for _ in range(5):
        ToolCallTracker.enforce_call_budget()
    assert ToolCallTracker.get_call_budget_used() == 5


@pytest.mark.asyncio
async def test_local_call_api_respects_cap(monkeypatch):
    _set_cap(monkeypatch, 2)

    async def echo(value: int) -> int:
        return value

    tool = StructuredTool.from_function(coroutine=echo, name="echo", description="Echo a value.")
    tracker = ActivityTracker()
    monkeypatch.setattr(tracker, "tools", {"test_app": [tool]})

    call_api = CallApiHelper.create_local_call_api_function()
    ToolCallTracker.seed_call_budget(0)

    assert await call_api("test_app", "echo", {"value": 1}) == "1"
    assert await call_api("test_app", "echo", {"value": 2}) == "2"

    with pytest.raises(RuntimeError, match="Tool call limit reached"):
        await call_api("test_app", "echo", {"value": 3})
