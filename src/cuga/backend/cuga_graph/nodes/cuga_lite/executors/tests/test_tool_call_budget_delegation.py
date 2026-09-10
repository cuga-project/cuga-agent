"""Budget behaviour across agent delegation, spawn, and concurrency.

Review found the budget's central guarantee failing on a product path: the
re-entrancy guard, which exists so a registry-backed tool is not charged twice,
also made every tool a *child graph* ran free — and the child's own
``seed_call_budget`` destroyed the caller's counters on the way through.

The rule these tests encode: a ``counted_tool_call`` wrapper is always its own
logical call, and a delegation tree charges one ceiling — the caller's.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.tracking import tracker as tracker_module
from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import (
    ToolCallTracker,
    counted_tool_call,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset():
    yield
    tracker_module._tool_call_budget_context.set(None)
    tracker_module._thread_tool_call_budget_context.set(None)
    tracker_module._block_tool_call_budget_context.set(None)
    tracker_module._counting_tool_call_context.set(False)


def _set_caps(monkeypatch, *, block=0, run=0, thread=0):
    monkeypatch.setattr(
        "cuga.config.settings",
        SimpleNamespace(
            advanced_features=SimpleNamespace(
                max_tool_calls_per_block=block,
                max_tool_calls_per_run=run,
                max_tool_calls_per_thread=thread,
            )
        ),
    )


def _counting_tool(counter, key="n"):
    async def _tool(**_kwargs):
        counter[key] += 1

    return counted_tool_call(_tool)


# ── Blockers 1 and 2 ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delegated_child_is_bounded_by_the_callers_ceiling(monkeypatch):
    """Sync delegation (``delegate_to_*`` / sync ``spawn_agent``) runs the child
    graph on the caller's Task. The child must not get a fresh budget."""
    _set_caps(monkeypatch, run=5)
    seen = {"n": 0}
    child = _counting_tool(seen)

    async def delegate_to_researcher(task: str):
        ToolCallTracker.seed_call_budget(0, 0)  # the child sandbox seeds too
        for _ in range(50):
            try:
                await child()
            except RuntimeError:
                break
        return "ok"

    ToolCallTracker.seed_call_budget(2, 2)
    await counted_tool_call(delegate_to_researcher)(task="t")

    # 2 already spent + 1 for the delegation call itself leaves 2 for the child.
    assert seen["n"] == 2, f"child made {seen['n']} calls against a cap of 5"
    assert ToolCallTracker.get_run_budget_used() == 5


@pytest.mark.asyncio
async def test_delegation_does_not_destroy_the_callers_counters(monkeypatch):
    """``ContextVar.set`` has no token to unwind, so a child seeding its own
    boxes used to leave the caller reading the child's — the parent's count went
    backwards (2 -> 0), which is worse than not counting at all."""
    _set_caps(monkeypatch, run=100)

    async def delegate_to_researcher(task: str):
        ToolCallTracker.seed_call_budget(0, 0)
        return "ok"

    ToolCallTracker.seed_call_budget(7, 9)
    await counted_tool_call(delegate_to_researcher)(task="t")

    assert ToolCallTracker.get_run_budget_used() == 8, "caller's run counter went backwards"
    assert ToolCallTracker.get_thread_budget_used() == 10, "caller's thread counter went backwards"


@pytest.mark.asyncio
async def test_async_spawn_child_is_bounded(monkeypatch):
    """``asyncio.create_task`` copies the context, carrying the nested flag into
    the child. The copy references the same boxes, so the child's calls must both
    be charged and be visible to the parent afterwards."""
    _set_caps(monkeypatch, run=5)
    seen = {"n": 0}
    child = _counting_tool(seen)

    async def _child_graph():
        ToolCallTracker.seed_call_budget(0, 0)
        for _ in range(50):
            try:
                await child()
            except RuntimeError:
                break

    async def spawn_agent(task: str):
        await asyncio.create_task(_child_graph())
        return "ok"

    ToolCallTracker.seed_call_budget(1, 1)
    await counted_tool_call(spawn_agent)(task="t")

    assert seen["n"] == 3, f"spawned child made {seen['n']} calls against a cap of 5"
    assert ToolCallTracker.get_run_budget_used() == 5


@pytest.mark.asyncio
async def test_registry_backed_tool_is_still_charged_once(monkeypatch):
    """The case the nested guard exists for must keep working: a tool whose body
    calls ``call_api`` crosses two enforcement points and is charged once."""
    _set_caps(monkeypatch, run=100)

    async def registry_backed(value: int):
        ToolCallTracker.enforce_call_budget()  # the inner call_api choke point
        return value

    wrapped = counted_tool_call(registry_backed)
    ToolCallTracker.seed_call_budget(0, 0)

    await wrapped(value=1)
    await wrapped(value=2)

    assert ToolCallTracker.get_run_budget_used() == 2, "one logical call charged twice"


# ── Cases review asked to pin (passing, previously untested) ───────────────


@pytest.mark.asyncio
async def test_separate_threads_each_get_a_full_budget(monkeypatch):
    """Two graphs on different thread_ids run as sibling tasks; contextvar
    isolation must keep their budgets independent."""
    _set_caps(monkeypatch, run=3)
    seen = {"a": 0, "b": 0}

    async def _run(key):
        ToolCallTracker.seed_call_budget(0, 0)
        tool = _counting_tool(seen, key)
        for _ in range(10):
            try:
                await tool()
            except RuntimeError:
                break

    await asyncio.gather(_run("a"), _run("b"))
    assert (seen["a"], seen["b"]) == (3, 3), "budgets bled between concurrent threads"


@pytest.mark.asyncio
async def test_wait_for_child_increments_the_parent_box(monkeypatch):
    """``asyncio.wait_for`` runs the block in a new Task whose context is a copy;
    the boxes are mutable so increments must still reach the seeding context.
    This is why the counters are ``[int]`` boxes rather than plain ints."""
    _set_caps(monkeypatch, run=100)
    seen = {"n": 0}
    tool = _counting_tool(seen)

    async def block():
        for _ in range(4):
            await tool()

    ToolCallTracker.seed_call_budget(0, 0)
    await asyncio.wait_for(block(), timeout=5)

    assert ToolCallTracker.get_run_budget_used() == 4, "increments were lost across the Task copy"


@pytest.mark.asyncio
async def test_gathered_tools_share_one_run_budget(monkeypatch):
    """Concurrent tools inside a single block share the turn's budget rather
    than each getting their own."""
    _set_caps(monkeypatch, run=6)
    seen = {"n": 0}
    tool = _counting_tool(seen)

    async def _try():
        try:
            await tool()
        except RuntimeError:
            pass

    ToolCallTracker.seed_call_budget(0, 0)
    await asyncio.gather(*[_try() for _ in range(20)])

    assert seen["n"] == 6, f"{seen['n']} calls executed against a run cap of 6"


def test_keep_highest_ignores_a_default_zero():
    """The server rebuilds AgentState each turn, so the incoming thread count is
    sometimes the field default 0. The ceiling must not be resettable that way."""
    from cuga.backend.cuga_graph.state.agent_state import keep_highest

    assert keep_highest(100, 0) == 100
    assert keep_highest(0, 100) == 100
    assert keep_highest(None, 5) == 5
    assert keep_highest(7, None) == 7
