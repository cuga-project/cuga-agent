"""The three nested tool-call budgets: block, task (turn), thread (conversation).

The design claim these tests defend is that **only the budgets that do not reset
are bounds**. The per-block cap is a fail-fast latency guard — breaching it is
recoverable, so the model reflects and gets a fresh block budget — and on its own
it lets ~cuga_lite_max_steps x max_tool_calls_per_block calls through, which is
roughly the runaway that motivated the cap. It is the task and thread ceilings
that actually bound spend.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.tracking import tracker as tracker_module
from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import (
    BlockToolCallBudgetExceeded,
    RunToolCallBudgetExceeded,
    ThreadToolCallBudgetExceeded,
    ToolCallBudgetExceeded,
    ToolCallTracker,
    thread_budget_exhausted,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_budget_contexts():
    """Contextvars set by a sync test leak into the next one in the same thread;
    clear all three so no test can inherit a live budget."""
    yield
    tracker_module._tool_call_budget_context.set(None)
    tracker_module._thread_tool_call_budget_context.set(None)
    tracker_module._block_tool_call_budget_context.set(None)


def _set_caps(monkeypatch, *, block=0, task=0, thread=0):
    """Pin all three caps via cuga.config.settings (read inside the tracker),
    immune to dynaconf state left behind by other tests in the full suite."""
    monkeypatch.setattr(
        "cuga.config.settings",
        SimpleNamespace(
            advanced_features=SimpleNamespace(
                max_tool_calls_per_block=block,
                max_tool_calls_per_run=task,
                max_tool_calls_per_thread=thread,
            )
        ),
    )


def _spend_one_block(limit=10_000):
    """Model writes a block that loops until something stops it. Returns the
    calls it got through and the budget that fired (None if it just finished)."""
    ToolCallTracker.seed_block_budget()
    made = 0
    for _ in range(limit):
        try:
            ToolCallTracker.enforce_call_budget()
        except ToolCallBudgetExceeded as exc:
            return made, type(exc)
        made += 1
    return made, None


# ── The headline: a resetting cap is not a bound ───────────────────────────


def test_per_block_cap_alone_does_not_bound_the_task(monkeypatch):
    """This is why max_tool_calls_per_block cannot be the only cap.

    Each breach is recoverable, so the model reflects and writes another block
    with a *fresh* block budget. Over cuga_lite_max_steps=70 blocks that is
    70 x 100 = 7,000 calls — the same order as the 7,685-call runaway this cap
    exists to stop. The block guard shortens each stall; it does not bound spend.
    """
    _set_caps(monkeypatch, block=100, task=0, thread=0)  # task/thread disabled
    ToolCallTracker.seed_call_budget(0)

    total = 0
    for _ in range(70):
        made, fired = _spend_one_block()
        assert fired is BlockToolCallBudgetExceeded
        total += made

    assert total == 7000, "a resetting per-block cap let 7,000 calls through — it is not a ceiling"


def test_task_cap_is_what_actually_bounds_the_same_loop(monkeypatch):
    """Identical 70-block retry loop, now with the task ceiling on: it stops at
    max_tool_calls_per_run no matter how many times the model reflects and retries."""
    _set_caps(monkeypatch, block=100, task=256, thread=0)
    ToolCallTracker.seed_call_budget(0)

    total = 0
    for _ in range(70):
        made, _fired = _spend_one_block()
        total += made

    assert total == 256, f"task ceiling breached: {total} calls against max_tool_calls_per_run=256"
    assert ToolCallTracker.get_run_budget_used() == 256


def test_thread_cap_bounds_what_the_task_cap_cannot(monkeypatch):
    """The per-turn counter resets every turn, so a long conversation is
    unbounded without a thread ceiling: 20 turns x 256 = 5,120 calls."""
    _set_caps(monkeypatch, block=0, task=256, thread=1000)

    thread_used = 0
    for _ in range(20):  # 20 user turns; prepare resets the turn counter each time
        ToolCallTracker.seed_call_budget(0, thread_used)
        _spend_one_block(limit=1000)
        thread_used = ToolCallTracker.get_thread_budget_used()

    assert thread_used == 1000, f"conversation ceiling breached: {thread_used} against 1000"


# ── Which budget fired, and is it recoverable ──────────────────────────────


def test_block_breach_leaves_the_task_budget_spendable(monkeypatch):
    """A block breach must be recoverable: the run budget survives it, and the
    next block starts with a fresh block budget."""
    _set_caps(monkeypatch, block=5, task=100, thread=0)
    ToolCallTracker.seed_call_budget(0)

    made, fired = _spend_one_block()
    assert (made, fired) == (5, BlockToolCallBudgetExceeded)
    assert not ToolCallTracker.budget_exhausted(), "a block breach must not end the turn"

    made_again, fired_again = _spend_one_block()
    assert (made_again, fired_again) == (5, BlockToolCallBudgetExceeded)
    assert ToolCallTracker.get_run_budget_used() == 10


def test_block_breach_message_says_it_is_recoverable(monkeypatch):
    """The advice must differ by scope: retry more narrowly for a block breach,
    stop calling tools for a task/thread breach. Same text would mislead."""
    _set_caps(monkeypatch, block=2, task=100, thread=0)
    ToolCallTracker.seed_call_budget(0)

    made, fired = _spend_one_block()  # 2 calls through, 3rd refused
    assert (made, fired) == (2, BlockToolCallBudgetExceeded)

    # Re-raise the same refusal to read its text: it must name the remaining
    # run budget, which is what tells the model retrying is worth it.
    with pytest.raises(BlockToolCallBudgetExceeded, match="run budget still has 98 calls left"):
        ToolCallTracker.enforce_call_budget()


def test_terminal_breaches_are_flagged_exhausted(monkeypatch):
    """budget_exhausted() drives the state flag that ends the turn, so it must
    be true for task and thread breaches and false for a block breach."""
    _set_caps(monkeypatch, block=0, task=3, thread=0)
    ToolCallTracker.seed_call_budget(0)
    _spend_one_block()
    assert ToolCallTracker.budget_exhausted() is True

    _set_caps(monkeypatch, block=0, task=0, thread=3)
    ToolCallTracker.seed_call_budget(0, 0)
    _spend_one_block()
    assert ToolCallTracker.budget_exhausted() is True


def test_widest_budget_wins_the_error(monkeypatch):
    """With every budget spent at once the conversation ceiling must be the one
    reported — telling the model to 'retry more narrowly' would be false advice."""
    _set_caps(monkeypatch, block=5, task=5, thread=5)
    ToolCallTracker.seed_call_budget(5, 5)
    ToolCallTracker.seed_block_budget()

    with pytest.raises(ThreadToolCallBudgetExceeded):
        ToolCallTracker.enforce_call_budget()


def test_task_breach_reported_when_only_thread_has_room(monkeypatch):
    _set_caps(monkeypatch, block=100, task=5, thread=1000)
    ToolCallTracker.seed_call_budget(5, 5)
    ToolCallTracker.seed_block_budget()

    with pytest.raises(RunToolCallBudgetExceeded):
        ToolCallTracker.enforce_call_budget()


# ── Counters stay honest ───────────────────────────────────────────────────


def test_rejected_calls_never_inflate_any_counter(monkeypatch):
    """Checked before counting, at every level — a refused call did not happen."""
    _set_caps(monkeypatch, block=2, task=5, thread=9)
    ToolCallTracker.seed_call_budget(0, 0)
    ToolCallTracker.seed_block_budget()

    for _ in range(2):
        ToolCallTracker.enforce_call_budget()
    for _ in range(3):  # all refused by the block cap
        with pytest.raises(BlockToolCallBudgetExceeded):
            ToolCallTracker.enforce_call_budget()

    assert ToolCallTracker.get_block_budget_used() == 2
    assert ToolCallTracker.get_run_budget_used() == 2
    assert ToolCallTracker.get_thread_budget_used() == 2


def test_seeding_clears_a_stale_block_budget(monkeypatch):
    """A block budget left over from a previous execution must never be
    inherited — it would charge a fresh block for calls it never made."""
    _set_caps(monkeypatch, block=100, task=0, thread=0)
    ToolCallTracker.seed_call_budget(0)
    ToolCallTracker.seed_block_budget()
    ToolCallTracker.enforce_call_budget()
    assert ToolCallTracker.get_block_budget_used() == 1

    ToolCallTracker.seed_call_budget(0)
    assert ToolCallTracker.get_block_budget_used() == 0


# ── Each knob disables independently ───────────────────────────────────────


@pytest.mark.parametrize(
    "caps,expected",
    [
        ({"block": 0, "task": 0, "thread": 0}, 500),  # all disabled
        ({"block": 3, "task": 0, "thread": 0}, 3),
        ({"block": 0, "task": 7, "thread": 0}, 7),
        ({"block": 0, "task": 0, "thread": 11}, 11),
        ({"block": 9, "task": 4, "thread": 0}, 4),  # tightest wins
    ],
    ids=["all-off", "block-only", "task-only", "thread-only", "task-tighter-than-block"],
)
def test_each_cap_disables_at_zero(monkeypatch, caps, expected):
    _set_caps(monkeypatch, **caps)
    ToolCallTracker.seed_call_budget(0, 0)
    made, _fired = _spend_one_block(limit=500)
    assert made == expected


def test_unseeded_context_is_a_no_op(monkeypatch):
    """Callers outside the sandbox loop (non-CugaLite paths) are unaffected."""
    _set_caps(monkeypatch, block=1, task=1, thread=1)
    tracker_module._tool_call_budget_context.set(None)
    for _ in range(5):
        ToolCallTracker.enforce_call_budget()


# ── End to end through the real executor ───────────────────────────────────


@pytest.mark.asyncio
async def test_block_cap_fires_through_the_executor_and_stays_recoverable(monkeypatch):
    """CodeExecutor.eval_with_tools_async is where the block budget is opened,
    so the guard must hold on the real path — and the breach must come back as
    execution output with the run budget intact, ready for the model's retry."""
    from unittest.mock import MagicMock

    from langchain_core.tools import StructuredTool

    from cuga.backend.activity_tracker.tracker import ActivityTracker
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors import CodeExecutor
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common.call_api_helper import CallApiHelper
    from cuga.backend.cuga_graph.state.agent_state import AgentState, VariablesManager

    _set_caps(monkeypatch, block=3, task=100, thread=0)

    async def echo(value: int) -> int:
        """Trivial tool that echoes its argument."""
        return value

    tool = StructuredTool.from_function(coroutine=echo, name="echo", description="Echo a value.")
    monkeypatch.setattr(ActivityTracker(), "tools", {"test_app": [tool]})

    state = MagicMock(spec=AgentState)
    state.variables_manager = VariablesManager()
    ToolCallTracker.seed_call_budget(0)

    code = "for i in range(50):\n    r = await call_api('test_app', 'echo', {'value': i})\n"
    output, _ = await CodeExecutor.eval_with_tools_async(
        code=code,
        _locals={"call_api": CallApiHelper.create_local_call_api_function()},
        state=state,
        mode="local",
    )

    assert "Tool call limit reached for this code block" in output  # recoverable, no raise
    assert ToolCallTracker.get_run_budget_used() == 3, "the block guard must not spend the run budget"
    assert not ToolCallTracker.budget_exhausted(), "a block breach must leave the turn alive"


# ── prepare's contract, at the helper level ────────────────────────────────


def test_thread_budget_exhausted_helper(monkeypatch):
    """prepare reads this from the checkpointed count so a conversation already
    over its ceiling opens the turn exhausted instead of spending a step."""
    _set_caps(monkeypatch, block=0, task=0, thread=100)
    assert thread_budget_exhausted(99) is False
    assert thread_budget_exhausted(100) is True

    _set_caps(monkeypatch, block=0, task=0, thread=0)  # disabled
    assert thread_budget_exhausted(10_000) is False


# ── Defaults ───────────────────────────────────────────────────────────────


def test_shipped_defaults_match_the_in_code_fallbacks():
    """A deployment whose settings predate these keys must get the documented
    numbers, not whatever the getattr fallback happens to say."""
    import tomllib

    settings_path = Path(tracker_module.__file__).resolve().parents[5] / "settings.toml"
    config = tomllib.loads(settings_path.read_text())["advanced_features"]
    source = Path(tracker_module.__file__).read_text()

    for key, expected in (
        ("max_tool_calls_per_block", 100),
        ("max_tool_calls_per_run", 256),
        ("max_tool_calls_per_thread", 2000),
    ):
        assert config[key] == expected, f"settings.toml {key}"
        assert f'"{key}", {expected}' in source, f"in-code fallback for {key} must match settings.toml"
