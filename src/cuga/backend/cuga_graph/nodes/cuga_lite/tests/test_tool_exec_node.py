"""``tool_exec`` node: every ``tool_call`` gets a ``ToolMessage``, inside the guard envelope.

The node is the function-calling counterpart of the sandbox, so the assertions
mirror what #560 pinned for the sandbox: budgets and the tracker apply to every
call even though the callables in ``_tools_context`` are bare, and every exit
carries the budget fields. On top of that, the FC-specific contract: ids are
preserved, one failing call never aborts its siblings, provider-side invalid
calls are answered, and step discipline defers everything but the first call.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, List, Optional
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.tool_exec_node import (
    DEFERRED_CALL_MESSAGE,
    create_tool_exec_node,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.tracking import tracker as tracker_module
from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import (
    ToolCallTracker,
    make_recording_awaitable,
)

pytestmark = pytest.mark.unit


class _Adapter:
    messages_key = "chat_messages"
    metadata_key = "cuga_lite_metadata"
    execute_node_name = "sandbox"
    sender_name = "CugaLite"

    def __init__(self, tools: dict, max_steps: int = 50):
        self._tools_context = tools
        self._tracker = MagicMock()
        self._model = None
        self._max_steps = max_steps

    def get_messages(self, state: Any) -> List[BaseMessage]:
        return list(state.chat_messages or [])

    def resolve_max_steps(self, state: Any, override: Optional[int]) -> int:
        return override if override is not None else self._max_steps


def _state(last: AIMessage, step_count: int = 0):
    return SimpleNamespace(
        chat_messages=[HumanMessage(content="add 1 and 2"), last],
        step_count=step_count,
        tool_calls=[],
        tool_calls_used_run=0,
        tool_calls_used_thread=0,
    )


def _call(name: str, args: dict, call_id: str) -> dict:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def _update(result) -> dict:
    """Normal exits are ``Command(goto="call_model")``; error exits ``Command(goto=END)``."""
    assert isinstance(result, Command), type(result)
    return result.update


def _tool_messages(result) -> List[ToolMessage]:
    return [m for m in _update(result)["chat_messages"] if isinstance(m, ToolMessage)]


@pytest.fixture(autouse=True)
def _reset_budget_contexts():
    yield
    tracker_module._tool_call_budget_context.set(None)
    tracker_module._thread_tool_call_budget_context.set(None)
    tracker_module._block_tool_call_budget_context.set(None)
    tracker_module._block_tool_call_cap_override_context.set(None)


def _caps(monkeypatch, *, block=0, run=0, thread=0):
    from cuga.config import settings

    monkeypatch.setattr(settings.advanced_features, "max_tool_calls_per_block", block, raising=False)
    monkeypatch.setattr(settings.advanced_features, "max_tool_calls_per_run", run, raising=False)
    monkeypatch.setattr(settings.advanced_features, "max_tool_calls_per_thread", thread, raising=False)


# ── happy path ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_every_call_runs_and_ids_are_preserved(monkeypatch):
    _caps(monkeypatch)

    async def add(a: int, b: int) -> int:
        return a + b

    def greet(name: str) -> str:  # sync tools are made awaitable, like the sandbox does
        return f"hi {name}"

    node = create_tool_exec_node(_Adapter({"add": add, "greet": greet}))
    last = AIMessage(
        content="", tool_calls=[_call("add", {"a": 1, "b": 2}, "c1"), _call("greet", {"name": "bob"}, "c2")]
    )

    result = await node(_state(last), config={"configurable": {}})

    msgs = _tool_messages(result)
    assert [(m.tool_call_id, m.name, m.content, m.status) for m in msgs] == [
        ("c1", "add", "3", "success"),
        ("c2", "greet", "hi bob", "success"),
    ]
    assert _update(result)["chat_messages"][:2] == _state(last).chat_messages, (
        "history is appended to, never rewritten"
    )
    assert _update(result)["script"] is None
    assert _update(result)["step_count"] == 1
    assert _update(result)["tool_calls_used_run"] == 2 and _update(result)["tool_budget_exhausted"] is False


@pytest.mark.asyncio
async def test_raw_provider_shaped_calls_with_json_string_args_are_decoded(monkeypatch):
    """LangChain parses ``args`` into a dict before an AIMessage is built, so a
    JSON-string payload only arrives in the raw provider shape (``function.arguments``).
    ``model_construct`` bypasses validation to put exactly that on the message."""
    _caps(monkeypatch)
    seen = []

    async def add(a: int, b: int) -> int:
        seen.append((a, b))
        return a + b

    node = create_tool_exec_node(_Adapter({"add": add}))
    last = AIMessage.model_construct(
        content="",
        tool_calls=[
            {"id": "c1", "function": {"name": "add", "arguments": '{"a": 5, "b": 6}'}},
            {"id": "c2", "function": {"name": "add", "arguments": "{not json"}},
        ],
        invalid_tool_calls=[],
        additional_kwargs={},
        response_metadata={},
    )

    result = await node(_state(last), config={"configurable": {}})

    m1, m2 = _tool_messages(result)
    assert (m1.tool_call_id, m1.content, m1.status) == ("c1", "11", "success") and seen == [(5, 6)]
    assert m2.tool_call_id == "c2" and m2.status == "error" and "valid JSON" in m2.content


# ── every failure is a ToolMessage, never an exception ───────────────────────


@pytest.mark.asyncio
async def test_unknown_tool_lists_the_known_names(monkeypatch):
    _caps(monkeypatch)

    async def add(a: int, b: int) -> int:
        return a + b

    node = create_tool_exec_node(_Adapter({"add": add, "_internal": add}))
    result = await node(_state(AIMessage(content="", tool_calls=[_call("subtract", {}, "c1")])), config=None)

    (m,) = _tool_messages(result)
    assert m.status == "error" and m.tool_call_id == "c1"
    assert "Unknown tool 'subtract'" in m.content and "add" in m.content
    assert "_internal" not in m.content, "internal injections are not offered to the model"


@pytest.mark.asyncio
async def test_a_failing_call_does_not_abort_its_siblings(monkeypatch):
    _caps(monkeypatch)

    async def boom(x: int) -> int:
        raise ValueError("nope")

    async def add(a: int, b: int) -> int:
        return a + b

    node = create_tool_exec_node(_Adapter({"boom": boom, "add": add}))
    last = AIMessage(
        content="",
        tool_calls=[
            _call("boom", {"x": 1}, "c1"),
            _call("add", {"wrong": 1}, "c2"),
            _call("add", {"a": 2, "b": 2}, "c3"),
        ],
    )

    result = await node(_state(last), config=None)

    m1, m2, m3 = _tool_messages(result)
    assert m1.status == "error" and "nope" in m1.content
    assert m2.status == "error" and "rejected these arguments" in m2.content
    assert (m3.content, m3.status) == ("4", "success")


@pytest.mark.asyncio
async def test_timeout_is_an_error_toolmessage(monkeypatch):
    _caps(monkeypatch)
    from cuga.config import settings

    monkeypatch.setattr(settings.advanced_features, "sandbox_execution_timeout", 0.05, raising=False)

    async def slow() -> str:
        await asyncio.Event().wait()  # never set: immune to any asyncio.sleep patch elsewhere in the suite
        return "late"

    node = create_tool_exec_node(_Adapter({"slow": slow}))
    result = await node(_state(AIMessage(content="", tool_calls=[_call("slow", {}, "c1")])), config=None)

    (m,) = _tool_messages(result)
    assert m.status == "error" and "timed out" in m.content and m.tool_call_id == "c1"


@pytest.mark.asyncio
async def test_provider_side_invalid_tool_calls_are_answered(monkeypatch):
    _caps(monkeypatch)

    async def add(a: int, b: int) -> int:
        return a + b

    node = create_tool_exec_node(_Adapter({"add": add}))
    last = AIMessage(
        content="",
        tool_calls=[_call("add", {"a": 1, "b": 1}, "c1")],
        invalid_tool_calls=[
            {
                "name": "add",
                "args": "{broken",
                "id": "bad1",
                "error": "unterminated",
                "type": "invalid_tool_call",
            }
        ],
    )

    result = await node(_state(last), config=None)

    good, bad = _tool_messages(result)
    assert (good.tool_call_id, good.content) == ("c1", "2")
    assert bad.tool_call_id == "bad1" and bad.status == "error" and "unterminated" in bad.content


# ── guard envelope ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bare_callables_are_still_budget_capped(monkeypatch):
    """The callables in _tools_context carry no budget wrapper — the node must add it."""
    _caps(monkeypatch, run=1)
    calls = []

    async def add(a: int, b: int) -> int:
        calls.append((a, b))
        return a + b

    node = create_tool_exec_node(_Adapter({"add": add}))
    last = AIMessage(
        content="", tool_calls=[_call("add", {"a": 1, "b": 1}, "c1"), _call("add", {"a": 2, "b": 2}, "c2")]
    )

    result = await node(_state(last), config=None)

    m1, m2 = _tool_messages(result)
    assert m1.content == "2" and calls == [(1, 1)], "the second call must be refused, not executed"
    assert m2.status == "error" and "Tool call limit reached" in m2.content
    assert _update(result)["tool_calls_used_run"] == 1, "a refused call never inflates the counter"
    assert _update(result)["tool_budget_exhausted"] is True, (
        "call_model must see the exhaustion and end the turn"
    )


@pytest.mark.asyncio
async def test_tracking_session_wraps_the_calls(monkeypatch):
    """A recorded tool lands in result['tool_calls'] when tracking is requested, and
    the session is closed afterwards — the receipt cannot leak into the next node."""
    _caps(monkeypatch)

    async def add(a: int, b: int) -> int:
        return a + b

    node = create_tool_exec_node(_Adapter({"add": make_recording_awaitable(add, "add", app_name="calc")}))
    last = AIMessage(content="", tool_calls=[_call("add", {"a": 3, "b": 4}, "c1")])

    result = await node(_state(last), config={"configurable": {"track_tool_calls": True}})

    assert [(c["name"], c["app_name"], c["result"]) for c in _update(result)["tool_calls"]] == [
        ("add", "calc", 7)
    ]
    assert ToolCallTracker.is_enabled() is False

    untracked = await node(_state(last), config={"configurable": {}})
    assert _update(untracked)["tool_calls"] == []


# ── step discipline ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_step_discipline_runs_only_the_first_call_and_defers_the_rest(monkeypatch):
    _caps(monkeypatch)
    calls = []

    async def add(a: int, b: int) -> int:
        calls.append((a, b))
        return a + b

    node = create_tool_exec_node(_Adapter({"add": add}))
    last = AIMessage(
        content="",
        tool_calls=[
            _call("add", {"a": 1, "b": 1}, "c1"),
            _call("add", {"a": 2, "b": 2}, "c2"),
            _call("add", {"a": 3, "b": 3}, "c3"),
        ],
    )

    result = await node(
        _state(last), config={"configurable": {"cuga_lite_step_discipline": "one_tool_per_step"}}
    )

    m1, m2, m3 = _tool_messages(result)
    assert calls == [(1, 1)]
    assert (m1.tool_call_id, m1.content, m1.status) == ("c1", "2", "success")
    assert (m2.tool_call_id, m2.content, m2.status) == ("c2", DEFERRED_CALL_MESSAGE, "error")
    assert (m3.tool_call_id, m3.content, m3.status) == ("c3", DEFERRED_CALL_MESSAGE, "error")
    assert _update(result)["tool_calls_used_run"] == 1


@pytest.mark.asyncio
async def test_step_discipline_off_runs_every_call(monkeypatch):
    _caps(monkeypatch)
    calls = []

    async def add(a: int, b: int) -> int:
        calls.append((a, b))
        return a + b

    node = create_tool_exec_node(_Adapter({"add": add}))
    last = AIMessage(
        content="", tool_calls=[_call("add", {"a": 1, "b": 1}, "c1"), _call("add", {"a": 2, "b": 2}, "c2")]
    )

    await node(_state(last), config={"configurable": {"cuga_lite_step_discipline": "off"}})
    assert calls == [(1, 1), (2, 2)]


# ── routing edge cases ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_nothing_executable_still_advances_the_step(monkeypatch):
    """A mis-route into tool_exec must not become an infinite no-op loop."""
    _caps(monkeypatch)
    node = create_tool_exec_node(_Adapter({}))

    result = await node(_state(AIMessage(content="no calls here"), step_count=4), config=None)

    assert result.goto == "call_model"
    assert _update(result)["step_count"] == 5 and _update(result)["script"] is None
    assert "chat_messages" not in _update(result), "no ToolMessage is fabricated"


@pytest.mark.asyncio
async def test_step_limit_ends_the_run_with_an_error_command(monkeypatch):
    _caps(monkeypatch)

    async def add(a: int, b: int) -> int:
        return a + b

    node = create_tool_exec_node(_Adapter({"add": add}, max_steps=3))
    last = AIMessage(content="", tool_calls=[_call("add", {"a": 1, "b": 1}, "c1")])

    result = await node(_state(last, step_count=3), config=None)

    assert isinstance(result, Command)
    assert "Maximum step limit" in result.update["error"]
    assert result.update["execution_complete"] is True
    assert "tool_calls_used_run" in result.update, "budget fields ride every exit"


# ── outer guard + batch output budget ────────────────────────────────────────


@pytest.mark.asyncio
async def test_unexpected_failure_after_the_batch_still_reports_the_budget(monkeypatch):
    """Same contract as the sandbox: whatever breaks after a call ran, the update
    carries the spent budget, or the checkpoint keeps the pre-batch counts."""
    _caps(monkeypatch)
    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter import tool_exec_node as mod

    async def add(a: int, b: int) -> int:
        return a + b

    def boom(*args, **kwargs):
        raise RuntimeError("history store unavailable")

    monkeypatch.setattr(mod, "core_append_with_step_limit", boom)
    node = create_tool_exec_node(_Adapter({"add": add}))
    last = AIMessage(content="", tool_calls=[_call("add", {"a": 1, "b": 1}, "c1")])

    result = await node(_state(last), config=None)

    assert isinstance(result, Command)
    assert "history store unavailable" in result.update["error"]
    assert result.update["tool_calls_used_run"] == 1, "the call that ran must still be counted"
    assert ToolCallTracker.is_enabled() is False


@pytest.mark.asyncio
async def test_one_batch_shares_one_output_limit(monkeypatch):
    """N results share execution_output_max_length, like the N prints of one block."""
    _caps(monkeypatch)
    from cuga.config import settings

    monkeypatch.setattr(settings.advanced_features, "execution_output_max_length", 10, raising=False)

    async def text(n: int) -> str:
        return "x" * n

    node = create_tool_exec_node(_Adapter({"text": text}))
    last = AIMessage(
        content="",
        tool_calls=[
            _call("text", {"n": 8}, "c1"),
            _call("text", {"n": 8}, "c2"),
            _call("text", {"n": 3}, "c3"),
        ],
    )

    result = await node(_state(last), config=None)

    m1, m2, m3 = _tool_messages(result)
    assert m1.content == "x" * 8, "under the limit: untouched"
    assert m2.content.startswith("xx") and "truncated" in m2.content, "only the remaining budget is spent"
    assert "truncated" in m3.content and not m3.content.startswith("x"), "nothing left for the third result"


@pytest.mark.asyncio
async def test_normal_exit_routes_to_call_model_and_error_exit_to_end(monkeypatch):
    """One routing mechanism: no static edge, so a terminal error never schedules
    one more model turn."""
    _caps(monkeypatch)

    async def add(a: int, b: int) -> int:
        return a + b

    node = create_tool_exec_node(_Adapter({"add": add}, max_steps=3))
    last = AIMessage(content="", tool_calls=[_call("add", {"a": 1, "b": 1}, "c1")])

    assert (await node(_state(last), config=None)).goto == "call_model"
    from langgraph.graph import END

    assert (await node(_state(last, step_count=3), config=None)).goto == END


@pytest.mark.asyncio
async def test_step_discipline_defers_by_position_even_when_the_first_call_fails(monkeypatch):
    """An error on the first call is the result the model must read before the next
    call — the second call must not run just because the first one failed."""
    _caps(monkeypatch)
    calls = []

    async def add(a: int, b: int) -> int:
        calls.append((a, b))
        return a + b

    node = create_tool_exec_node(_Adapter({"add": add}))
    last = AIMessage(content="", tool_calls=[_call("nope", {}, "c1"), _call("add", {"a": 2, "b": 2}, "c2")])

    result = await node(
        _state(last), config={"configurable": {"cuga_lite_step_discipline": "one_tool_per_step"}}
    )

    m1, m2 = _tool_messages(result)
    assert m1.status == "error" and "Unknown tool" in m1.content
    assert (m2.tool_call_id, m2.content, m2.status) == ("c2", DEFERRED_CALL_MESSAGE, "error")
    assert calls == []
