"""Function-calling dynamic tool binding: tools found by find_tools or already called, per searched app."""

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.graph_adapter import (
    dynamic_tool_names,
    found_tool_names,
    searched_apps,
    tool_app_name,
)

pytestmark = pytest.mark.unit

EXE = ["google_mail__send_email", "google_mail__list_emails", "slack__post_message", "files__read_file"]


def _history():
    return [
        HumanMessage(content="task"),
        AIMessage(
            content="",
            tool_calls=[
                {"name": "find_tools", "args": {"query": "send", "app_name": "google_mail"}, "id": "c1"}
            ],
        ),
        ToolMessage(
            content="send_email(to, subject, body): send an email", tool_call_id="c1", name="find_tools"
        ),
        AIMessage(
            content="", tool_calls=[{"name": "files__read_file", "args": {"file_path": "a"}, "id": "c2"}]
        ),
        ToolMessage(content="ok", tool_call_id="c2", name="files__read_file"),
    ]


def test_found_tools_are_matched_within_the_searched_app_and_called_tools_are_kept():
    assert found_tool_names(_history(), EXE) == ["google_mail__send_email", "files__read_file"]


def test_nothing_found_at_the_start():
    assert found_tool_names([HumanMessage(content="task")], EXE) == []


def test_searched_apps_come_from_find_tools_calls():
    assert searched_apps(_history()) == {"google_mail"}


def test_unprefixed_names_use_the_providers_app_map():
    # SDK / LangChain providers name tools without an ``app__`` prefix; the app comes from the provider's grouping.
    exe = ["gmail_send_email", "gmail_find_email", "slack_send_channel_message"]
    app_of = {"gmail_send_email": "gmail", "gmail_find_email": "gmail", "slack_send_channel_message": "slack"}
    history = [
        HumanMessage(content="task"),
        AIMessage(content="", tool_calls=[{"name": "find_tools", "args": {"app_name": "gmail"}, "id": "c1"}]),
        ToolMessage(content="gmail_send_email(to, body)", tool_call_id="c1", name="find_tools"),
    ]
    assert found_tool_names(history, exe, app_of) == ["gmail_send_email"]
    assert tool_app_name("slack_send_channel_message", app_of) == "slack"
    assert tool_app_name("slack__post_message") == "slack"
    assert tool_app_name("plain_tool") == ""


def test_true_mode_binds_only_the_tools_found_or_called():
    assert dynamic_tool_names(_history(), EXE, True) == ["google_mail__send_email", "files__read_file"]


def test_apps_mode_binds_every_tool_of_each_app_searched_or_used():
    assert dynamic_tool_names(_history(), EXE, "apps") == [
        "google_mail__send_email",
        "google_mail__list_emails",
        "files__read_file",
    ]


def test_apps_mode_with_the_providers_app_map():
    exe = ["gmail_send_email", "gmail_find_email", "slack_send_channel_message"]
    app_of = {"gmail_send_email": "gmail", "gmail_find_email": "gmail", "slack_send_channel_message": "slack"}
    history = [
        HumanMessage(content="task"),
        AIMessage(content="", tool_calls=[{"name": "find_tools", "args": {"app_name": "gmail"}, "id": "c1"}]),
        ToolMessage(content="gmail_send_email(to, body)", tool_call_id="c1", name="find_tools"),
    ]
    assert dynamic_tool_names(history, exe, "apps", app_of) == ["gmail_send_email", "gmail_find_email"]


def test_nothing_is_bound_before_any_search():
    assert dynamic_tool_names([HumanMessage(content="task")], EXE, "apps") == []
