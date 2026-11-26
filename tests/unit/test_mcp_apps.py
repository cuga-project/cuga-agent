"""MCP Apps (SEP-1865) host support: a tool's form pauses the agent and its result resumes it.

Covers the protocol helpers, the MCP manager (visibility, envelope, app tool calls) against
the deterministic mock MCP Apps server in ``tests/fixtures/mcp_apps``, the tool wrapper's
pause, the pause/resume helpers and handler, and the chat UI's HTTP API.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastmcp import Client
from langchain_core.messages import HumanMessage

from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import (
    ActionResponse,
    ActionType,
    AdditionalData,
)
from cuga.backend.cuga_graph.utils.nodes_names import ActionIds, NodeNames
from cuga.backend.mcp_apps import protocol
from cuga.backend.mcp_apps.interaction import create_pause_action, interaction_id_of, outcome_message
from cuga.backend.mcp_apps.protocol import McpAppInteractionPause
from cuga.backend.mcp_apps.store import InteractionStore, interaction_store
from tests.fixtures.mcp_apps.mock_server import FORM_HTML, FORM_URI, mcp

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# protocol
# ---------------------------------------------------------------------------


def test_action_ids_agree():
    assert protocol.ACTION_ID == ActionIds.MCP_APP_INTERACTION
    assert ActionType.MCP_APP.value == "mcp_app"


@pytest.mark.parametrize(
    ("meta", "expected"),
    [
        (
            {"ui": {"resourceUri": "ui://a/b", "visibility": ["app"]}},
            {"resourceUri": "ui://a/b", "visibility": ["app"]},
        ),
        ({"ui/resourceUri": "ui://legacy/view"}, {"resourceUri": "ui://legacy/view"}),
        ({"ui": {"resourceUri": "https://evil.example/x"}}, {}),
        (None, {}),
        ({"fastmcp": {"tags": []}}, {}),
    ],
)
def test_ui_meta(meta, expected):
    assert protocol.ui_meta(meta) == expected


def test_visibility_defaults_to_model_and_app():
    assert protocol.model_can_see({}) and protocol.app_can_call({})
    assert not protocol.model_can_see({"visibility": ["app"]})
    assert not protocol.app_can_call({"visibility": ["model"]})


def test_envelope_round_trip_and_plain_results():
    env = protocol.build_envelope(
        server="s",
        tool="t",
        tool_title=None,
        resource_uri="ui://s/v",
        arguments={"a": 1},
        result={"content": []},
    )
    assert protocol.envelope_from(json.loads(json.dumps(env)))["tool_input"] == {"a": 1}
    assert protocol.envelope_from({"rows": []}) is None
    assert protocol.envelope_from({protocol.ENVELOPE_KEY: {"resource_uri": "http://x"}}) is None


def test_pause_is_not_an_exception():
    """Generated code's ``except Exception`` must not swallow the pause."""
    with pytest.raises(McpAppInteractionPause):
        try:
            raise McpAppInteractionPause({}, app_name="a", tool_name="t", agent_id=None)
        except Exception:  # noqa: BLE001 - this is what agent code does
            pytest.fail("pause was caught as an Exception")


def test_csp_header_is_restrictive_by_default_and_rejects_injection():
    default = protocol.csp_header(None)
    assert "connect-src 'none'" in default and "frame-src 'none'" in default
    policy = protocol.csp_header(
        {
            "connectDomains": ["https://api.example.com", "x; script-src *"],
            "resourceDomains": ["https://cdn.example.com"],
        }
    )
    assert "connect-src https://api.example.com" in policy
    assert "script-src *" not in policy
    assert "https://cdn.example.com" in policy.split("script-src", 1)[1].split(";", 1)[0]


# ---------------------------------------------------------------------------
# the mock MCP Apps server the tests use
# ---------------------------------------------------------------------------


async def test_mock_server_declares_mcp_apps_metadata():
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
        assert tools["open_form"].meta["ui"] == {"resourceUri": FORM_URI, "visibility": ["model", "app"]}
        assert tools["submit_form"].meta["ui"]["visibility"] == ["app"]
        assert tools["plain_tool"].meta["ui"]["visibility"] == ["model"]
        (content,) = await client.read_resource(FORM_URI)
        assert content.mime_type == protocol.UI_MIME_TYPE and content.text == FORM_HTML


# ---------------------------------------------------------------------------
# MCP manager
# ---------------------------------------------------------------------------


def _manager_with_mock_server():
    from cuga.backend.tools_env.registry.mcp_manager.mcp_manager import MCPManager

    mgr = MCPManager(config={})
    mgr.mcp_transports["mock_forms"] = object()
    mgr._call_transport = lambda server_name: mcp  # in-process FastMCP server as the transport
    mgr.mcp_app_tool_meta["mock_forms"] = {
        "open_form": {"resourceUri": FORM_URI, "visibility": ["model", "app"], "title": None},
        "submit_form": {"visibility": ["app"], "title": None},
        "plain_tool": {"visibility": ["model"], "title": None},
    }
    mgr.original_tool_name_by_sanitized["mock_forms_open_form"] = "open_form"
    mgr.original_tool_name_by_sanitized["mock_forms_plain_tool"] = "plain_tool"
    return mgr


@contextmanager
def _ui_host(enabled: bool = True):
    token = protocol.set_ui_host(enabled)
    try:
        yield
    finally:
        protocol.reset_ui_host(token)


async def test_manager_wraps_mcp_app_results_in_an_envelope():
    mgr = _manager_with_mock_server()
    args = {"title": "Orders", "fields": ["total"]}
    with _ui_host():
        (text,) = await mgr._call_mcp_server_tool("mock_forms", "mock_forms_open_form", args)
    env = protocol.envelope_from(json.loads(text.text))
    assert env["server"] == "mock_forms" and env["tool"] == "open_form"
    assert env["resource_uri"] == FORM_URI and env["tool_input"] == args
    assert env["tool_result"]["structuredContent"]["form"]["fields"] == ["total"]

    with _ui_host():
        (plain,) = await mgr._call_mcp_server_tool("mock_forms", "mock_forms_plain_tool", {"x": 2})
    assert json.loads(plain.text) == {"double": 4}  # a tool without a UI is unchanged


async def test_manager_returns_the_ordinary_result_when_the_client_cannot_show_apps():
    """Regression: SDK, API and channel callers keep getting an MCP Apps tool's plain result."""
    mgr = _manager_with_mock_server()
    args = {"title": "Orders", "fields": ["total"]}
    assert protocol.ui_host_enabled() is False  # the default
    (text,) = await mgr._call_mcp_server_tool("mock_forms", "mock_forms_open_form", args)
    data = json.loads(text.text)
    assert protocol.envelope_from(data) is None
    assert data["form"]["fields"] == ["total"]  # the tool's structured content, as before MCP Apps support


async def test_manager_app_tool_calls_respect_visibility():
    mgr = _manager_with_mock_server()
    with _ui_host():
        opened = await mgr._call_mcp_server_tool(
            "mock_forms", "mock_forms_open_form", {"title": "T", "fields": ["a"]}
        )
    sid = protocol.envelope_from(json.loads(opened[0].text))["tool_result"]["structuredContent"]["form"][
        "session_id"
    ]
    result = await mgr.call_mcp_app_tool(
        "mock_forms", "submit_form", {"session_id": sid, "values": {"a": "1"}}
    )
    assert result["isError"] is False
    assert result["structuredContent"] == {"status": "completed", "session_id": sid, "values": {"a": "1"}}
    bad = await mgr.call_mcp_app_tool("mock_forms", "submit_form", {"session_id": sid})
    assert bad["isError"] is True  # a tool error is forwarded to the app as a result, not raised

    with pytest.raises(PermissionError):  # model-only tool
        await mgr.call_mcp_app_tool("mock_forms", "plain_tool", {"x": 1})
    with pytest.raises(LookupError):
        await mgr.call_mcp_app_tool("mock_forms", "no_such_tool", {})
    with pytest.raises(LookupError):
        await mgr.call_mcp_app_tool("other_server", "open_form", {})
    with pytest.raises(PermissionError):
        await mgr.read_mcp_app_resource("mock_forms", "ui://mock-forms/other")
    content = await mgr.read_mcp_app_resource("mock_forms", FORM_URI)
    assert content["mimeType"] == protocol.UI_MIME_TYPE and content["text"] == FORM_HTML


# ---------------------------------------------------------------------------
# agent tool wrapper
# ---------------------------------------------------------------------------


def _open_form_tool(provider, app_name: str = "mock_forms"):
    return provider.create_tool_from_api_dict(
        f"{app_name}_open_form",
        {
            "description": "d",
            "parameters": {"properties": {"table_name": {"type": "string"}}, "required": []},
        },
        app_name,
        agent_id="agent-1",
    )


async def test_registry_tool_raises_pause_for_mcp_app_results(monkeypatch):
    from cuga.backend.cuga_graph.nodes.cuga_lite.providers import registry as provider

    env = protocol.build_envelope(
        server="mock_forms",
        tool="open_form",
        tool_title="Propose Table",
        resource_uri=FORM_URI,
        arguments={"table_name": "t"},
        result={"content": [{"type": "text", "text": "Waiting"}]},
    )

    async def fake_call_api(*_args, **_kwargs):
        return env

    monkeypatch.setattr(provider, "call_api", fake_call_api)
    tool = _open_form_tool(provider)
    with _ui_host(), pytest.raises(McpAppInteractionPause) as caught:
        await tool.coroutine(table_name="t")
    assert caught.value.tool_name == "mock_forms_open_form"
    assert caught.value.agent_id == "agent-1"
    assert caught.value.envelope["resource_uri"] == FORM_URI


# ---------------------------------------------------------------------------
# pause / resume
# ---------------------------------------------------------------------------


def _pause() -> McpAppInteractionPause:
    env = protocol.build_envelope(
        server="mock_forms",
        tool="open_form",
        tool_title=None,
        resource_uri=FORM_URI,
        arguments={"table_name": "customers"},
        result={"content": [{"type": "text", "text": "Proposed 5 columns."}], "isError": False},
    )[protocol.ENVELOPE_KEY]
    return McpAppInteractionPause(env, app_name="mock_forms", tool_name="mock_forms_open_form", agent_id=None)


def test_create_pause_action_records_the_interaction():
    action, note = create_pause_action(_pause(), thread_id="thread-1", user_id="u1")
    info = action.additional_data.tool["mcp_app"]
    assert action.action_id == ActionIds.MCP_APP_INTERACTION
    assert action.type == ActionType.MCP_APP.value
    item = interaction_store.get(info["interaction_id"])
    assert item.thread_id == "thread-1" and item.server == "mock_forms" and item.status == "pending"
    assert "mock_forms_open_form" in note and "did not run" in note
    assert interaction_id_of(action.additional_data) == info["interaction_id"]


def test_outcome_message_prefers_model_context_then_server_result():
    store = InteractionStore()
    item = store.create(
        thread_id="t",
        user_id=None,
        agent_id=None,
        server="s",
        tool="open_form",
        agent_tool="s_open_form",
        resource_uri=FORM_URI,
        tool_input={"table_name": "x"},
        tool_result={},
    )
    store.record_app_result(
        item.interaction_id,
        "submit_form",
        {},
        {"content": [{"type": "text", "text": "accepted"}], "structuredContent": {"server": True}},
    )
    text = outcome_message(item, {"message": "Looks good"})
    assert '"server": true' in text and "User: Looks good" in text and "Tool says: accepted" in text

    store.set_model_context(item.interaction_id, {"structuredContent": {"from_app": 1}})
    assert '"from_app": 1' in outcome_message(item, {})
    assert "closed the form" in outcome_message(item, {"cancelled": True})
    assert "could not be continued" in outcome_message(None, {})

    assert store.complete(item.interaction_id) is item
    assert store.complete(item.interaction_id) is None


def _state_for_resume(expected_id: str, sent_id: str):
    from cuga.backend.cuga_graph.state.agent_state import AgentState

    state = AgentState(input="make a table", url="")
    state.chat_messages = [HumanMessage(content="make a table")]
    state.hitl_response = ActionResponse(
        action_id=ActionIds.MCP_APP_INTERACTION,
        response_type=ActionType.MCP_APP,
        timestamp="2026-11-25T00:00:00Z",
        metadata={"interaction_id": sent_id, "message": "Accepted"},
        additional_data=AdditionalData(tool={"mcp_app": {"interaction_id": expected_id}}),
    )
    return state


def test_resume_handler_feeds_the_result_to_the_agent():
    from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_node import CugaLiteHumanInTheLoopHandler

    action, _ = create_pause_action(_pause(), thread_id="thread-2")
    iid = action.additional_data.tool["mcp_app"]["interaction_id"]
    interaction_store.set_model_context(iid, {"structuredContent": {"table_name": "customers"}})

    command = CugaLiteHumanInTheLoopHandler().handle_human_response(_state_for_resume(iid, iid), "CugaLite")
    assert command.goto == "CugaLiteSubgraph"
    last = command.update["chat_messages"][-1]
    assert '"table_name": "customers"' in (last["content"] if isinstance(last, dict) else last.content)
    assert interaction_store.get(iid).status == "completed"


def test_resume_handler_rejects_a_mismatched_interaction():
    from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_node import CugaLiteHumanInTheLoopHandler

    action, _ = create_pause_action(_pause(), thread_id="thread-3")
    iid = action.additional_data.tool["mcp_app"]["interaction_id"]
    command = CugaLiteHumanInTheLoopHandler().handle_human_response(
        _state_for_resume(iid, "forged"), "CugaLite"
    )
    assert command.goto == NodeNames.FINAL_ANSWER_AGENT
    assert interaction_store.get(iid).status == "pending"


# ---------------------------------------------------------------------------
# HTTP API for the chat UI
# ---------------------------------------------------------------------------


@pytest.fixture
def api(monkeypatch):
    from cuga.backend.mcp_apps import routes

    calls = []

    async def fake_registry(path, payload, agent_id):
        calls.append((path, payload, agent_id))
        if path.endswith("/resources/read"):
            return {
                "uri": payload["uri"],
                "mimeType": protocol.UI_MIME_TYPE,
                "text": "<p>form</p>",
                "_meta": {"ui": {"csp": {"connectDomains": ["https://api.example.com"]}}},
            }
        return {"content": [{"type": "text", "text": "ok"}], "isError": False}

    monkeypatch.setattr(routes, "_registry", fake_registry)
    app = FastAPI()
    app.include_router(routes.router)
    action, _ = create_pause_action(_pause(), thread_id="thread-api")
    iid = action.additional_data.tool["mcp_app"]["interaction_id"]
    return SimpleNamespace(client=TestClient(app), iid=iid, calls=calls)


def test_api_reads_the_resource_of_the_recorded_server(api):
    r = api.client.post(
        "/api/mcp-apps/resource", json={"interaction_id": api.iid}, headers={"X-Thread-ID": "thread-api"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["html"] == "<p>form</p>" and body["csp"] == {"connectDomains": ["https://api.example.com"]}
    assert api.calls[-1][1] == {"server": "mock_forms", "uri": FORM_URI}

    assert api.client.post("/api/mcp-apps/resource", json={"interaction_id": "nope"}).status_code == 404
    other = api.client.post(
        "/api/mcp-apps/resource", json={"interaction_id": api.iid}, headers={"X-Thread-ID": "x"}
    )
    assert other.status_code == 403


def test_api_tool_calls_are_recorded_and_closed_after_completion(api):
    r = api.client.post(
        "/api/mcp-apps/tools/call",
        json={"interaction_id": api.iid, "name": "submit_form", "arguments": {"a": 1}},
    )
    assert r.status_code == 200
    assert api.calls[-1][1] == {
        "server": "mock_forms",
        "name": "submit_form",
        "arguments": {"a": 1},
    }
    assert interaction_store.get(api.iid).app_results[-1]["tool"] == "submit_form"

    assert (
        api.client.post(
            "/api/mcp-apps/model-context", json={"interaction_id": api.iid, "structuredContent": {"k": 1}}
        ).status_code
        == 200
    )
    assert interaction_store.get(api.iid).model_context == {"structuredContent": {"k": 1}}

    interaction_store.complete(api.iid)
    late = api.client.post("/api/mcp-apps/tools/call", json={"interaction_id": api.iid, "name": "x"})
    assert late.status_code == 409


def test_sandbox_proxy_sets_csp_and_allowed_embedders(api):
    r = api.client.get(
        "/mcp-apps/sandbox_proxy.html",
        params={"csp": json.dumps({"connectDomains": ["https://api.example.com"]})},
        headers={"host": "127.0.0.1:7860"},
    )
    assert r.status_code == 200
    policy = r.headers["content-security-policy"]
    assert "connect-src https://api.example.com" in policy
    assert "frame-ancestors http://localhost:7860" in policy
    assert '["http://localhost:7860"]' in r.text


# ---------------------------------------------------------------------------
# client capability: forms only for a client that renders MCP Apps
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [("1", True), ("true", True), ("on", True), (None, False), ("", False), ("0", False), ("no", False)],
)
def test_client_header_values(value, expected):
    assert protocol.header_enabled(value) is expected


def test_ui_host_flag_is_task_local_and_resettable():
    assert protocol.ui_host_enabled() is False
    token = protocol.set_ui_host(True)
    assert protocol.ui_host_enabled() is True
    protocol.reset_ui_host(token)
    assert protocol.ui_host_enabled() is False


@pytest.mark.parametrize("enabled", [False, True])
async def test_call_api_asks_for_the_envelope_only_for_an_app_client(monkeypatch, enabled):
    from cuga.backend.cuga_graph.nodes.cuga_lite.providers import registry as provider

    sent = []

    async def fake_post(session, url, payload, timeout_seconds):
        sent.append(payload)
        return 200, json.dumps({"ok": True})

    monkeypatch.setattr(provider, "post_registry_call", fake_post)
    with _ui_host(enabled):
        await provider.call_api("mock_forms", "mock_forms_plain_tool", {"table_name": "t"})
    assert sent, "the registry was not called"
    if enabled:
        assert sent[-1]["mcp_apps"] is True
    else:
        # Unchanged wire format for every caller that cannot show forms.
        assert sent[-1] == {
            "function_name": "mock_forms_plain_tool",
            "app_name": "mock_forms",
            "args": {"table_name": "t"},
        }


@pytest.mark.parametrize("flag", [False, True])
async def test_registry_call_endpoint_scopes_the_client_flag_to_the_call(monkeypatch, flag):
    from cuga.backend.tools_env.registry.registry import api_registry_server as srv

    seen = []

    class FakeText:
        text = '{"ok": true}'

    class FakeReg:
        async def show_apis_for_app(self, app_name):
            return {}

        async def call_function(self, **kwargs):
            seen.append(protocol.ui_host_enabled())
            return [FakeText()]

    monkeypatch.setattr(srv, "database_mode", False)
    monkeypatch.setattr(srv, "registry", FakeReg(), raising=False)
    monkeypatch.setattr(srv, "mcp_manager", SimpleNamespace(auth_config={}), raising=False)
    request = srv.FunctionCallRequest(app_name="a", function_name="f", args={}, mcp_apps=flag)
    assert "mcp_apps" not in json.loads(request.model_dump_json())  # kept out of traces
    await srv.call_mcp_function(request)
    assert seen == [flag]
    assert protocol.ui_host_enabled() is False  # reset after the call


def test_registry_call_request_defaults_to_no_apps():
    from cuga.backend.tools_env.registry.registry import api_registry_server as srv

    request = srv.FunctionCallRequest.model_validate({"app_name": "a", "function_name": "f", "args": {}})
    assert request.mcp_apps is False


# ---------------------------------------------------------------------------
# sandbox node and CugaLite routing
# ---------------------------------------------------------------------------


class _FakeAdapter:
    messages_key = "chat_messages"
    sender_name = "CugaLite"

    def __init__(self):
        self.steps = []
        self._tracker = SimpleNamespace(collect_step=lambda step: self.steps.append(step))

    def resolve_max_steps(self, state, max_steps):
        return max_steps

    def get_messages(self, state):
        return list(state.chat_messages)


def _sandbox_state(step_count: int = 0):
    return SimpleNamespace(tool_calls=[], step_count=step_count, chat_messages=[HumanMessage(content="go")])


def test_sandbox_pause_ends_the_subgraph_with_a_human_action():
    from langgraph.graph import END

    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node import _mcp_app_pause_command
    from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import ToolCallTracker

    ToolCallTracker.start_tracking()
    adapter = _FakeAdapter()
    command = _mcp_app_pause_command(
        adapter,
        _sandbox_state(),
        _pause(),
        thread_id="thread-sb",
        user_id="u1",
        fc_plan=None,
        fc_outcomes={},
        fc_meta={},
        track_tool_calls=True,
        max_steps=10,
    )
    assert command.goto == END
    action = command.update["hitl_action"]
    assert action.action_id == ActionIds.MCP_APP_INTERACTION
    assert command.update["sender"] == "CugaLite" and command.update["step_count"] == 1
    note = command.update["chat_messages"][-1].content
    assert "opened an interactive form" in note
    item = interaction_store.get(action.additional_data.tool["mcp_app"]["interaction_id"])
    assert item.thread_id == "thread-sb" and item.user_id == "u1"
    assert [s.name for s in adapter.steps] == ["McpAppInteraction"]


def test_sandbox_pause_respects_the_step_limit():
    from langgraph.graph import END

    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node import _mcp_app_pause_command
    from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import ToolCallTracker

    ToolCallTracker.start_tracking()
    command = _mcp_app_pause_command(
        _FakeAdapter(),
        _sandbox_state(step_count=3),
        _pause(),
        thread_id="t",
        user_id=None,
        fc_plan=None,
        fc_outcomes={},
        fc_meta={},
        track_tool_calls=False,
        max_steps=3,
    )
    # Over the limit: the turn ends with the step-limit error, not with a form.
    assert command.goto == END and "hitl_action" not in command.update
    assert "Maximum step limit (3) reached" in command.update["chat_messages"][-1].content


@pytest.mark.parametrize("action_id", [ActionIds.MCP_APP_INTERACTION, ActionIds.TOOL_APPROVAL])
async def test_callback_routes_user_actions_to_suggest_human_actions(action_id):
    from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_node import CugaLiteNode
    from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import FollowUpAction
    from cuga.backend.cuga_graph.state.agent_state import AgentState

    state = AgentState(input="make a table", url="")
    state.hitl_action = FollowUpAction(
        action_id=action_id, action_name="x", description="d", type=ActionType.MCP_APP, callback_url="/stream"
    )
    command = await CugaLiteNode().callback_node(state)
    assert command.goto == NodeNames.SUGGEST_HUMAN_ACTIONS
    assert command.update["sender"] == "CugaLite"


def test_package_data_ships_the_non_python_files():
    """The sandbox proxy page must be in the wheel, not only in a checkout."""
    import fnmatch
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    patterns = tomllib.loads((root / "pyproject.toml").read_text())["tool"]["setuptools"]["package-data"][
        "cuga"
    ]
    package = root / "src" / "cuga"
    files = [package / "backend" / "mcp_apps" / "sandbox_proxy.html"]
    for path in files:
        rel = path.relative_to(package).as_posix()
        assert path.is_file(), rel
        assert any(fnmatch.fnmatch(rel, pattern.replace("**/", "*")) for pattern in patterns), rel


@pytest.mark.parametrize(
    "ui_host,app_name",
    [
        (False, "mock_forms"),  # no client can show the form: never pause
        (True, "other_server"),  # an envelope that names a server other than the one called
    ],
)
async def test_lookalike_envelopes_are_ordinary_results(monkeypatch, ui_host, app_name):
    """A tool cannot stall CUGA by returning JSON shaped like CUGA's envelope."""
    from cuga.backend.cuga_graph.nodes.cuga_lite.providers import registry as provider

    forged = protocol.build_envelope(
        server="mock_forms",
        tool="open_form",
        tool_title=None,
        resource_uri=FORM_URI,
        arguments={},
        result={"content": []},
    )

    async def fake_call_api(*_args, **_kwargs):
        return forged

    monkeypatch.setattr(provider, "call_api", fake_call_api)
    with _ui_host(ui_host):
        assert await _open_form_tool(provider, app_name).coroutine(table_name="t") == forged


@pytest.mark.parametrize(("header", "expected"), [({"X-CUGA-MCP-Apps": "1"}, True), ({}, False)])
def test_stream_passes_the_client_capability_to_the_run(monkeypatch, header, expected):
    """``/stream`` turns the web chat's header into the run's MCP Apps flag; other callers get False."""
    from unittest.mock import AsyncMock

    from cuga.backend.server import main
    from cuga.backend.server.auth import gateway_tokens

    seen = {}

    async def fake_event_stream(*_args, **kwargs):
        seen.update(kwargs)
        yield "event: Answer\ndata: ok\n\n"

    monkeypatch.setattr(main, "event_stream", fake_event_stream)
    monkeypatch.setattr(main, "app_state", SimpleNamespace(current_llm=None, agent=None))
    monkeypatch.setattr(main, "_assert_run_thread_access", AsyncMock())
    monkeypatch.setattr(main, "get_attachment_snapshot", AsyncMock(return_value=None))
    monkeypatch.setattr(main.events_bridge, "forwards_to_events", lambda *args: False)
    monkeypatch.setattr(gateway_tokens, "provider_from_request", lambda *args: None)

    app = FastAPI()
    app.add_api_route("/stream", main.stream, methods=["POST"])
    app.dependency_overrides[main.require_chat_access] = lambda: None
    # A draft (preview) chat: no conversation record or thread lease is involved.
    response = TestClient(app).post(
        "/stream", json={"query": "hi"}, headers={"X-Use-Draft": "true", "X-Thread-ID": "t-1", **header}
    )
    assert response.status_code == 200, response.text
    assert seen["mcp_apps"] is expected
