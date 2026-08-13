"""The per-task tool-call cap must hold on the supervisor graph too.

The cap (``advanced_features.max_tool_calls``) was enforced only inside the
CugaLite sandbox. The supervisor graph runs its own executor with its own tool
context, so delegation, skill, runtime and provider tools escaped it entirely —
for two independent reasons, one per test below.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import counted_tool_call
from cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.execute_agent_tool import (
    create_execute_agent_tool_node,
)

pytestmark = pytest.mark.unit


def _set_cap(monkeypatch, cap):
    """Pin the cap via cuga.config.settings (read inside enforce_call_budget),
    immune to dynaconf state left behind by other tests in the full suite."""
    monkeypatch.setattr(
        "cuga.config.settings",
        SimpleNamespace(advanced_features=SimpleNamespace(max_tool_calls=cap)),
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
    return SimpleNamespace(
        script=script,
        thread_id="t-cap",
        step_count=0,
        tool_calls_used=used,
        task_todos=None,
        supervisor_variables={},
        selected_agents=[],
        agent_results={},
        agent_variables={},
        agent_chat_messages={},
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
    # And the count is persisted so the cap spans the task, not one block.
    assert update["tool_calls_used"] == 3


@pytest.mark.asyncio
async def test_supervisor_budget_carries_across_steps(monkeypatch):
    """tool_calls_used seeds the next step, making the cap per task."""
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

    assert seen["n"] == 1, "the second step must only get the 1 call left in the task budget"


def _tool_context_writes(path: Path):
    """Lines that register a callable into a sandbox tool context."""
    pattern = re.compile(r"^\s*adapter\._(agent_)?tools_context(\[[^\]]+\]\s*=|\.update\()")
    return [
        (i, line.rstrip()) for i, line in enumerate(path.read_text().splitlines(), 1) if pattern.match(line)
    ]


def test_every_tool_registration_is_budget_counted():
    """Guard for the whole class of bug, not just today's instance.

    Every tool the sandbox can call — SDK/MCP provider tools, plain python
    tools, skills, runtime filesystem/shell, agent delegation, todos,
    find_tools — enters via a ``*_tools_context`` write. A write that skips
    ``counted_tool_call`` silently escapes the cap, which is exactly how the
    supervisor path and the lite runtime tools were missed. Any new
    registration site must be wrapped or this fails.
    """
    root = Path(__file__).resolve().parents[2]
    sources = [
        root / "cuga_lite" / "adapter" / "prepare_node.py",
        root / "cuga_supervisor" / "nodes" / "prepare_agents_and_prompt.py",
    ]

    unwrapped = []
    for src in sources:
        assert src.exists(), f"missing {src}"
        lines = src.read_text().splitlines()
        for lineno, line in _tool_context_writes(src):
            # `.update(` and `[name] =` may wrap onto following lines.
            window = "\n".join(lines[lineno - 1 : lineno + 2])
            if "counted_tool_call" in window:
                continue
            # A re-wrap of an entry read back out of the same context (e.g. the
            # knowledge-tool thread_id decorator) keeps the inner counting.
            if "_tools_context.get(" in "\n".join(lines[max(0, lineno - 6) : lineno]):
                continue
            unwrapped.append(f"{src.name}:{lineno}: {line.strip()}")

    assert not unwrapped, "tool registrations that escape max_tool_calls:\n" + "\n".join(unwrapped)


def test_default_cap_is_256():
    """The shipped default and the in-code fallback must agree; a mismatch means
    a deployment without the settings key silently gets a different budget."""
    import tomllib

    from cuga.backend.cuga_graph.nodes.cuga_lite.tracking import tracker as tracker_module

    settings_path = Path(tracker_module.__file__).resolve().parents[5] / "settings.toml"
    config = tomllib.loads(settings_path.read_text())
    assert config["advanced_features"]["max_tool_calls"] == 256

    source = Path(tracker_module.__file__).read_text()
    assert '"max_tool_calls", 256' in source, "in-code fallback must match settings.toml"
