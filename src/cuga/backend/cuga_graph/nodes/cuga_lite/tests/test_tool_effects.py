"""Paging notes and write detection (AppWorld hybrid failure analysis, fixes A and B)."""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage, ToolMessage

from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.tool_effects import (
    has_write_tools,
    is_noop_answer,
    is_write_tool,
    page_note,
    paging,
    turn_made_a_write,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.providers.registry import create_tool_from_api_dict

pytestmark = pytest.mark.unit

PAGED = [
    {"name": "page_index", "type": "integer", "required": False, "default": 0},
    {"name": "page_limit", "type": "integer", "required": False, "default": 5},
]


def _tool(name, method="GET", path="/x", params=()):
    return create_tool_from_api_dict(
        name, {"description": "d", "parameters": list(params), "method": method, "path": path}, "app"
    )


def test_write_detection_by_http_method_excluding_auth():
    assert is_write_tool(_tool("app_place_order", "POST", "/orders"))
    assert is_write_tool(_tool("app_delete_thread", "DELETE", "/threads/{id}"))
    assert not is_write_tool(_tool("app_show_cart", "GET", "/cart"))
    assert not is_write_tool(_tool("app_login_auth_token_post", "POST", "/auth/token"))
    assert not is_write_tool(None)


def test_paging_detection_uses_kwargs_then_schema_defaults():
    tool = _tool("app_list", params=PAGED)
    assert paging(tool, {}) == ("page_index", 0, 5)
    assert paging(tool, {"page_index": 2, "page_limit": 20}) == ("page_index", 2, 20)
    assert paging(_tool("app_one"), {}) is None


def test_page_note_only_on_a_full_page():
    tool = _tool("app_list", params=PAGED)
    assert page_note(tool, {}, [1, 2, 3], hybrid=True) is None, "short page = last page"
    note = page_note(tool, {}, [1, 2, 3, 4, 5], hybrid=True)
    assert "page_index=0 with 5 items per page" in note and "execute_python" in note
    assert "page_index=1" in page_note(tool, {}, [1, 2, 3, 4, 5], hybrid=False)
    assert page_note(tool, {}, {"not": "a list"}, hybrid=True) is None


@pytest.mark.parametrize("text", ["N/A", "n/a.", "", "  none ", "-"])
def test_noop_answers(text):
    assert is_noop_answer(text)


@pytest.mark.parametrize("text", ["15", "Yes", "Done, order 3146 placed."])
def test_real_answers(text):
    assert not is_noop_answer(text)


def test_turn_write_detection_sees_native_and_code_calls():
    tools = {"app_place_order": _tool("app_place_order", "POST"), "app_show_cart": _tool("app_show_cart")}
    assert has_write_tools(tools)
    read_only = [HumanMessage(content="q"), ToolMessage(content="[]", tool_call_id="1", name="app_show_cart")]
    assert not turn_made_a_write(read_only, 0, tools)
    native = read_only + [ToolMessage(content="ok", tool_call_id="2", name="app_place_order")]
    assert turn_made_a_write(native, 0, tools)
    failed = read_only + [
        ToolMessage(content="err", tool_call_id="2", name="app_place_order", status="error")
    ]
    assert not turn_made_a_write(failed, 0, tools)
    code = read_only + [
        ToolMessage(
            content="out",
            tool_call_id="3",
            name="execute_python",
            artifact={"code_calls": ["app_place_order"]},
        )
    ]
    assert turn_made_a_write(code, 0, tools)
    assert not turn_made_a_write(native, 3, tools), "earlier turns do not count"
