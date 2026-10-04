"""Planning in function-calling and hybrid mode: ``create_update_todos`` as a native call.

The tool is bookkeeping, like free-code's TodoWrite: it is never deferred by step
discipline and never charged to the tool-call budgets, it does not break a repeated-call
streak, the prompt says when to use it, and the run state reminds the model when an
unfinished plan has not been touched for a while.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List, Optional
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.code_extraction import make_tool_awaitable
from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.todos import (
    TODOS_TOOL_NAME,
    create_update_todos_tool,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.graph_adapter import (
    AgentGraphAdapter,
    _steps_since_todos_update,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.tool_exec_node import create_tool_exec_node
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.repeat_calls import detect_repeat_streak
from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import render_fc_prompt
from cuga.backend.cuga_graph.nodes.cuga_lite.tracking import tracker as tracker_module

pytestmark = pytest.mark.unit


def _call(name: str, args: dict, call_id: str) -> dict:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


_PLAN = {
    "todos": [
        {"text": "find the orders", "status": "completed"},
        {"text": "sum the totals", "status": "in_progress"},
        {"text": "report", "status": "pending"},
    ]
}


class _ExecAdapter:
    messages_key = "chat_messages"
    metadata_key = "cuga_lite_metadata"
    execute_node_name = "sandbox"
    sender_name = "CugaLite"

    def __init__(self, tools: dict):
        self._tools_context = tools
        self._tracker = MagicMock()
        self._model = None

    def get_messages(self, state: Any) -> List[BaseMessage]:
        return list(state.chat_messages or [])

    def resolve_max_steps(self, state: Any, override: Optional[int]) -> int:
        return 50


async def _todos_tools(store: list) -> dict:
    tool = await create_update_todos_tool(todos_store_ref=store)
    # As prepare_node registers it.
    return {TODOS_TOOL_NAME: make_tool_awaitable(tool.coroutine or tool.func)}


@pytest.fixture(autouse=True)
def _no_caps(monkeypatch):
    from cuga.config import settings

    for key in ("max_tool_calls_per_block", "max_tool_calls_per_run", "max_tool_calls_per_thread"):
        monkeypatch.setattr(settings.advanced_features, key, 0, raising=False)
    yield
    tracker_module._tool_call_budget_context.set(None)
    tracker_module._thread_tool_call_budget_context.set(None)
    tracker_module._block_tool_call_budget_context.set(None)
    tracker_module._block_tool_call_cap_override_context.set(None)


# ── tool_exec ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_todos_update_the_plan_and_reply_in_one_line():
    store: list = []
    node = create_tool_exec_node(_ExecAdapter(await _todos_tools(store)))
    last = AIMessage(content="", tool_calls=[_call(TODOS_TOOL_NAME, _PLAN, "t1")])
    state = SimpleNamespace(chat_messages=[HumanMessage(content="q"), last], step_count=0, tool_calls=[])

    result = await node(state, config=None)

    (reply,) = [m for m in result.update["chat_messages"] if isinstance(m, ToolMessage)]
    assert reply.tool_call_id == "t1" and reply.status == "success"
    assert "3 step(s)" in reply.content and "1 completed, 1 in progress, 1 pending" in reply.content
    assert [t["status"] for t in store] == ["completed", "in_progress", "pending"]


@pytest.mark.asyncio
async def test_todos_are_never_deferred_and_never_charged_to_the_budget(monkeypatch):
    from cuga.config import settings

    monkeypatch.setattr(settings.advanced_features, "max_tool_calls_per_run", 1, raising=False)
    ran = []

    async def add(a: int, b: int) -> int:
        ran.append((a, b))
        return a + b

    tools = {**(await _todos_tools([])), "add": add}
    node = create_tool_exec_node(_ExecAdapter(tools))
    last = AIMessage(
        content="",
        tool_calls=[_call(TODOS_TOOL_NAME, _PLAN, "t1"), _call("add", {"a": 1, "b": 2}, "c1")],
    )
    state = SimpleNamespace(chat_messages=[HumanMessage(content="q"), last], step_count=0, tool_calls=[])

    result = await node(state, config={"configurable": {"cuga_lite_step_discipline": "one_tool_per_step"}})

    replies = {m.tool_call_id: m for m in result.update["chat_messages"] if isinstance(m, ToolMessage)}
    assert replies["t1"].status == "success"
    assert replies["c1"].content == "3" and ran == [(1, 2)], "the app call after the plan update still runs"
    assert result.update["tool_calls_used_run"] == 1


@pytest.mark.asyncio
async def test_bad_todo_arguments_are_an_error_reply():
    node = create_tool_exec_node(_ExecAdapter(await _todos_tools([])))
    last = AIMessage(content="", tool_calls=[_call(TODOS_TOOL_NAME, {"todos": "not a list"}, "t1")])
    state = SimpleNamespace(chat_messages=[HumanMessage(content="q"), last], step_count=0, tool_calls=[])

    result = await node(state, config=None)

    (reply,) = [m for m in result.update["chat_messages"] if isinstance(m, ToolMessage)]
    assert reply.status == "error"


# ── repeat streaks ──────────────────────────────────────────────────────────


def test_a_plan_update_between_item_lookups_does_not_hide_the_loop():
    history = [
        AIMessage(content="", tool_calls=[_call("get_order", {"order_id": 1}, "a")]),
        AIMessage(content="", tool_calls=[_call(TODOS_TOOL_NAME, _PLAN, "t1")]),
        AIMessage(content="", tool_calls=[_call("get_order", {"order_id": 2}, "b")]),
        AIMessage(content="", tool_calls=[_call("get_order", {"order_id": 3}, "c")]),
    ]
    streak = detect_repeat_streak(history)
    assert streak is not None and streak.length == 3 and streak.varying_arg == "order_id"


# ── stale-plan reminder ─────────────────────────────────────────────────────


def _ai_call(name: str, call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[_call(name, {}, call_id)])


def test_steps_since_update_counts_native_and_codeact_turns():
    assert (
        _steps_since_todos_update([_ai_call(TODOS_TOOL_NAME, "t"), _ai_call("x", "a"), _ai_call("x", "b")])
        == 2
    )
    assert _steps_since_todos_update([AIMessage(content="await create_update_todos([...])")]) == 0
    assert _steps_since_todos_update([HumanMessage(content="q"), _ai_call("x", "a")]) == 1


def _adapter(todos: list) -> AgentGraphAdapter:
    return AgentGraphAdapter(
        tracker=MagicMock(),
        base_callbacks=[],
        task_todos_ref=todos,
        tools_context_ref={},
        base_tool_provider=None,
    )


def _history(steps_after_update: int) -> list:
    return [HumanMessage(content="q"), _ai_call(TODOS_TOOL_NAME, "t")] + [
        _ai_call("x", f"c{i}") for i in range(steps_after_update)
    ]


def test_reminder_fires_every_n_steps_while_the_plan_is_unfinished():
    adapter = _adapter([{"text": "a", "status": "pending"}])
    cfg = {"cuga_lite_todo_reminder_after": 3}

    assert "last updated" not in adapter.prepare_turn_context(SimpleNamespace(chat_messages=_history(2)), cfg)
    assert "last updated 3 steps ago" in adapter.prepare_turn_context(
        SimpleNamespace(chat_messages=_history(3)), cfg
    )
    assert "last updated" not in adapter.prepare_turn_context(SimpleNamespace(chat_messages=_history(4)), cfg)
    assert "last updated 6 steps ago" in adapter.prepare_turn_context(
        SimpleNamespace(chat_messages=_history(6)), cfg
    )


def test_no_reminder_for_a_finished_plan_or_when_off():
    finished = _adapter([{"text": "a", "status": "completed"}])
    assert "last updated" not in finished.prepare_turn_context(
        SimpleNamespace(chat_messages=_history(3)), {"cuga_lite_todo_reminder_after": 3}
    )
    pending = _adapter([{"text": "a", "status": "pending"}])
    assert "last updated" not in pending.prepare_turn_context(
        SimpleNamespace(chat_messages=_history(3)), {"cuga_lite_todo_reminder_after": 0}
    )


# ── prompt ──────────────────────────────────────────────────────────────────


def test_fc_prompt_explains_planning_only_when_the_tool_is_bound():
    assert "create_update_todos" in render_fc_prompt(todos=True)
    assert "create_update_todos" not in render_fc_prompt(todos=False)
