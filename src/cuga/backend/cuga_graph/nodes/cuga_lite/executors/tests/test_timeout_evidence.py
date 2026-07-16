"""
Tests for timeout evidence preservation and the per-block tool-call budget.

When a sandbox code block hits `sandbox_execution_timeout`, the agent used to
receive only a bare "Execution timed out" traceback: stdout printed before the
kill and the number of tool calls completed were discarded, so the agent's
usual response was to re-run the same doomed loop until the step limit.
`LocalExecutor.execute` now returns the partial stdout, the per-block
tool-call count, and restructuring guidance instead.

The count comes from `BlockToolCallBudget`, which also enforces an optional
per-block budget (`advanced_features.max_tool_calls_per_block`, 0 = off).
"""

import asyncio

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.local.local_executor import LocalExecutor
from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import (
    BlockToolCallBudget,
    ToolCallBudgetExceededError,
)


def _wrap(body: str) -> str:
    indented = "\n".join("    " + line for line in body.split("\n"))
    return f"""
import asyncio
async def _async_main():
{indented}
    return locals()
"""


@pytest.mark.asyncio
async def test_timeout_returns_partial_stdout_and_guidance():
    """A timed-out block reports its partial stdout instead of discarding it."""
    executor = LocalExecutor()
    code = _wrap(
        """print("progress: fetched 3 of 100")
await asyncio.sleep(5)
print("never printed")"""
    )

    result = await executor.execute(wrapped_code=code, context_locals={}, timeout=1)

    assert "timed out after 1 seconds" in result
    assert "progress: fetched 3 of 100" in result
    assert "never printed" not in result
    # Guidance must tell the agent not to blindly retry.
    assert "Do NOT rerun the same code" in result
    assert "variables" in result.lower()


@pytest.mark.asyncio
async def test_timeout_reports_no_stdout_case():
    """A silent timed-out block still gets guidance, with an explicit no-stdout note."""
    executor = LocalExecutor()
    code = _wrap("await asyncio.sleep(5)")

    result = await executor.execute(wrapped_code=code, context_locals={}, timeout=1)

    assert "timed out after 1 seconds" in result
    assert "no stdout was printed" in result


@pytest.mark.asyncio
async def test_timeout_reports_block_tool_call_count():
    """The count of tool calls completed before the kill survives the task boundary.

    `asyncio.wait_for` runs the block in a Task whose context is a copy of the
    executor's; the shared-dict holder makes increments inside the block
    visible to the executor's timeout handler.
    """
    executor = LocalExecutor()

    async def fake_tool():
        BlockToolCallBudget.check_and_increment()
        return {"ok": True}

    code = _wrap(
        """for _ in range(7):
    await fake_tool()
await asyncio.sleep(5)"""
    )

    result = await executor.execute(wrapped_code=code, context_locals={"fake_tool": fake_tool}, timeout=1)

    assert "completed 7 tool call(s)" in result


@pytest.mark.asyncio
async def test_successful_block_unaffected():
    """Blocks that finish in time keep the existing output contract."""
    executor = LocalExecutor()
    code = _wrap('print("done")')

    result = await executor.execute(wrapped_code=code, context_locals={}, timeout=5)

    assert result == "done\n"


@pytest.mark.asyncio
async def test_budget_breach_raises_inside_wait_for_task(monkeypatch):
    """With a budget configured, the block aborts on the call after the limit."""
    from cuga.config import settings

    monkeypatch.setattr(settings.advanced_features, "max_tool_calls_per_block", 5, raising=False)

    async def over_budget():
        for _ in range(6):
            BlockToolCallBudget.check_and_increment()

    BlockToolCallBudget.reset()
    with pytest.raises(ToolCallBudgetExceededError):
        await asyncio.wait_for(over_budget(), timeout=5)


def test_budget_disabled_by_default_and_without_scope(monkeypatch):
    """Budget 0 (the default) never raises, and no open block scope is a no-op."""
    from cuga.config import settings

    monkeypatch.setattr(settings.advanced_features, "max_tool_calls_per_block", 0, raising=False)
    BlockToolCallBudget.reset()
    for _ in range(1000):
        BlockToolCallBudget.check_and_increment()
    assert BlockToolCallBudget.current_count() == 1000
