"""Function-calling dynamic tool binding: tools found by find_tools or already called, per searched app."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from cuga.backend.cuga_graph.nodes.cuga_lite.adapter import graph_adapter as ga
from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.graph_adapter import (
    dynamic_tool_names,
    found_tool_names,
    searched_apps,
    tool_app_name,
)
from cuga.config import settings

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


# Executable set as prepare records it with agent spawning on: find_tools and the spawn tools have no app.
EXE_WITH_IN_GRAPH = ["find_tools", *EXE, "spawn_agent", "get_agent_result"]


def test_tools_of_no_app_are_bound_before_any_search():
    for mode in (True, "apps"):
        assert dynamic_tool_names([HumanMessage(content="task")], EXE_WITH_IN_GRAPH, mode) == [
            "spawn_agent",
            "get_agent_result",
        ], "find_tools is bound separately, so it is not listed"


def test_tools_of_no_app_are_kept_next_to_the_found_tools():
    assert dynamic_tool_names(_history(), EXE_WITH_IN_GRAPH, "apps") == [
        "google_mail__send_email",
        "google_mail__list_emails",
        "files__read_file",
        "spawn_agent",
        "get_agent_result",
    ]


@pytest.mark.asyncio
async def test_resolve_bind_tools_binds_the_tools_of_no_app_and_find_tools_once(monkeypatch):
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_fc_dynamic_tools", "apps", raising=False)
    captured = []

    async def fake_resolve(active_model, **kwargs):
        captured.append(kwargs["configurable"])
        return "BOUND"

    adapter = ga.AgentGraphAdapter(
        tracker=MagicMock(),
        base_callbacks=[],
        task_todos_ref=[],
        tools_context_ref={
            "_lc_bind_tools_executable_names": EXE_WITH_IN_GRAPH,
            "_lc_bind_tools_find_tools": object(),
        },
        base_tool_provider=None,
    )
    state = SimpleNamespace(chat_messages=[HumanMessage(content="task")])
    with patch.object(ga, "resolve_model_with_bind_tools", new=AsyncMock(side_effect=fake_resolve)):
        await adapter.resolve_bind_tools(state, object(), {"cuga_lite_execution_mode": "function_calling"})

    (cfg,) = captured
    assert cfg["cuga_lite_bind_tools_mode"] == "tools"
    assert cfg["cuga_lite_bind_tools_tool_names"] == ["spawn_agent", "get_agent_result"]
    assert cfg["cuga_lite_bind_tools_include_find_tools"] is True
