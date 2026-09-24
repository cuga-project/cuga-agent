"""Compatibility checks for the official Agent Client Protocol SDK."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from acp import Agent, Client, PROTOCOL_VERSION, connect_to_agent, run_agent, spawn_agent_process
from acp.schema import (
    AgentCapabilities,
    AgentMessageChunk,
    AllowedOutcome,
    ClientCapabilities,
    DeniedOutcome,
    PermissionOption,
    PromptCapabilities,
    RequestPermissionRequest,
    RequestPermissionResponse,
    TextContentBlock,
)

pytestmark = pytest.mark.unit


@pytest.mark.unit
def test_official_sdk_public_interfaces_are_importable() -> None:
    """Plans 03–05 depend only on these public runtime and interface exports."""
    assert Agent is not None
    assert Client is not None
    assert connect_to_agent is not None
    assert run_agent is not None
    assert spawn_agent_process is not None
    assert PROTOCOL_VERSION == 1


@pytest.mark.unit
def test_generated_content_capability_and_permission_models_construct() -> None:
    """Representative stable-v1 wire models retain the fields later adapters need."""
    text = TextContentBlock(type="text", text="hello")
    agent_capabilities = AgentCapabilities(
        loadSession=False,
        promptCapabilities=PromptCapabilities(image=False, audio=False, embeddedContext=False),
    )
    client_capabilities = ClientCapabilities(terminal=False)
    update = AgentMessageChunk(sessionUpdate="agent_message_chunk", content=text)
    option = PermissionOption(
        optionId="allow-once",
        name="Allow once",
        kind="allow_once",
    )
    request = RequestPermissionRequest(
        sessionId="session-1",
        toolCall={"toolCallId": "tool-1", "title": "Read file"},
        options=[option],
    )
    allowed = RequestPermissionResponse(outcome=AllowedOutcome(outcome="selected", optionId=option.option_id))
    denied = RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

    assert text.text == "hello"
    assert agent_capabilities.load_session is False
    assert client_capabilities.terminal is False
    assert update.content == text
    assert request.options == [option]
    assert allowed.outcome.option_id == "allow-once"
    assert denied.outcome.outcome == "cancelled"


@pytest.mark.unit
def test_production_code_has_no_beeai_sdk_imports() -> None:
    """The superseded BeeAI package must not remain in production modules."""
    source_root = Path(__file__).parents[3] / "src"
    hits = [path for path in source_root.rglob("*.py") if "acp_sdk" in path.read_text(encoding="utf-8")]
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
