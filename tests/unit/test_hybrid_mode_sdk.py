"""Hybrid execution mode end to end: native tool calls + an ``execute_python`` tool.

Real CugaLite graph and local sandbox, scripted model.
"""

from __future__ import annotations

from typing import List

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
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
        return _Bound(self, [t["function"]["name"] if isinstance(t, dict) else t.name for t in tools])

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

    async def list_page(page_index: int) -> list:
        """One page of items (two per page, three pages)."""
        CALLS.append(("list_page", page_index))
        pages = [[1, 2], [3, 4], [5]]
        return pages[page_index] if page_index < len(pages) else []

    return [
        StructuredTool.from_function(coroutine=double, name="double", description="Double a number."),
        StructuredTool.from_function(coroutine=list_page, name="list_page", description="One page of items."),
    ]


def _call(name, args, call_id):
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    from cuga.config import settings

    monkeypatch.setattr(settings.policy, "enabled", False, raising=False)
    monkeypatch.setattr(settings.evolve, "enabled", False, raising=False)
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_tool_search", "on", raising=False)
    monkeypatch.setattr(settings.advanced_features, "reflection_enabled", False, raising=False)
    CALLS.clear()
    yield
    CALLS.clear()


def _replies(model, turn):
    return [m for m in model.seen[turn] if isinstance(m, ToolMessage)]


@pytest.mark.asyncio
async def test_execute_python_is_bound_with_tool_search_and_loops_over_pages():
    from cuga.sdk import CugaAgent

    code = (
        "items = []\n"
        "page = 0\n"
        "while True:\n"
        "    batch = await list_page(page_index=page)\n"
        "    if not batch:\n"
        "        break\n"
        "    items.extend(batch)\n"
        "    page += 1\n"
        "print(sum(items))\n"
    )
    model = _ScriptedModel(
        [
            AIMessage(content="", tool_calls=[_call("execute_python", {"code": code}, "x1")]),
            AIMessage(content="The total is 15."),
        ]
    )
    agent = CugaAgent(tools=_tools(), model=model, execution_mode="hybrid")

    result = await agent.invoke("sum all items across pages", thread_id="hybrid-loop")

    assert result.answer == "The total is 15."
    assert {"tool_search", "execute_python"} <= set(model.bound_per_call[0])
    assert not {"double", "list_page"} & set(model.bound_per_call[0]), "domain tools stay deferred"
    assert [c for c in CALLS if c[0] == "list_page"] == [("list_page", i) for i in range(4)]
    (reply,) = _replies(model, 1)
    assert reply.name == "execute_python" and "15" in reply.content


@pytest.mark.asyncio
async def test_native_result_is_saved_as_a_variable_and_reused_in_code():
    from cuga.sdk import CugaAgent

    model = _ScriptedModel(
        [
            AIMessage(content="", tool_calls=[_call("tool_search", {"query": "select:double"}, "s1")]),
            AIMessage(content="", tool_calls=[_call("double", {"value": 21}, "c1")]),
            AIMessage(
                content="", tool_calls=[_call("execute_python", {"code": "print(double_result + 1)"}, "x1")]
            ),
            AIMessage(content="43"),
        ]
    )
    agent = CugaAgent(tools=_tools(), model=model, execution_mode="hybrid")

    result = await agent.invoke("double 21 then add one", thread_id="hybrid-vars")

    assert result.answer == "43"
    native_reply = [m for m in _replies(model, 2) if m.name == "double"][0]
    assert "saved as variable `double_result`" in native_reply.content
    code_reply = [m for m in _replies(model, 3) if m.name == "execute_python"][0]
    assert code_reply.content.strip().endswith("43")


@pytest.mark.asyncio
async def test_fenced_code_in_text_runs_as_execute_python():
    from cuga.sdk import CugaAgent

    model = _ScriptedModel(
        [
            AIMessage(content="```python\nx = await double(value=4)\nprint(x)\n```"),
            AIMessage(content="It is 8."),
        ]
    )
    agent = CugaAgent(tools=_tools(), model=model, execution_mode="hybrid")

    result = await agent.invoke("double 4", thread_id="hybrid-fence")

    assert result.answer == "It is 8."
    assert CALLS == [("double", 4)], "a deferred tool is callable from code without loading it"


@pytest.mark.asyncio
async def test_function_calling_mode_does_not_get_execute_python():
    from cuga.sdk import CugaAgent

    model = _ScriptedModel([AIMessage(content="Nothing to do.")])
    agent = CugaAgent(tools=_tools(), model=model, execution_mode="function_calling")

    await agent.invoke("hello", thread_id="fc-no-code")

    assert "execute_python" not in model.bound_per_call[0]
    assert "execute_python" not in model.seen[0][0].content


@pytest.mark.asyncio
async def test_noop_answer_without_any_write_gets_one_correction():
    from cuga.sdk import CugaAgent

    placed = []

    async def place_order(item: str) -> str:
        """Place an order."""
        placed.append(item)
        return "ordered"

    order_tool = StructuredTool.from_function(
        coroutine=place_order, name="place_order", description="Place an order."
    )
    for target in (order_tool, order_tool.coroutine):
        target._http_method = "POST"
    model = _ScriptedModel(
        [
            AIMessage(
                content="", tool_calls=[_call("tool_search", {"query": "select:place_order,double"}, "s1")]
            ),
            AIMessage(content="", tool_calls=[_call("double", {"value": 1}, "r1")]),  # a read, no write
            AIMessage(content="N/A"),
            AIMessage(content="", tool_calls=[_call("place_order", {"item": "bench"}, "c1")]),
            AIMessage(content="N/A"),
        ]
    )
    agent = CugaAgent(tools=[*_tools(), order_tool], model=model, execution_mode="hybrid")

    result = await agent.invoke("order the bench", thread_id="hybrid-noop")

    assert placed == ["bench"]
    assert result.answer == "N/A", "after a real write, N/A is a legitimate action-task answer"
    corrections = [
        m for m in model.seen[3] if isinstance(m, HumanMessage) and "nothing was changed" in m.content
    ]
    assert len(corrections) == 1
    assert len(model.seen) == 5, "the second N/A (after the write) is delivered, not corrected again"


@pytest.mark.asyncio
async def test_a_full_default_page_carries_the_partial_result_note():
    from cuga.sdk import CugaAgent

    async def list_threads(page_index: int = 0, page_limit: int = 2) -> list:
        """List threads."""
        return [{"id": page_index * 10 + i} for i in range(page_limit)]

    tool = StructuredTool.from_function(
        coroutine=list_threads, name="list_threads", description="List threads."
    )
    model = _ScriptedModel(
        [
            AIMessage(content="", tool_calls=[_call("tool_search", {"query": "select:list_threads"}, "s1")]),
            AIMessage(content="", tool_calls=[_call("list_threads", {}, "c1")]),
            AIMessage(content="2"),
        ]
    )
    agent = CugaAgent(tools=[*_tools(), tool], model=model, execution_mode="hybrid")

    await agent.invoke("how many threads", thread_id="hybrid-page")

    reply = [m for m in model.seen[2] if isinstance(m, ToolMessage) and m.name == "list_threads"][0]
    assert "Partial result" in reply.content and "execute_python" in reply.content
