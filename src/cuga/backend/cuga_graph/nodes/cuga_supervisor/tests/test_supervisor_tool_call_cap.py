"""The per-run tool-call cap must hold on the supervisor graph too.

The cap (``advanced_features.max_tool_calls_per_run``) was enforced only inside the
CugaLite sandbox. The supervisor graph runs its own executor with its own tool
context, so delegation, skill, runtime and provider tools escaped it entirely —
for two independent reasons, one per test below.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import counted_tool_call
from cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.execute_agent_tool import (
    create_execute_agent_tool_node,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_budget_context():
    """Seeding leaves a live budget in the contextvar; clear it so ordering
    between files can never make an unrelated test inherit one."""
    from cuga.backend.cuga_graph.nodes.cuga_lite.tracking import tracker as tracker_module

    yield
    tracker_module._tool_call_budget_context.set(None)


def _set_cap(monkeypatch, cap):
    """Pin the cap via cuga.config.settings (read inside enforce_call_budget),
    immune to dynaconf state left behind by other tests in the full suite."""
    monkeypatch.setattr(
        "cuga.config.settings",
        SimpleNamespace(advanced_features=SimpleNamespace(max_tool_calls_per_run=cap)),
    )


def _skip_policy(monkeypatch):
    """Policy enactment is orthogonal to the cap; short-circuit its denial hook."""
    monkeypatch.setattr(
        "cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.execute_agent_tool"
        ".ToolApprovalHandler.handle_denial",
        staticmethod(lambda adapter, state: None),
    )


def _make_adapter(tools_context):
    adapter = MagicMock()
    adapter._agent_tools_context = tools_context
    adapter.get_variable_manager.return_value = None
    return adapter


def _make_state(script, used=0):
    from cuga.backend.cuga_graph.state.agent_state import VariablesManager

    return SimpleNamespace(
        variables_manager=VariablesManager(),
        script=script,
        thread_id="t-cap",
        step_count=0,
        tool_calls_used_run=used,
        task_todos=None,
        supervisor_variables={},
        selected_agents=[],
        agent_results={},
        agent_variables={},
        agent_chat_messages={},
        # Read by _delegation_state_update, which every exit from the node calls.
        supervisor_metadata={},
        metrics={},
    )


@pytest.mark.asyncio
async def test_supervisor_executor_seeds_the_budget(monkeypatch):
    """Without seed_call_budget in execute_agent_tool the budget context is
    None, so enforce_call_budget no-ops and the cap never fires — a runaway
    delegation loop runs unbounded."""
    _set_cap(monkeypatch, 3)
    _skip_policy(monkeypatch)

    seen = {"n": 0}

    async def delegate_to_researcher(task: str) -> str:
        seen["n"] += 1
        return "ok"

    node = create_execute_agent_tool_node(
        _make_adapter({"delegate_to_researcher": counted_tool_call(delegate_to_researcher)})
    )
    # The adapter's append hook is exercised elsewhere; stub it to isolate the cap.
    monkeypatch.setattr(
        "cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.execute_agent_tool.core_append",
        lambda adapter, state, msgs: ([], None),
    )

    state = _make_state("for _ in range(20):\n    await delegate_to_researcher('t')\n")
    update = await node(state)

    assert seen["n"] == 3, f"cap=3 but the supervisor made {seen['n']} delegation calls"
    # And the count is persisted so the cap spans the run, not one block.
    assert update["tool_calls_used_run"] == 3


@pytest.mark.asyncio
async def test_supervisor_budget_carries_across_steps(monkeypatch):
    """tool_calls_used_run seeds the next step, making the cap per run."""
    _set_cap(monkeypatch, 3)
    _skip_policy(monkeypatch)
    monkeypatch.setattr(
        "cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.execute_agent_tool.core_append",
        lambda adapter, state, msgs: ([], None),
    )

    seen = {"n": 0}

    async def delegate_to_researcher(task: str) -> str:
        seen["n"] += 1
        return "ok"

    node = create_execute_agent_tool_node(
        _make_adapter({"delegate_to_researcher": counted_tool_call(delegate_to_researcher)})
    )
    # Two calls already spent by an earlier step.
    state = _make_state("for _ in range(20):\n    await delegate_to_researcher('t')\n", used=2)
    await node(state)

    assert seen["n"] == 1, "the second step must only get the 1 call left in the run budget"


@pytest.mark.asyncio
async def test_unwrapped_tool_is_still_capped(monkeypatch):
    """The guarantee, stated behaviourally rather than structurally.

    The cap is enforced where generated code gets its namespace, not at each
    tool's registration site, so a tool registered with no wrapper at all is
    still charged. That is what makes the budget hold for tool kinds nobody
    thought to wrap — SDK/MCP providers, skills, runtime fs/shell, delegation —
    and it is the property that must survive future refactors.
    """
    _set_cap(monkeypatch, 3)
    _skip_policy(monkeypatch)
    monkeypatch.setattr(
        "cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.execute_agent_tool.core_append",
        lambda adapter, state, msgs: ([], None),
    )

    seen = {"n": 0}

    async def brand_new_tool(task: str) -> str:
        seen["n"] += 1
        return "ok"

    # Registered bare — exactly how a newly added tool would arrive.
    node = create_execute_agent_tool_node(_make_adapter({"brand_new_tool": brand_new_tool}))
    state = _make_state("for i in range(20):\n    await brand_new_tool('t')\n")
    await node(state)

    assert seen["n"] == 3, f"unwrapped tool escaped the cap: {seen['n']} calls against cap 3"


def test_variables_are_not_charged_as_tool_calls(monkeypatch):
    """Only coroutine functions are charged. Plain callables in the namespace are
    variables carried over from earlier blocks; charging them would both bill
    them as tool calls and turn them into coroutines, breaking their call sites.
    """
    import asyncio

    _set_cap(monkeypatch, 1)  # would trip immediately if these were charged
    _skip_policy(monkeypatch)
    monkeypatch.setattr(
        "cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.execute_agent_tool.core_append",
        lambda adapter, state, msgs: ([], None),
    )

    seen = {"n": 0}

    def helper(x):  # a sync function the model defined in an earlier block
        seen["n"] += 1
        return x * 2

    node = create_execute_agent_tool_node(_make_adapter({"helper": helper}))
    state = _make_state("doubled = [helper(i) for i in range(5)]\n")
    update = asyncio.run(node(state))

    assert seen["n"] == 5, "a plain callable was charged against the tool budget"
    assert update.get("error") is None, f"wrapping broke a sync call site: {update.get('error')}"
    assert update["tool_calls_used_run"] == 0


def test_default_cap_is_256():
    """The shipped default and the in-code fallback must agree; a mismatch means
    a deployment without the settings key silently gets a different budget."""
    import tomllib

    from cuga.backend.cuga_graph.nodes.cuga_lite.tracking import tracker as tracker_module

    settings_path = Path(tracker_module.__file__).resolve().parents[5] / "settings.toml"
    config = tomllib.loads(settings_path.read_text())
    assert config["advanced_features"]["max_tool_calls_per_run"] == 256

    source = Path(tracker_module.__file__).read_text()
    assert '"max_tool_calls_per_run", 256' in source, "in-code fallback must match settings.toml"


@pytest.mark.asyncio
async def test_supervisor_persists_the_thread_counter_and_exhausted_flag(monkeypatch):
    """The supervisor must write back both new fields, not just tool_calls_used_run.

    tool_calls_used_thread is the conversation ceiling (prepare never resets it)
    and tool_budget_exhausted is what ends the turn in call_model. A node that
    seeds them but forgets to persist them leaves the ceiling at 0 forever and
    the turn thrashing until the step limit.
    """
    _set_cap(monkeypatch, 3)
    _skip_policy(monkeypatch)
    monkeypatch.setattr(
        "cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.execute_agent_tool.core_append",
        lambda adapter, state, msgs: ([], None),
    )

    async def delegate_to_researcher(task: str) -> str:
        return "ok"

    node = create_execute_agent_tool_node(
        _make_adapter({"delegate_to_researcher": counted_tool_call(delegate_to_researcher)})
    )
    state = _make_state("for _ in range(20):\n    await delegate_to_researcher('t')\n")
    state.tool_calls_used_thread = 40
    update = await node(state)

    assert update["tool_calls_used_run"] == 3
    assert update["tool_calls_used_thread"] == 43, "thread count must carry over from earlier turns"
    assert update["tool_budget_exhausted"] is True, "a spent run budget must end the turn"


@pytest.mark.asyncio
async def test_supervisor_thread_ceiling_bounds_what_the_task_cap_cannot(monkeypatch):
    """With the task cap generous and the thread already near its ceiling, the
    conversation ceiling is what stops the loop."""
    monkeypatch.setattr(
        "cuga.config.settings",
        SimpleNamespace(
            advanced_features=SimpleNamespace(
                max_tool_calls_per_run=1000, max_tool_calls_per_thread=50, max_tool_calls_per_block=0
            )
        ),
    )
    _skip_policy(monkeypatch)
    monkeypatch.setattr(
        "cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.execute_agent_tool.core_append",
        lambda adapter, state, msgs: ([], None),
    )

    seen = {"n": 0}

    async def delegate_to_researcher(task: str) -> str:
        seen["n"] += 1
        return "ok"

    node = create_execute_agent_tool_node(
        _make_adapter({"delegate_to_researcher": counted_tool_call(delegate_to_researcher)})
    )
    state = _make_state("for _ in range(100):\n    await delegate_to_researcher('t')\n")
    state.tool_calls_used_thread = 48
    update = await node(state)

    assert seen["n"] == 2, f"thread ceiling breached: {seen['n']} calls with 2 left in the conversation"
    assert update["tool_budget_exhausted"] is True


@pytest.mark.asyncio
async def test_step_limit_exit_still_persists_the_spent_budget(monkeypatch):
    """Every exit from the execute node runs AFTER the block, so every one of
    them can be leaving spent budget behind.

    The step-limit path used to return a Command with no budget fields at all.
    An absent key is not a zero — LangGraph keeps the checkpoint's pre-block
    value, so those calls vanish from the conversation ceiling and keep_highest
    has nothing to work with.

    Asserted against what the tool actually executed rather than a literal, so
    the test pins the accounting rather than a property of the harness.
    """
    from langchain_core.messages import AIMessage

    _set_cap(monkeypatch, 100)
    _skip_policy(monkeypatch)
    # Force the step-limit branch: append returns an error message.
    monkeypatch.setattr(
        "cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.execute_agent_tool.core_append",
        lambda adapter, state, msgs: ([], AIMessage(content="Maximum step limit (50) reached.")),
    )

    executed = {"n": 0}

    async def delegate_to_researcher(task: str) -> str:
        executed["n"] += 1
        return "ok"

    node = create_execute_agent_tool_node(
        _make_adapter({"delegate_to_researcher": counted_tool_call(delegate_to_researcher)})
    )
    state = _make_state("for _ in range(4):\n    await delegate_to_researcher('t')\n")
    state.tool_calls_used_thread = 30
    result = await node(state)
    update = result.update if hasattr(result, "update") else result

    assert executed["n"] > 0, "nothing ran; the test would pass vacuously"
    assert update.get("tool_calls_used_run") == executed["n"], "spent run budget lost on the step-limit exit"
    assert update.get("tool_calls_used_thread") == 30 + executed["n"], (
        "spent thread budget lost on the step-limit exit"
    )
