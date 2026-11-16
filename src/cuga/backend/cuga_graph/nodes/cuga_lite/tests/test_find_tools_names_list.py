"""find_tools names list: a result ends with the names of the app's other tools, and ``tool_names`` loads them by name."""

import inspect
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool

from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.graph_adapter import found_tool_names
from cuga.backend.cuga_graph.nodes.cuga_lite.helpers import find_tools as ft
from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister import render_tools_markdown
from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.base import ShortlistCandidate
from cuga.config import settings

pytestmark = pytest.mark.unit

MAIL = ["google_mail__send_email", "google_mail__list_emails", "google_mail__delete_email"]


def _noop(value: str = "") -> str:
    return value


def _tools(names):
    return [StructuredTool.from_function(func=_noop, name=n, description=f"{n} tool") for n in names]


def _result(query, *names):
    return render_tools_markdown(
        [ShortlistCandidate(name=n, reasoning="Matches.") for n in names], _tools(MAIL), query
    )


def test_the_names_list_holds_only_the_tools_the_result_did_not_describe():
    text = _result("send a message", "google_mail__send_email")
    section = ft.names_list_section("google_mail", _tools(MAIL), text)
    assert section.lstrip().startswith(ft.NAMES_LIST_MARKER)
    assert "google_mail__list_emails" in section and "google_mail__delete_email" in section
    assert "google_mail__send_email" not in section
    assert 'tool_names=["<exact name>", ...]' in section


def test_no_names_list_when_the_result_describes_every_tool_of_the_app():
    text = _result("everything", *MAIL)
    assert ft.names_list_section("google_mail", _tools(MAIL), text) == ""


def test_tools_are_loaded_by_exact_name_with_or_without_the_app_prefix_and_unknown_names_are_reported():
    text = ft.render_tools_by_name(
        "google_mail", _tools(MAIL), ["google_mail__list_emails", "delete_email", "list_emails", "nope"]
    )
    assert text.count("`google_mail__list_emails`") == 1  # a name given twice loads once
    assert "`google_mail__delete_email`" in text
    assert "Not a tool of `google_mail`: nope" in text


def test_only_the_loaded_tools_count_as_found_not_the_names_in_the_list():
    searched = _result("send a message", "google_mail__send_email") + ft.names_list_section(
        "google_mail", _tools(MAIL), _result("send a message", "google_mail__send_email")
    )
    loaded = ft.render_tools_by_name("google_mail", _tools(MAIL), ["google_mail__list_emails"])
    messages = [
        HumanMessage(content="task"),
        AIMessage(
            content="",
            tool_calls=[
                {"name": "find_tools", "args": {"query": "send", "app_name": "google_mail"}, "id": "1"}
            ],
        ),
        ToolMessage(content=searched, tool_call_id="1", name="find_tools"),
    ]
    assert found_tool_names(messages, MAIL) == ["google_mail__send_email"]
    messages += [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "find_tools",
                    "args": {
                        "query": "",
                        "app_name": "google_mail",
                        "tool_names": ["google_mail__list_emails"],
                    },
                    "id": "2",
                }
            ],
        ),
        ToolMessage(content=loaded, tool_call_id="2", name="find_tools"),
    ]
    assert found_tool_names(messages, MAIL) == ["google_mail__send_email", "google_mail__list_emails"]


async def _make_tool():
    app = type("App", (), {"name": "google_mail"})()
    return await ft.create_find_tools_tool(_tools(MAIL), [app], {"google_mail": _tools(MAIL)}, llm=None)


async def _call(tool, args):
    """find_tools is an async function registered as ``func``, so ``ainvoke`` returns an awaitable; the executor awaits it."""
    out = await tool.ainvoke(args)
    return await out if inspect.isawaitable(out) else out


@pytest.mark.asyncio
async def test_setting_off_find_tools_has_no_tool_names_argument(monkeypatch):
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_find_tools_names_list", False, raising=False)
    tool = await _make_tool()
    assert "tool_names" not in tool.args


@pytest.mark.asyncio
async def test_setting_on_a_search_ends_with_the_names_list_and_tool_names_loads_without_a_search(
    monkeypatch,
):
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_find_tools_names_list", True, raising=False)
    tool = await _make_tool()
    assert "tool_names" in tool.args
    search = AsyncMock(return_value=_result("send", "google_mail__send_email"))
    with patch.object(ft.PromptUtils, "find_tools", search):
        out = await _call(tool, {"query": "send", "app_name": "google_mail"})
        assert ft.NAMES_LIST_MARKER in out and "google_mail__delete_email" in out
        assert search.await_count == 1
        loaded = await _call(
            tool, {"query": "", "app_name": "google_mail", "tool_names": ["google_mail__delete_email"]}
        )
        assert search.await_count == 1  # no new search
        assert "`google_mail__delete_email`" in loaded
