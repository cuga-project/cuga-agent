"""Deferred tool loading end to end through the SDK (fc-deferred-tool-search).

Real CugaLite graph, scripted model: turn 1 starts with only ``tool_search``
bound, loads a tool, calls it natively and answers; turn 2 on the same thread
starts with that tool already bound — across the SDK's state round-trip.
"""

from __future__ import annotations

from typing import List

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool

pytestmark = pytest.mark.unit

CALLS: list = []


class _Bound:
    def __init__(self, model, names):
        self._model = model
        self.names = names

    async def ainvoke(self, messages, config=None, **kwargs):
        self._model.bound_per_call.append(self.names)
        return await self._model.ainvoke(messages, config=config, **kwargs)


class _ScriptedModel:
    def __init__(self, responses):
        self._responses = list(responses)
        self.seen: list = []
        self.bound_per_call: List[List[str]] = []

    def bind_tools(self, tools, **kwargs):
        names = [t["function"]["name"] if isinstance(t, dict) else t.name for t in tools]
        return _Bound(self, names)

    async def ainvoke(self, messages, config=None, **kwargs):
        self.seen.append(list(messages))
        if not self._responses:
            raise AssertionError("model asked for more responses than scripted")
        return self._responses.pop(0)


def _tools():
    async def double(value: int) -> int:
        """Double a number."""
        CALLS.append(("double", value))
        return value * 2

    async def negate(value: int) -> int:
        """Negate a number."""
        CALLS.append(("negate", value))
        return -value

    return [
        StructuredTool.from_function(coroutine=double, name="double", description="Double a number."),
        StructuredTool.from_function(coroutine=negate, name="negate", description="Negate a number."),
    ]


def _call(name, args, call_id):
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    from cuga.config import settings

    monkeypatch.setattr(settings.policy, "enabled", False, raising=False)
    monkeypatch.setattr(settings.evolve, "enabled", False, raising=False)
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_tool_search", "on", raising=False)
    CALLS.clear()
    yield
    CALLS.clear()


@pytest.mark.asyncio
async def test_tool_search_loads_then_binds_then_carries_across_turns():
    from cuga.sdk import CugaAgent

    model = _ScriptedModel(
        [
            AIMessage(content="", tool_calls=[_call("tool_search", {"query": "select:double"}, "s1")]),
            AIMessage(content="", tool_calls=[_call("double", {"value": 21}, "c1")]),
            AIMessage(content="The answer is 42."),
            # turn 2: double is already loaded, so the model can call it straight away
            AIMessage(content="", tool_calls=[_call("double", {"value": 5}, "c2")]),
            AIMessage(content="Now 10."),
        ]
    )
    agent = CugaAgent(tools=_tools(), model=model, execution_mode="function_calling")

    turn1 = await agent.invoke("double 21", thread_id="fc-tool-search")
    assert turn1.answer == "The answer is 42."
    assert CALLS == [("double", 21)]

    first, after_search, _ = model.bound_per_call[:3]
    assert "tool_search" in first and "double" not in first and "negate" not in first
    assert "double" in after_search and "negate" not in after_search, "only what was loaded is bound"

    search_reply = next(m for m in model.seen[1] if isinstance(m, ToolMessage) and m.name == "tool_search")
    assert "double(value: int)" in search_reply.content, "the result is a compact signature"

    turn2 = await agent.invoke("double 5", thread_id="fc-tool-search")
    assert turn2.answer == "Now 10."
    assert CALLS == [("double", 21), ("double", 5)]
    assert "double" in model.bound_per_call[3], "loaded tools stay bound on the next turn"


@pytest.mark.asyncio
async def test_prompt_announces_deferred_tools_instead_of_binding_them():
    from cuga.sdk import CugaAgent

    model = _ScriptedModel([AIMessage(content="Nothing to do.")])
    agent = CugaAgent(tools=_tools(), model=model, execution_mode="function_calling")

    await agent.invoke("hello", thread_id="fc-tool-search-prompt")

    system = model.seen[0][0].content
    assert "# FINDING TOOLS" in system
    assert "double, negate" in system or ("double" in system and "negate" in system)
    assert model.bound_per_call[0] == ["tool_search"] or set(model.bound_per_call[0]) >= {"tool_search"}
    assert not {"double", "negate"} & set(model.bound_per_call[0])


@pytest.mark.asyncio
async def test_calling_an_unloaded_tool_with_bad_args_gets_the_select_hint():
    from cuga.sdk import CugaAgent

    model = _ScriptedModel(
        [
            AIMessage(content="", tool_calls=[_call("negate", {"value": "not a number"}, "c1")]),
            AIMessage(content="Gave up."),
        ]
    )
    agent = CugaAgent(tools=_tools(), model=model, execution_mode="function_calling")

    await agent.invoke("negate something", thread_id="fc-tool-search-hint")

    reply = next(m for m in model.seen[1] if isinstance(m, ToolMessage))
    assert reply.status == "error"
    assert "Invalid arguments for negate" in reply.content
    assert 'select:negate' in reply.content
    assert CALLS == [], "an invalid call never runs"


@pytest.mark.asyncio
async def test_answering_after_search_without_a_tool_call_gets_one_correction():
    """M3 hockey #9: the right tool was loaded, the model answered "no access"."""
    from cuga.sdk import CugaAgent

    model = _ScriptedModel(
        [
            AIMessage(content="", tool_calls=[_call("tool_search", {"query": "select:double"}, "s1")]),
            AIMessage(content="I don't have access to that data."),
            AIMessage(content="", tool_calls=[_call("double", {"value": 4}, "c1")]),
            AIMessage(content="It is 8."),
        ]
    )
    agent = CugaAgent(tools=_tools(), model=model, execution_mode="function_calling")

    result = await agent.invoke("double 4", thread_id="fc-search-only")

    assert result.answer == "It is 8."
    assert CALLS == [("double", 4)]
    correction = model.seen[2][-1]
    assert "searched for tools but have not called any of them" in correction.content
    assert "double" in correction.content


@pytest.mark.asyncio
async def test_the_search_only_correction_fires_once_per_turn():
    from cuga.sdk import CugaAgent

    model = _ScriptedModel(
        [
            AIMessage(content="", tool_calls=[_call("tool_search", {"query": "select:double"}, "s1")]),
            AIMessage(content="No access."),
            AIMessage(content="Still no access."),
        ]
    )
    agent = CugaAgent(tools=_tools(), model=model, execution_mode="function_calling")

    result = await agent.invoke("double 4", thread_id="fc-search-only-once")

    assert result.answer == "Still no access."
    assert len(model.seen) == 3, "exactly one corrective turn, then the answer is delivered"
