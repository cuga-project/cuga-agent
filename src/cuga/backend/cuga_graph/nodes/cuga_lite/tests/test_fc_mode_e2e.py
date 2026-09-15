"""Function-calling mode end to end: the real CugaLite graph, a scripted model.

Node tests pin what each node returns; only a real run proves the loop —
prepare selects the FC prompt, call_model routes native ``tool_calls`` to
``tool_exec``, the ``ToolMessage`` replies are replayed to the model with their
ids, and a text reply ends the run. The scripted model asserts on what it is
sent, so a broken replay fails loudly instead of the test passing by accident.

The last test is the feature-off golden: with no mode configured the graph
behaves exactly as before — dict-serialised CodeAct turns, the sandbox, and a
``tool_exec`` node that is present but never entered.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import MemorySaver

from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.graph_adapter import (
    FC_MODE_VIOLATION_CORRECTION,
    AgentGraphAdapter,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.tool_exec_node import DEFERRED_CALL_MESSAGE
from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph import CugaLiteState, create_cuga_lite_graph
from cuga.backend.cuga_graph.nodes.cuga_lite.tracking import tracker as tracker_module

pytestmark = pytest.mark.unit

CALLS: list = []


class _ScriptedModel:
    """Queued responses; an entry may be a callable that receives the outbound messages."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.invocations = 0
        self.seen: list = []
        self.bound_tool_names: list = []

    def bind_tools(self, tools, **kwargs):
        self.bound_tool_names = [getattr(t, "name", str(t)) for t in tools]
        return self

    async def ainvoke(self, messages, config=None, **kwargs):
        self.invocations += 1
        self.seen.append(list(messages))
        if not self._responses:
            raise AssertionError(
                f"model asked for response #{self.invocations} — the run did not end when it should"
            )
        nxt = self._responses.pop(0)
        return nxt(messages) if callable(nxt) else nxt


def _provider(*tools):
    provider = MagicMock()
    provider.get_all_tools = AsyncMock(return_value=list(tools))
    provider.get_apps = AsyncMock(return_value=[])
    provider.get_tools = AsyncMock(return_value=[])
    provider.app_name = "test_app"
    return provider


def _echo_tool():
    async def echo(value: int) -> int:
        """Echo a value."""
        CALLS.append(("echo", value))
        return value

    return StructuredTool.from_function(coroutine=echo, name="echo", description="Echo a value.")


def _tc(name, args, call_id):
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def _config(thread_id, **extra):
    return {"configurable": {"thread_id": thread_id, "enable_todos": False, **extra}}


def _last_tool_message(messages):
    assert isinstance(messages[-1], ToolMessage), (
        f"expected a ToolMessage last, got {type(messages[-1]).__name__}"
    )
    return messages[-1]


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    from cuga.config import settings

    monkeypatch.setattr(settings.policy, "enabled", False, raising=False)
    CALLS.clear()
    yield
    CALLS.clear()
    tracker_module._tool_call_budget_context.set(None)
    tracker_module._thread_tool_call_budget_context.set(None)
    tracker_module._block_tool_call_budget_context.set(None)
    tracker_module._block_tool_call_cap_override_context.set(None)


def _run(model, config, *tools):
    graph = create_cuga_lite_graph(
        model=model, tool_provider=_provider(*tools), apps_list=[], thread_id="t"
    ).compile(checkpointer=MemorySaver())
    return graph.ainvoke(CugaLiteState(chat_messages=[HumanMessage(content="use the tools")]), config=config)


@pytest.mark.asyncio
async def test_single_hop_tool_call_round_trip():
    model = _ScriptedModel(
        [
            AIMessage(content="", tool_calls=[_tc("echo", {"value": 7}, "call_1")]),
            AIMessage(content="The value is 7."),
        ]
    )

    result = await _run(model, _config("fc-1", cuga_lite_execution_mode="function_calling"), _echo_tool())

    assert CALLS == [("echo", 7)]
    assert result["final_answer"] == "The value is 7."
    assert result["script"] is None and model.invocations == 2
    assert "echo" in model.bound_tool_names, (
        "FC mode must advertise the tools natively (bind mode none -> all)"
    )

    kinds = [type(m).__name__ for m in result["chat_messages"]]
    assert kinds == ["HumanMessage", "AIMessage", "ToolMessage", "AIMessage"], kinds
    assert result["chat_messages"][2].tool_call_id == "call_1" and result["chat_messages"][2].content == "7"

    first, second = model.seen
    assert isinstance(first[0], SystemMessage) and "native function-calling" in first[0].content
    assert _last_tool_message(second).tool_call_id == "call_1", "the ToolMessage must be replayed with its id"
    assert "native function-calling" in result["prepared_prompt"]
    assert "```python" not in result["prepared_prompt"], "the CodeAct prompt must not be rendered in FC mode"


@pytest.mark.asyncio
async def test_three_hop_chain_each_hop_depends_on_the_previous_result():
    async def lookup_id(name: str) -> int:
        """Employee id by name."""
        CALLS.append(("lookup_id", name))
        return 42

    async def lookup_manager(employee_id: int) -> str:
        """Manager id for an employee."""
        CALLS.append(("lookup_manager", employee_id))
        return "M-42"

    async def lookup_email(manager: str) -> str:
        """Email for a manager id."""
        CALLS.append(("lookup_email", manager))
        return "m42@example.com"

    tools = [
        StructuredTool.from_function(coroutine=lookup_id, name="lookup_id", description="id by name"),
        StructuredTool.from_function(coroutine=lookup_manager, name="lookup_manager", description="manager"),
        StructuredTool.from_function(coroutine=lookup_email, name="lookup_email", description="email"),
    ]

    def hop2(messages):
        assert _last_tool_message(messages).content == "42"
        return AIMessage(content="", tool_calls=[_tc("lookup_manager", {"employee_id": 42}, "c2")])

    def hop3(messages):
        assert _last_tool_message(messages).content == "M-42"
        return AIMessage(content="", tool_calls=[_tc("lookup_email", {"manager": "M-42"}, "c3")])

    def final(messages):
        assert _last_tool_message(messages).content == "m42@example.com"
        return AIMessage(content="Alice's manager can be reached at m42@example.com.")

    model = _ScriptedModel(
        [AIMessage(content="", tool_calls=[_tc("lookup_id", {"name": "alice"}, "c1")]), hop2, hop3, final]
    )

    result = await _run(model, _config("fc-3", cuga_lite_execution_mode="function_calling"), *tools)

    assert CALLS == [("lookup_id", "alice"), ("lookup_manager", 42), ("lookup_email", "M-42")]
    assert result["final_answer"].endswith("m42@example.com.")
    assert [m.tool_call_id for m in result["chat_messages"] if isinstance(m, ToolMessage)] == [
        "c1",
        "c2",
        "c3",
    ]
    assert model.invocations == 4


@pytest.mark.asyncio
async def test_step_discipline_in_fc_mode_runs_one_call_per_turn():
    model = _ScriptedModel(
        [
            AIMessage(
                content="", tool_calls=[_tc("echo", {"value": 1}, "c1"), _tc("echo", {"value": 2}, "c2")]
            ),
            AIMessage(content="", tool_calls=[_tc("echo", {"value": 2}, "c3")]),
            AIMessage(content="1 and 2."),
        ]
    )
    config = _config(
        "fc-sd", cuga_lite_execution_mode="function_calling", cuga_lite_step_discipline="one_tool_per_step"
    )

    result = await _run(model, config, _echo_tool())

    assert CALLS == [("echo", 1), ("echo", 2)], "the second call of turn 1 is deferred, then re-issued"
    tool_msgs = [m for m in result["chat_messages"] if isinstance(m, ToolMessage)]
    assert [(m.tool_call_id, m.content) for m in tool_msgs][:2] == [
        ("c1", "1"),
        ("c2", DEFERRED_CALL_MESSAGE),
    ]
    assert tool_msgs[2].tool_call_id == "c3" and tool_msgs[2].content == "2"
    assert "exactly ONE tool call" in result["prepared_prompt"]
    assert result["final_answer"] == "1 and 2."


@pytest.mark.asyncio
async def test_code_block_in_fc_mode_is_corrected_not_executed():
    model = _ScriptedModel(
        [AIMessage(content="```python\nawait echo(value=9)\n```"), AIMessage(content="Understood: done.")]
    )

    result = await _run(model, _config("fc-v", cuga_lite_execution_mode="function_calling"), _echo_tool())

    assert CALLS == [], "code must never run in function-calling mode"
    assert any(
        isinstance(m, HumanMessage) and m.content == FC_MODE_VIOLATION_CORRECTION
        for m in result["chat_messages"]
    )
    assert result["final_answer"] == "Understood: done." and model.invocations == 2


@pytest.mark.asyncio
async def test_feature_off_is_the_old_codeact_run_and_tool_exec_is_never_entered(monkeypatch):
    async def forbidden(state, config=None):
        raise AssertionError("tool_exec entered on a CodeAct run")

    monkeypatch.setattr(AgentGraphAdapter, "build_tool_exec_node", lambda self: forbidden)
    model = _ScriptedModel(
        [
            AIMessage(content="```python\nr = await echo(value=3)\nprint(r)\n```"),
            AIMessage(content="It printed 3."),
        ]
    )

    graph = create_cuga_lite_graph(
        model=model, tool_provider=_provider(_echo_tool()), apps_list=[], thread_id="t"
    )
    assert "tool_exec" in graph.nodes, "the node is wired (dormant) so the mode can switch per invoke"
    result = await graph.compile(checkpointer=MemorySaver()).ainvoke(
        CugaLiteState(chat_messages=[HumanMessage(content="use the tools")]), config=_config("codeact-golden")
    )

    assert CALLS == [("echo", 3)]
    assert result["final_answer"] == "It printed 3."
    kinds = [type(m).__name__ for m in result["chat_messages"]]
    assert kinds == ["HumanMessage", "AIMessage", "HumanMessage", "AIMessage"], kinds
    assert all(isinstance(m, dict) for m in model.seen[0]), (
        "CodeAct still serialises turns through the dict path"
    )
    assert "```python" in result["prepared_prompt"], "the CodeAct prompt is unchanged"
