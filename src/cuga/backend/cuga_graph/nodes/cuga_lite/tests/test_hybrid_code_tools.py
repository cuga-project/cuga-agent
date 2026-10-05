"""Hybrid: tools as seen from code — tool_search from code, the run_command bridge, the prompt."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import ToolMessage

from cuga.backend.cuga_graph.nodes.cuga_lite.adapter import tool_exec_node as te
from cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools.deferred import discovered_in_history
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common.rpc_client.cuga_tools import FoundTools
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers import execute_python as ep
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.tool_search import TOOL_SEARCH_NAME, ToolSearchResult
from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import render_fc_prompt
from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import counted_tool_call

pytestmark = pytest.mark.unit

LONG = "spotify_show_song_library_with_a_name_long_enough_to_need_a_wire_alias_for_the_api_x"


async def _search(query: str, app_name: Any = None, max_results: Any = None):
    return ToolSearchResult(
        text=f"Loaded 1 tool:\n{LONG}(page_index: int)", discovered=[LONG], stage="keyword"
    )


async def _get(**kwargs):
    return {"ok": True}


# ── tool_search from code ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tool_search_in_code_prints_signatures_lists_names_and_records_them():
    tools = ep.code_tools({TOOL_SEARCH_NAME: _search, "get": _get})
    found_list: List[str] = []
    token = ep.CODE_DISCOVERED.set(found_list)
    try:
        found = await tools[TOOL_SEARCH_NAME]("song library")
    finally:
        ep.CODE_DISCOVERED.reset(token)
    assert isinstance(found, FoundTools)
    assert "page_index: int" in str(found)
    assert found.names == [ep.wire_name(LONG)], "names as the model calls them"
    assert found_list == [LONG], "registered names, for the next bind"


def test_tool_search_in_code_is_not_charged_against_the_budget():
    tools = ep.code_tools({TOOL_SEARCH_NAME: _search, "get": _get})
    assert counted_tool_call(tools[TOOL_SEARCH_NAME]) is tools[TOOL_SEARCH_NAME]
    assert counted_tool_call(tools["get"]) is not tools["get"]


def test_tools_found_from_code_count_as_loaded():
    messages = [
        ToolMessage(
            content="ok",
            tool_call_id="c1",
            name=ep.EXECUTE_PYTHON_NAME,
            artifact={"code_calls": [], "discovered": ["a_tool", "not_deferred"]},
        )
    ]
    assert discovered_in_history(messages, deferred=["a_tool", "b_tool"]) == ["a_tool"]


@pytest.mark.asyncio
async def test_run_code_reports_what_the_code_searched(monkeypatch):
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.code_executor import CodeExecutor

    manager = MagicMock()
    manager.get_variable_names.return_value = []
    state = SimpleNamespace(variables_manager=manager, thread_id=None)
    monkeypatch.setattr(ep, "execute_python_timeout", lambda: 10)
    local = CodeExecutor._get_local_executor()
    monkeypatch.setattr(CodeExecutor, "_get_local_executor", classmethod(lambda cls: local))
    discovered: List[str] = []
    output, _, called = await ep.run_code(
        state,
        "found = await tool_search('song library')\nprint(found.names)",
        {TOOL_SEARCH_NAME: _search},
        discovered=discovered,
    )
    assert ep.wire_name(LONG) in output
    assert discovered == [LONG] and called == [], "a search is not a tool call"


# ── shapes for the cleared-reply stub ────────────────────────────────────────


def test_describe_shape():
    assert ep.describe_shape([{"a": 1}] * 3) == "list[3] of dict"
    assert ep.describe_shape({"a": 1}) == "dict(1 keys)"
    assert ep.describe_shape("abc") == "str(3 chars)"
    assert ep.describe_shape(7) == "int"
    assert ep.describe_shape([]) == "list[0]"


# ── the run_command bridge ───────────────────────────────────────────────────


def test_the_shell_bridge_offers_the_code_tools_but_not_itself():
    adapter = SimpleNamespace(
        _tools_context={
            "get": _get,
            "run_command": _get,
            TOOL_SEARCH_NAME: _search,
            ep.EXECUTE_PYTHON_NAME: _get,
        }
    )
    bridge = te._shell_bridge(adapter, {"cmd": "ls"}, write_check=None, earlier=[])
    assert set(bridge["tools"]) == {"get", TOOL_SEARCH_NAME}
    assert bridge["calls"] == [] and bridge["blocked"] == []


def test_calls_through_the_bridge_are_charged_to_the_budget():
    adapter = SimpleNamespace(_tools_context={"get": _get, TOOL_SEARCH_NAME: _search})
    bridge = te._shell_bridge(adapter, {"cmd": "python s.py"}, write_check=None, earlier=[])
    assert getattr(bridge["tools"]["get"], "_cuga_budget_counted", False), "wrapped by counted_tool_call"
    # tool_search is exempt by marker, the same as a native search.
    assert counted_tool_call(bridge["tools"][TOOL_SEARCH_NAME]) is bridge["tools"][TOOL_SEARCH_NAME]


@pytest.mark.asyncio
async def test_calls_through_the_bridge_are_recorded():
    adapter = SimpleNamespace(_tools_context={"get": _get})
    bridge = te._shell_bridge(adapter, {"cmd": "python s.py"}, write_check=None, earlier=[])
    token = ep.CODE_CALLS.set(bridge["calls"])
    try:
        assert await bridge["tools"]["get"]() == {"ok": True}
    finally:
        ep.CODE_CALLS.reset(token)
    assert bridge["calls"] == ["get"]


# ── prompt and description ───────────────────────────────────────────────────


def test_the_hybrid_prompt_describes_run_command_only_when_it_is_bound():
    assert "cuga_tools" in render_fc_prompt(hybrid=True, shell=True)
    assert "cuga_tools" not in render_fc_prompt(hybrid=True, shell=False)
    assert "cuga_tools" not in render_fc_prompt(hybrid=False, shell=True)


def test_the_description_states_the_real_import_rule(monkeypatch):
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common import benchmark_mode

    monkeypatch.setattr(benchmark_mode, "is_benchmark_mode", lambda: True)
    assert "Any standard-library module" in ep.execute_python_description()
    monkeypatch.setattr(benchmark_mode, "is_benchmark_mode", lambda: False)
    restricted = ep.execute_python_description()
    assert "Allowed imports:" in restricted and "statistics" in restricted
    assert "tool_search" in restricted
