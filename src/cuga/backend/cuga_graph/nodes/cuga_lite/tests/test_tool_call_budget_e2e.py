"""End-to-end tool-call budget test: the real graph, a real checkpointer, two turns.

Everything else about the budget is tested at the unit or node level, which
cannot see the two things that only exist once the graph actually runs:

1. ``tool_calls_used_thread`` has to survive a LangGraph checkpoint round-trip
   and come back on the next invocation. A node test asserts what a node
   *returns*; only a real run proves the value is persisted and re-read.
2. Exhaustion has to end the turn with a synthesised answer. Node tests pin the
   routing; this pins that the loop actually terminates rather than cycling
   call_model -> sandbox until the step limit.

The model is scripted rather than live — the point is the graph wiring and the
budget arithmetic, both of which are deterministic.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import MemorySaver

from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph import (
    CugaLiteState,
    create_cuga_lite_graph,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.tracking import tracker as tracker_module

pytestmark = pytest.mark.unit

CALLS_MADE: list[int] = []


class _ScriptedModel:
    """Returns queued responses in order; records how many times it was asked.

    The second response is the final answer the model is expected to produce
    once its tools are withheld — if the graph ever routes back to the sandbox
    instead, the queue runs dry and the test fails loudly rather than hanging.
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.invocations = 0

    def bind_tools(self, *args, **kwargs):
        return self

    async def ainvoke(self, messages, config=None, **kwargs):
        self.invocations += 1
        if not self._responses:
            raise AssertionError(
                f"model asked for response #{self.invocations} — the turn did not end when it should have"
            )
        return AIMessage(content=self._responses.pop(0))


def _tool_provider():
    async def echo(value: int) -> int:
        """Records the call so the test can count what actually executed."""
        CALLS_MADE.append(value)
        return value

    tool = StructuredTool.from_function(coroutine=echo, name="echo", description="Echo a value.")
    provider = MagicMock()
    provider.get_all_tools = AsyncMock(return_value=[tool])
    provider.get_apps = AsyncMock(return_value=[])
    provider.get_tools = AsyncMock(return_value=[])
    provider.app_name = "test_app"
    return provider


def _set_caps(monkeypatch, *, block, run, thread):
    """Set the three caps on the real settings object.

    A whole-module SimpleNamespace stand-in (fine for unit tests of the tracker)
    does not survive a real graph run — prepare alone reads ``settings.evolve``,
    ``settings.policy`` and more. Patch only the keys under test.
    """
    from cuga.config import settings as real_settings

    for key, value in (
        ("max_tool_calls_per_block", block),
        ("max_tool_calls_per_run", run),
        ("max_tool_calls_per_thread", thread),
    ):
        monkeypatch.setattr(real_settings.advanced_features, key, value, raising=False)
    monkeypatch.setattr(real_settings.policy, "enabled", False, raising=False)


LOOPING_BLOCK = "```python\nfor i in range(10):\n    await echo(value=i)\n```"
FINAL_ANSWER = "I called echo until the budget ran out; the values I got were 0 onward."


@pytest.fixture(autouse=True)
def _clean():
    CALLS_MADE.clear()
    yield
    CALLS_MADE.clear()
    tracker_module._tool_call_budget_context.set(None)
    tracker_module._thread_tool_call_budget_context.set(None)
    tracker_module._block_tool_call_budget_context.set(None)


@pytest.mark.asyncio
async def test_budget_holds_across_two_turns_of_one_conversation(monkeypatch):
    """run cap 3, thread cap 5, on one thread_id.

    Turn 1 may spend 3 (run cap). Turn 2 gets a fresh run budget but only 2
    calls left in the conversation — which is the whole point of the thread
    counter, and is only observable once the checkpoint carries it over.
    """
    _set_caps(monkeypatch, block=0, run=3, thread=5)

    model = _ScriptedModel([LOOPING_BLOCK, FINAL_ANSWER, LOOPING_BLOCK, FINAL_ANSWER])
    graph = create_cuga_lite_graph(
        model=model, tool_provider=_tool_provider(), apps_list=[], thread_id="budget-e2e"
    ).compile(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "budget-e2e", "enable_todos": False}}

    # ── Turn 1 ────────────────────────────────────────────────────────────
    turn1 = await graph.ainvoke(
        CugaLiteState(chat_messages=[HumanMessage(content="call echo a lot")]), config=config
    )

    assert len(CALLS_MADE) == 3, f"run cap 3 breached: {len(CALLS_MADE)} calls executed"
    assert turn1["tool_calls_used_run"] == 3
    assert turn1["tool_calls_used_thread"] == 3
    assert turn1["final_answer"] == FINAL_ANSWER, "exhausted turn must end with a synthesised answer"
    assert "Maximum step limit" not in (turn1.get("error") or ""), "turn thrashed into the step limit"
    assert model.invocations == 2, "one block + one synthesis pass, not a retry loop"

    # ── Turn 2, same thread ───────────────────────────────────────────────
    turn2 = await graph.ainvoke(
        CugaLiteState(chat_messages=turn1["chat_messages"] + [HumanMessage(content="do it again")]),
        config=config,
    )

    assert turn2["tool_calls_used_run"] <= 3, "the run counter must have been reset by prepare"
    assert len(CALLS_MADE) == 5, (
        f"conversation ceiling 5 breached: {len(CALLS_MADE)} calls across both turns — "
        "tool_calls_used_thread did not survive the checkpoint"
    )
    assert turn2["tool_calls_used_thread"] == 5
    assert turn2["final_answer"] == FINAL_ANSWER


@pytest.mark.asyncio
async def test_a_turn_within_budget_is_untouched(monkeypatch):
    """The control case: with budget to spare nothing intervenes — the tool runs
    its full loop and the turn ends normally."""
    _set_caps(monkeypatch, block=0, run=100, thread=100)

    model = _ScriptedModel([LOOPING_BLOCK, FINAL_ANSWER])
    graph = create_cuga_lite_graph(
        model=model, tool_provider=_tool_provider(), apps_list=[], thread_id="budget-e2e-ok"
    ).compile(checkpointer=MemorySaver())

    result = await graph.ainvoke(
        CugaLiteState(chat_messages=[HumanMessage(content="call echo a lot")]),
        config={"configurable": {"thread_id": "budget-e2e-ok", "enable_todos": False}},
    )

    assert len(CALLS_MADE) == 10, "an in-budget loop was interfered with"
    assert result["tool_calls_used_run"] == 10
    assert result["final_answer"] == FINAL_ANSWER


LOOP_3 = "```python\nfor i in range(3):\n    await echo(value=i)\n```"


@pytest.mark.asyncio
async def test_resuming_mid_turn_does_not_reset_the_run_budget(monkeypatch):
    """An interrupt mid-turn (tool approval / HITL) must not re-enter prepare.

    prepare is what zeroes ``tool_calls_used_run``, and it runs on START ->
    prepare only. If a resume re-entered the graph from the top instead of the
    interrupted node, every approval would silently hand the task a fresh
    budget — a runaway would just need to trip one approval per 256 calls, and
    nothing would look wrong until the bill arrived.

    Interrupting before ``sandbox`` reproduces the resume path without needing
    the policy engine: the assertion is on the budget, which is what the
    approval flow would put at risk.
    """
    _set_caps(monkeypatch, block=0, run=5, thread=0)

    model = _ScriptedModel([LOOP_3, LOOP_3, FINAL_ANSWER])
    graph = create_cuga_lite_graph(
        model=model, tool_provider=_tool_provider(), apps_list=[], thread_id="budget-resume"
    ).compile(checkpointer=MemorySaver(), interrupt_before=["sandbox"])
    config = {"configurable": {"thread_id": "budget-resume", "enable_todos": False}}

    await graph.ainvoke(CugaLiteState(chat_messages=[HumanMessage(content="call echo")]), config=config)
    assert CALLS_MADE == [], "interrupt should land before the block executes"

    await graph.ainvoke(None, config=config)  # resume 1 -> first block runs
    assert len(CALLS_MADE) == 3

    await graph.ainvoke(None, config=config)  # resume 2 -> second block runs
    state = graph.get_state(config).values

    assert len(CALLS_MADE) == 5, (
        f"{len(CALLS_MADE)} calls against run cap 5 — resuming re-ran prepare and reset the budget"
    )
    assert state["tool_calls_used_run"] == 5
