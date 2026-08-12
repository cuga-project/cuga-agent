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


@pytest.mark.unit
def test_enforce_raises_clear_error_past_cap(monkeypatch):
    """Past the cap, the error must tell the model what to do instead of
    simply failing — it is recoverable guidance, not a crash."""
    _set_cap(monkeypatch, 3)

    ToolCallTracker.seed_call_budget(0)
    for _ in range(3):
        ToolCallTracker.enforce_call_budget()

    with pytest.raises(RuntimeError, match="final answer from the data already retrieved"):
        ToolCallTracker.enforce_call_budget()


@pytest.mark.unit
def test_budget_carries_over_from_prior_steps(monkeypatch):
    """Seeding with the count from earlier steps makes the cap per task, not per block."""
    _set_cap(monkeypatch, 100)

    ToolCallTracker.seed_call_budget(99)
    ToolCallTracker.enforce_call_budget()  # 100th call — still allowed
    assert ToolCallTracker.get_call_budget_used() == 100

    with pytest.raises(RuntimeError, match="Tool call limit reached"):
        ToolCallTracker.enforce_call_budget()


@pytest.mark.unit
def test_unseeded_context_and_zero_cap_are_not_enforced(monkeypatch):
    """Two no-op cases: outside a seeded sandbox context (non-CugaLite callers)
    and with the cap disabled via max_tool_calls = 0."""
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


@pytest.mark.unit
@pytest.mark.asyncio
async def test_exhaustion_returns_control_to_the_model(monkeypatch):
    """Cap exhaustion inside executed code surfaces as execution output (the
    CodeExecutor catches in-code exceptions), so the model keeps control and
    can synthesize a final answer from the data already retrieved."""
    from unittest.mock import MagicMock

    from cuga.backend.cuga_graph.nodes.cuga_lite.executors import CodeExecutor
    from cuga.backend.cuga_graph.state.agent_state import AgentState, VariablesManager

    _set_cap(monkeypatch, 1)

    async def echo(value: int) -> int:
        """Trivial tool that echoes its argument."""
        return value

    tool = StructuredTool.from_function(coroutine=echo, name="echo", description="Echo a value.")
    tracker = ActivityTracker()
    monkeypatch.setattr(tracker, "tools", {"test_app": [tool]})

    state = MagicMock(spec=AgentState)
    state.variables_manager = VariablesManager()
    ToolCallTracker.seed_call_budget(0)

    code = (
        "r1 = await call_api('test_app', 'echo', {'value': 1})\n"
        "r2 = await call_api('test_app', 'echo', {'value': 2})\n"
    )
    output, _ = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals={"call_api": CallApiHelper.create_local_call_api_function()},
        state=state,
        mode="local",
    )

    assert "Tool call limit reached" in output  # recoverable feedback, no raise


@pytest.mark.unit
@pytest.mark.asyncio
async def test_local_call_api_respects_cap(monkeypatch):
    """End-to-end through the local call_api closure: calls under the cap
    succeed, the one past it raises."""
    _set_cap(monkeypatch, 2)

    async def echo(value: int) -> int:
        """Trivial tool that echoes its argument."""
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
