"""Step discipline in CodeAct: one tool call per code block.

``cuga_lite_step_discipline = "one_tool_per_step"`` rides the per-block budget
from #560 with the cap forced to 1 for the block. What has to hold, and is
only visible on a real graph run:

- the second call in a block is refused before it runs, with the one-tool
  message, and is neither executed nor recorded nor counted;
- the block's variables — including the first call's result — survive the
  refusal, so the next block can use them (the executor keeps the frame);
- with discipline off every call runs, exactly as before.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import MemorySaver

from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph import CugaLiteState, create_cuga_lite_graph
from cuga.backend.cuga_graph.nodes.cuga_lite.tracking import tracker as tracker_module
from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import (
    BlockToolCallBudgetExceeded,
    ToolCallTracker,
    tracked_tool,
)

pytestmark = pytest.mark.unit

CALLS: list = []

TWO_CALL_BLOCK = (
    "```python\nfirst = await echo(value=1)\nsecond = await echo(value=2)\nprint(first, second)\n```"
)
USE_FIRST_BLOCK = "```python\nprint('kept:', first + 10)\n```"
FINAL = "First was 1."


class _ScriptedModel:
    def __init__(self, responses):
        self._responses = list(responses)
        self.invocations = 0

    def bind_tools(self, *args, **kwargs):
        return self

    async def ainvoke(self, messages, config=None, **kwargs):
        self.invocations += 1
        if not self._responses:
            raise AssertionError(
                f"model asked for response #{self.invocations} — the run did not end when it should"
            )
        return AIMessage(content=self._responses.pop(0))


def _provider():
    # A custom-provider tool records itself via @tracked_tool (direct LangChain
    # tools get the same wrapper from prepare). Recording sits *inside* the
    # budget wrapper the executor adds, which is what makes "refused, therefore
    # not recorded" observable below.
    @tracked_tool(app_name="test_app")
    async def echo(value: int) -> int:
        """Echo a value."""
        CALLS.append(value)
        return value

    provider = MagicMock()
    provider.get_all_tools = AsyncMock(
        return_value=[StructuredTool.from_function(coroutine=echo, name="echo", description="Echo a value.")]
    )
    provider.get_apps = AsyncMock(return_value=[])
    provider.get_tools = AsyncMock(return_value=[])
    provider.app_name = "test_app"
    return provider


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    from cuga.config import settings

    monkeypatch.setattr(settings.policy, "enabled", False, raising=False)
    monkeypatch.setattr(settings.advanced_features, "max_tool_calls_per_block", 100, raising=False)
    CALLS.clear()
    yield
    CALLS.clear()
    tracker_module._tool_call_budget_context.set(None)
    tracker_module._thread_tool_call_budget_context.set(None)
    tracker_module._block_tool_call_budget_context.set(None)
    tracker_module._block_tool_call_cap_override_context.set(None)


def _run(responses, **configurable):
    model = _ScriptedModel(responses)
    graph = create_cuga_lite_graph(
        model=model, tool_provider=_provider(), apps_list=[], thread_id="t"
    ).compile(checkpointer=MemorySaver())
    config = {
        "configurable": {"thread_id": "sd", "enable_todos": False, "track_tool_calls": True, **configurable}
    }
    return graph.ainvoke(
        CugaLiteState(chat_messages=[HumanMessage(content="echo twice")]), config=config
    ), model


# ── tracker: the override and its restore ────────────────────────────────────


def test_cap_override_refuses_the_second_call_with_the_one_tool_message_and_restores_on_reset():
    ToolCallTracker.seed_call_budget(0)
    token = ToolCallTracker.set_block_cap_override(1)
    try:
        ToolCallTracker.seed_block_budget()
        ToolCallTracker.enforce_call_budget()
        with pytest.raises(BlockToolCallBudgetExceeded) as exc:
            ToolCallTracker.enforce_call_budget()
        assert "One tool call per step" in str(exc.value)
        assert "were kept" in str(exc.value)
        assert ToolCallTracker.get_run_budget_used() == 1, "a refused call is never counted"
    finally:
        ToolCallTracker.reset_block_cap_override(token)

    # Back to the settings cap (100 here): the same block budget now admits more calls.
    ToolCallTracker.seed_block_budget()
    for _ in range(5):
        ToolCallTracker.enforce_call_budget()
    assert ToolCallTracker.get_run_budget_used() == 6


# ── graph: refuse, keep variables, do not record ─────────────────────────────


@pytest.mark.asyncio
async def test_one_tool_per_step_refuses_the_second_call_and_keeps_the_first_result():
    run, model = _run([TWO_CALL_BLOCK, USE_FIRST_BLOCK, FINAL], cuga_lite_step_discipline="one_tool_per_step")
    result = await run

    assert CALLS == [1], f"only the first call of the block may run, got {CALLS}"
    assert result["final_answer"] == FINAL and model.invocations == 3

    outputs = [m.content for m in result["chat_messages"] if isinstance(m, HumanMessage)][1:]
    assert "One tool call per step" in outputs[0], "the model is told why the block stopped"
    assert "kept: 11" in outputs[1], "the first call's result survived the refusal into the next block"

    assert [c["name"] for c in result["tool_calls"]] == ["echo"], "the refused call is not recorded"
    assert result["tool_calls_used_run"] == 1, "nor counted against the run budget"


@pytest.mark.asyncio
async def test_discipline_off_runs_every_call_as_before():
    run, model = _run([TWO_CALL_BLOCK, FINAL])
    result = await run

    assert CALLS == [1, 2]
    assert result["tool_calls_used_run"] == 2 and model.invocations == 2
    assert "One tool call per step" not in " ".join(
        m.content for m in result["chat_messages"] if isinstance(m, HumanMessage)
    )
