"""MCP Apps across CUGA's real services, with the deterministic mock server and no LLM.

The mock MCP Apps server (``tests/fixtures/mcp_apps``) and CUGA's registry server run over
real HTTP on free local ports. The agent's tool wrapper calls the registry as it does in a
run; the chat UI's MCP Apps API reaches the registry and the mock server through it. Covers:

- app-only tools are kept from the agent; model-only tools are kept from the app,
- a form opens only when the client renders MCP Apps; otherwise the plain result comes back,
- the form's resource, its tool calls and its model context go through CUGA,
- finishing the form resumes the agent with the app's result.
"""

from __future__ import annotations

import os
from contextlib import ExitStack
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import FastAPI
from langchain_core.messages import HumanMessage

from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import ActionResponse, ActionType
from cuga.backend.cuga_graph.utils.nodes_names import ActionIds
from cuga.backend.mcp_apps import protocol
from cuga.backend.mcp_apps.interaction import create_pause_action
from cuga.backend.mcp_apps.protocol import McpAppInteractionPause
from cuga.backend.mcp_apps.store import interaction_store
from tests.fixtures.mcp_apps.mock_server import (
    FORM_HTML,
    FORM_URI,
    SERVER_NAME,
    free_port,
    run_mock_server,
    serve_http,
)

pytestmark = pytest.mark.slow

THREAD = "thread-mcp-apps-system"


@pytest.fixture(scope="module")
def services(tmp_path_factory):
    """The mock MCP server and CUGA's registry, each on its own port."""
    from cuga.backend.tools_env.registry.registry import api_registry_server as registry_server
    from cuga.config import settings

    config = tmp_path_factory.mktemp("mcp_apps") / "mcp_servers.yaml"
    with ExitStack() as stack:
        mcp_url = stack.enter_context(run_mock_server())
        config.write_text(f"mcpServers:\n  {SERVER_NAME}:\n    url: {mcp_url}\n    transport: http\n")
        previous_file = os.environ.get("MCP_SERVERS_FILE")
        os.environ["MCP_SERVERS_FILE"] = str(config)
        stack.callback(
            lambda: (
                os.environ.__setitem__("MCP_SERVERS_FILE", previous_file)
                if previous_file is not None
                else os.environ.pop("MCP_SERVERS_FILE", None)
            )
        )
        registry_url = stack.enter_context(serve_http(registry_server.app, free_port(), "/status"))
        previous_host = getattr(settings.server_ports, "registry_host", None)
        settings.server_ports.registry_host = registry_url
        stack.callback(lambda: setattr(settings.server_ports, "registry_host", previous_host))
        yield registry_url


@pytest.fixture
def ui_host():
    token = protocol.set_ui_host(True)
    yield
    protocol.reset_ui_host(token)


def _tool(name: str, properties: dict):
    from cuga.backend.cuga_graph.nodes.cuga_lite.providers.registry import create_tool_from_api_dict

    return create_tool_from_api_dict(
        f"{SERVER_NAME}_{name}",
        {"description": name, "parameters": {"properties": properties, "required": list(properties)}},
        SERVER_NAME,
    )


def _open_form_tool():
    return _tool(
        "open_form", {"title": {"type": "string"}, "fields": {"type": "array", "items": {"type": "string"}}}
    )


async def test_the_agent_sees_only_model_visible_tools(services):
    async with httpx.AsyncClient(base_url=services, timeout=30) as client:
        apis = (await client.get(f"/applications/{SERVER_NAME}/apis")).json()
    names = set(apis)
    assert f"{SERVER_NAME}_open_form" in names and f"{SERVER_NAME}_plain_tool" in names
    assert f"{SERVER_NAME}_submit_form" not in names  # app-only


async def test_without_an_app_client_the_tool_returns_its_plain_result(services):
    """Regression: the SDK, API callers and channels keep the result they got before MCP Apps."""
    result = await _open_form_tool().coroutine(title="Contact", fields=["email"])
    assert result["form"]["fields"] == ["email"]
    assert protocol.envelope_from(result) is None


async def test_ordinary_tools_are_unchanged_for_an_app_client(services, ui_host):
    assert await _tool("plain_tool", {"x": {"type": "integer"}}).coroutine(x=21) == {"double": 42}


async def test_form_round_trip_through_cuga(services, ui_host):
    from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_node import CugaLiteHumanInTheLoopHandler
    from cuga.backend.cuga_graph.state.agent_state import AgentState
    from cuga.backend.mcp_apps import routes

    # 1. The agent calls the tool: its form opens and the code block stops.
    with pytest.raises(McpAppInteractionPause) as paused:
        await _open_form_tool().coroutine(title="Contact", fields=["email", "phone"])
    assert paused.value.envelope["server"] == SERVER_NAME
    assert paused.value.envelope["resource_uri"] == FORM_URI
    action, _note = create_pause_action(paused.value, thread_id=THREAD, user_id=None)
    interaction_id = action.additional_data.tool["mcp_app"]["interaction_id"]
    session_id = paused.value.envelope["tool_result"]["structuredContent"]["form"]["session_id"]

    # 2. The chat UI drives the form through CUGA's API (which reaches the registry over HTTP).
    app = FastAPI()
    app.include_router(routes.router)
    headers = {"X-Thread-ID": THREAD}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://cuga") as chat:
        resource = (
            await chat.post(
                "/api/mcp-apps/resource", json={"interaction_id": interaction_id}, headers=headers
            )
        ).json()
        assert resource["html"] == FORM_HTML and resource["status"] == "pending"
        assert resource["tool_input"] == {"title": "Contact", "fields": ["email", "phone"]}

        denied = await chat.post(
            "/api/mcp-apps/tools/call",
            json={"interaction_id": interaction_id, "name": "plain_tool", "arguments": {"x": 1}},
            headers=headers,
        )
        assert denied.status_code == 403  # a model-only tool is not callable from the app

        values = {"email": "a@example.com", "phone": "555"}
        submitted = await chat.post(
            "/api/mcp-apps/tools/call",
            json={
                "interaction_id": interaction_id,
                "name": "submit_form",
                "arguments": {"session_id": session_id, "values": values},
            },
            headers=headers,
        )
        assert submitted.status_code == 200, submitted.text
        assert submitted.json()["structuredContent"]["values"] == values

        context = await chat.post(
            "/api/mcp-apps/model-context",
            json={
                "interaction_id": interaction_id,
                "structuredContent": submitted.json()["structuredContent"],
            },
            headers=headers,
        )
        assert context.status_code == 200

    # 3. The user finishes the form: the agent resumes with the app's result as the tool's result.
    state = AgentState(input="collect contact details", url="")
    state.chat_messages = [HumanMessage(content="collect contact details")]
    state.hitl_response = ActionResponse(
        action_id=ActionIds.MCP_APP_INTERACTION,
        response_type=ActionType.MCP_APP,
        timestamp=datetime.now(timezone.utc).isoformat(),
        metadata={"interaction_id": interaction_id, "message": "Submitted."},
        additional_data=action.additional_data,
    )
    command = CugaLiteHumanInTheLoopHandler().handle_human_response(state, "CugaLite")
    assert command.goto == "CugaLiteSubgraph"
    last = command.update["chat_messages"][-1]
    observation = last["content"] if isinstance(last, dict) else last.content
    assert '"a@example.com"' in observation and "Submitted." in observation
    assert interaction_store.get(interaction_id).status == "completed"
