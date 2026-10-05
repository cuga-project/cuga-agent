"""tool_search budget: a model that keeps searching is told to use a loaded tool, then refused."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List

import pytest
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool

from cuga.backend.cuga_graph.nodes.cuga_lite.adapter import tool_exec_node as te
from cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools.deferred import DeferredToolSurface, search_streak
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.tool_search import TOOL_SEARCH_NAME, ToolSearchResult

pytestmark = pytest.mark.unit


def _search_reply(call_id: str) -> ToolMessage:
    return ToolMessage(content="Loaded 1 tool", tool_call_id=call_id, name=TOOL_SEARCH_NAME)


def test_the_streak_counts_searches_since_the_last_real_call_or_user_message():
    history = [
        HumanMessage(content="q"),
        _search_reply("s1"),
        ToolMessage(content="data", tool_call_id="c1", name="get_players"),
        _search_reply("s2"),
        _search_reply("s3"),
    ]
    assert search_streak(history) == 2
    assert search_streak([*history, HumanMessage(content="next")]) == 0
    assert search_streak([HumanMessage(content="q"), _search_reply("s1"), _search_reply("s2")]) == 2


async def _search(query: str, app_name: Any = None, max_results: Any = None):
    return ToolSearchResult(
        text=f"Loaded 1 tool: {query}_tool", discovered=[f"{query}_tool"], stage="keyword"
    )


async def _get(**kwargs):
    return {"ok": True}


def _adapter():
    search_tool = StructuredTool.from_function(coroutine=_search, name=TOOL_SEARCH_NAME, description="d")
    return SimpleNamespace(
        _tools_context={TOOL_SEARCH_NAME: _search, "get": _get}, _tools_context_ref={}
    ), DeferredToolSurface(
        core=[TOOL_SEARCH_NAME],
        pinned=[],
        deferred=["a_tool", "b_tool"],
        tools_by_name={TOOL_SEARCH_NAME: search_tool},
    )


async def _batch(calls: List[str], *, before: int, budget: int):
    adapter, surface = _adapter()
    results: List[ToolMessage] = []
    await te._run_batch(
        adapter,
        [
            {"id": f"c{i}", "name": n, "args": {"query": "a"} if n == TOOL_SEARCH_NAME else {}}
            for i, n in enumerate(calls)
        ],
        [],
        results,
        one_per_step=False,
        timeout=10,
        output_budget=te._BatchOutputBudget(0),
        surface=surface,
        discovered={"a_tool"},
        searches_in_a_row=before,
        search_budget=budget,
    )
    return results


@pytest.mark.asyncio
async def test_within_the_budget_a_search_is_untouched():
    (reply,) = await _batch([TOOL_SEARCH_NAME], before=4, budget=6)
    assert "[Search budget]" not in reply.content and reply.status != "error"


@pytest.mark.asyncio
async def test_past_the_budget_the_search_runs_and_says_to_stop():
    (reply,) = await _batch([TOOL_SEARCH_NAME], before=6, budget=6)
    assert reply.content.startswith("Loaded 1 tool: a_tool"), "the search still ran"
    assert "[Search budget] That was 7 tool searches in a row" in reply.content
    assert "Loaded tools: a_tool" in reply.content


@pytest.mark.asyncio
async def test_past_twice_the_budget_the_search_is_refused():
    (reply,) = await _batch([TOOL_SEARCH_NAME], before=12, budget=6)
    assert reply.status == "error" and "Search not run" in reply.content
    assert "a_tool" in reply.content


@pytest.mark.asyncio
async def test_a_real_call_resets_the_streak_within_a_batch():
    results = await _batch([TOOL_SEARCH_NAME, "get", TOOL_SEARCH_NAME], before=6, budget=6)
    assert "[Search budget]" in results[0].content
    assert "[Search budget]" not in results[2].content


@pytest.mark.asyncio
async def test_a_zero_budget_is_off():
    (reply,) = await _batch([TOOL_SEARCH_NAME], before=50, budget=0)
    assert "[Search budget]" not in reply.content


def test_the_budget_setting_is_read_and_sanitised(monkeypatch):
    from cuga.config import settings

    monkeypatch.setattr(settings.advanced_features, "cuga_lite_tool_search_budget", 6, raising=False)
    assert te._search_budget({}) == 6
    assert te._search_budget({"cuga_lite_tool_search_budget": 0}) == 0
    assert te._search_budget({"cuga_lite_tool_search_budget": "bad"}) == 0


@pytest.mark.asyncio
async def test_the_note_lists_the_newest_loaded_tools_first():
    adapter, surface = _adapter()
    results: List[ToolMessage] = []
    await te._run_batch(
        adapter,
        [{"id": "c0", "name": TOOL_SEARCH_NAME, "args": {"query": "z"}}],
        [],
        results,
        one_per_step=False,
        timeout=10,
        output_budget=te._BatchOutputBudget(0),
        surface=surface,
        discovered={"a_tool", "b_tool"},
        searches_in_a_row=6,
        search_budget=6,
        loaded=["a_tool", "b_tool"],
    )
    assert "Loaded tools: z_tool, b_tool, a_tool" in results[0].content
