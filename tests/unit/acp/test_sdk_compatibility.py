"""Compatibility checks for the official Agent Client Protocol SDK."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from acp import (
    Agent,
    CancelNotification,
    Client,
    InitializeRequest,
    InitializeResponse,
    NewSessionRequest,
    NewSessionResponse,
    PROTOCOL_VERSION,
    PromptRequest,
    PromptResponse,
    SessionNotification,
    connect_to_agent,
    run_agent,
    spawn_agent_process,
    stdio_streams,
)
from acp.schema import (
    AgentCapabilities,
    AgentMessageChunk,
    AllowedOutcome,
    ClientCapabilities,
    DeniedOutcome,
    Implementation,
    PermissionOption,
    PromptCapabilities,
    RequestPermissionRequest,
    RequestPermissionResponse,
    TextContentBlock,
    ToolCallUpdate,
)

pytestmark = pytest.mark.unit


@pytest.mark.unit
def test_official_sdk_public_interfaces_are_importable() -> None:
    """Plans 03–05 depend only on these public runtime and interface exports."""
    runtime_interfaces = (
        Agent,
        Client,
        connect_to_agent,
        run_agent,
        spawn_agent_process,
        stdio_streams,
    )

    assert all(interface is not None for interface in runtime_interfaces)
    assert PROTOCOL_VERSION == 1


@pytest.mark.unit
def test_lifecycle_models_construct_with_stable_v1_fields() -> None:
    """The inbound and outbound adapters rely on these exact lifecycle models."""
    implementation = Implementation(name="cuga", title="CUGA", version="1.0.0")
    client_capabilities = ClientCapabilities(terminal=False)
    agent_capabilities = AgentCapabilities(
        loadSession=False,
        promptCapabilities=PromptCapabilities(image=False, audio=False, embeddedContext=False),
    )
    initialize_request = InitializeRequest(
        protocolVersion=PROTOCOL_VERSION,
        clientCapabilities=client_capabilities,
        clientInfo=implementation,
    )
    initialize_response = InitializeResponse(
        protocolVersion=PROTOCOL_VERSION,
        agentCapabilities=agent_capabilities,
        agentInfo=implementation,
    )
    new_session_request = NewSessionRequest(cwd="/workspace", mcpServers=[])
    new_session_response = NewSessionResponse(sessionId="session-1")
    text = TextContentBlock(type="text", text="hello")
    prompt_request = PromptRequest(sessionId="session-1", prompt=[text])
    prompt_response = PromptResponse(stopReason="end_turn")
    cancel = CancelNotification(sessionId="session-1")

    assert initialize_request.client_info == implementation
    assert initialize_response.agent_info == implementation
    assert initialize_response.agent_capabilities.load_session is False
    assert new_session_request.mcp_servers == []
    assert new_session_response.session_id == "session-1"
    assert prompt_request.prompt == [text]
    assert prompt_response.stop_reason == "end_turn"
    assert cancel.session_id == "session-1"


@pytest.mark.unit
def test_generated_update_and_permission_models_construct() -> None:
    """Stable-v1 update and permission models retain fields used by Plans 03–05."""
    text = TextContentBlock(type="text", text="hello")
    message_update = AgentMessageChunk(sessionUpdate="agent_message_chunk", content=text)
    notification = SessionNotification(sessionId="session-1", update=message_update)
    tool_update = ToolCallUpdate(
        toolCallId="tool-1",
        kind="read",
        status="pending",
        title="Read file",
    )
    option = PermissionOption(
        optionId="allow-once",
        name="Allow once",
        kind="allow_once",
    )
    request = RequestPermissionRequest(
        sessionId="session-1",
        toolCall=tool_update,
        options=[option],
    )
    allowed = RequestPermissionResponse(outcome=AllowedOutcome(outcome="selected", optionId=option.option_id))
    denied = RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

    assert notification.update == message_update
    assert tool_update.tool_call_id == "tool-1"
    assert request.tool_call == tool_update
    assert request.options == [option]
    assert allowed.outcome.option_id == "allow-once"
    assert denied.outcome.outcome == "cancelled"


@pytest.mark.unit
def test_production_code_has_no_superseded_sdk_imports() -> None:
    """The superseded remote-protocol package must not remain in production modules."""
    source_root = Path(__file__).parents[3] / "src"
    superseded_import = "acp" + "_sdk"
    hits = [
        path for path in source_root.rglob("*.py") if superseded_import in path.read_text(encoding="utf-8")
    ]
    assert hits == []


@pytest.mark.unit
def test_base_cuga_import_does_not_eagerly_import_acp() -> None:
    """Installing the optional extra must not add ACP to base CUGA startup."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).parents[3] / "src")
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import cuga; assert 'acp' not in sys.modules",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
