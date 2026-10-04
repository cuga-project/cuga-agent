"""``unfinished_paging`` stop check: a full page was the last one read."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import BaseModel

from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.stop_checks import StopContext
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.execute_python import (
    EXECUTE_PYTHON_NAME,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.harness_messages import harness_message
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.tool_effects import (
    PARTIAL_PAGE_PREFIX,
    unfinished_paging,
)

pytestmark = pytest.mark.unit


class _PagedArgs(BaseModel):
    page_index: int = 0
    page_limit: int = 5
    query: str = ""


_TOOLS = {"show_threads": SimpleNamespace(name="show_threads", args_schema=_PagedArgs)}
_FULL = (
    f'[{{"id": 1}}]\n{PARTIAL_PAGE_PREFIX}this is page_index=0 with 5 items per page, and the page is full.]'
)


def _call(call_id, name, args):
    return AIMessage(
        content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}]
    )


def _ctx(messages, mode="hybrid"):
    return StopContext(answer="1", messages=messages, tools_by_name=_TOOLS, execution_mode=mode)


def test_a_full_last_page_objects_with_the_next_step():
    out = unfinished_paging(
        _ctx(
            [
                HumanMessage(content="how many priority-1 threads?"),
                _call("a", "show_threads", {}),
                ToolMessage(content=_FULL, tool_call_id="a", name="show_threads"),
            ]
        )
    )
    assert out is not None and "page_index=0" in out and "execute_python" in out
    assert "give your answer as it is" in out, "one page may be enough; the model decides"


def test_function_calling_mode_names_the_next_page():
    out = unfinished_paging(
        _ctx(
            [
                HumanMessage(content="q"),
                _call("a", "show_threads", {"page_index": 2}),
                ToolMessage(content=_FULL, tool_call_id="a", name="show_threads"),
            ],
            mode="function_calling",
        )
    )
    assert "page_index=3 and onwards" in out


def test_a_later_page_or_code_that_calls_the_tool_counts_as_continuing():
    base = [HumanMessage(content="q"), _call("a", "show_threads", {"page_limit": 5})]
    full = ToolMessage(content=_FULL, tool_call_id="a", name="show_threads")
    next_page = [
        _call("b", "show_threads", {"page_index": 1, "page_limit": 20}),
        ToolMessage(content="[]", tool_call_id="b", name="show_threads"),
    ]
    code = [
        _call("c", EXECUTE_PYTHON_NAME, {"code": "..."}),
        ToolMessage(
            content="42",
            tool_call_id="c",
            name=EXECUTE_PYTHON_NAME,
            artifact={"code_calls": ["show_threads"]},
        ),
    ]
    assert unfinished_paging(_ctx(base + [full] + next_page)) is None
    assert unfinished_paging(_ctx(base + [full] + code)) is None


def test_short_pages_other_filters_and_earlier_turns_are_ignored():
    short = [
        HumanMessage(content="q"),
        _call("a", "show_threads", {}),
        ToolMessage(content='[{"id": 1}]', tool_call_id="a", name="show_threads"),
    ]
    assert unfinished_paging(_ctx(short)) is None

    other_query = [
        HumanMessage(content="q"),
        _call("a", "show_threads", {"query": "x"}),
        ToolMessage(content=_FULL, tool_call_id="a", name="show_threads"),
        _call("b", "show_threads", {"query": "y", "page_index": 1}),
        ToolMessage(content="[]", tool_call_id="b", name="show_threads"),
    ]
    assert unfinished_paging(_ctx(other_query)) is not None, "a different filter is not the next page"

    previous_turn = [
        HumanMessage(content="old question"),
        _call("a", "show_threads", {}),
        ToolMessage(content=_FULL, tool_call_id="a", name="show_threads"),
        AIMessage(content="old answer"),
        HumanMessage(content="new question"),
        harness_message("a runtime note is not a new turn", "repeat_calls"),
    ]
    assert unfinished_paging(_ctx(previous_turn)) is None
