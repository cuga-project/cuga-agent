"""Tests for ACP manifest formatting and prompt preparation (Task 3.4).

Covers:
- Manifest description appears in the supervisor prompt.
- Configured description is used when discovery fails.
- ACP tool schema has only `task`.
- A2A variable behavior is unchanged.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ACP_MODULE = "cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol"
_A2A_MODULE = "cuga.backend.cuga_graph.nodes.cuga_supervisor.a2a_protocol"


def _make_manifest(name="remote-agent", description="Summarises documents", input_ct=None, output_ct=None):
    """Build a minimal AgentManifest-like object."""
    from acp_sdk.models import AgentManifest

    return AgentManifest(
        name=name,
        description=description,
        input_content_types=input_ct or ["text/plain"],
        output_content_types=output_ct or ["text/plain"],
    )


def _acp_agent_config(description=None, endpoint="https://acp.example.com", agent_name="remote-agent"):
    """Build the dict shape that supervisor_config produces for an ACP agent."""
    cfg = {
        "type": "external",
        "config": {
            "acp_protocol": {
                "endpoint": endpoint,
                "agent_name": agent_name,
                "timeout": 30,
                "verify_tls": True,
            }
        },
    }
    if description is not None:
        cfg["description"] = description
    return cfg


def _a2a_agent_config(description="A2A agent description"):
    return {
        "type": "external",
        "description": description,
        "config": {"a2a_protocol": {"endpoint": "http://a2a.test", "transport": "http"}},
    }


def _make_adapter(agents: dict):
    """Minimal adapter stub compatible with prepare_agents_and_prompt."""
    adapter = MagicMock()
    adapter._agents = agents
    adapter._agent_tools_context = {}
    adapter._special_instructions = None
    adapter._tool_provider = None
    adapter._static_prompt = "You are a supervisor."
    adapter._plan_approval = False
    adapter.get_metadata.return_value = None
    return adapter


def _make_state():
    return SimpleNamespace(
        supervisor_chat_messages=[MagicMock(), MagicMock()],
        sub_task=None,
        script=None,
        task_todos=None,
        tool_calls_used_thread=0,
        thread_id="test-thread",
    )


async def _run_prepare(adapter):
    """Invoke prepare_agents_and_prompt with a minimal state and return the Command update."""
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.prepare_agents_and_prompt import (
        create_prepare_agents_and_prompt_node,
    )

    node = create_prepare_agents_and_prompt_node(adapter)
    state = _make_state()
    command = await node(state)
    return command.update


# ---------------------------------------------------------------------------
# format_manifest_for_prompt unit tests
# ---------------------------------------------------------------------------


def test_format_manifest_for_prompt_includes_name_and_description():
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import format_manifest_for_prompt

    manifest = _make_manifest(name="my-agent", description="Does cool things")
    result = format_manifest_for_prompt(manifest)
    assert "my-agent" in result
    assert "Does cool things" in result


def test_format_manifest_for_prompt_includes_content_types():
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import format_manifest_for_prompt

    manifest = _make_manifest(input_ct=["text/plain"], output_ct=["application/json"])
    result = format_manifest_for_prompt(manifest)
    assert "text/plain" in result
    assert "application/json" in result


def test_format_manifest_for_prompt_empty_manifest_returns_fallback():
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import format_manifest_for_prompt

    class _Empty:
        name = None
        description = None
        input_content_types = []
        output_content_types = []

    assert format_manifest_for_prompt(_Empty()) == "ACP agent"


# ---------------------------------------------------------------------------
# prepare_agents_and_prompt ACP integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acp_manifest_description_appears_in_supervisor_prompt():
    """Manifest description fetched from the remote server must appear in prepared_prompt."""
    manifest = _make_manifest(description="Summarises documents for you")
    adapter = _make_adapter({"doc-agent": _acp_agent_config()})

    with (
        patch(f"{_ACP_MODULE}.HAS_ACP_SDK", True),
        patch(f"{_ACP_MODULE}.fetch_agent_manifest", AsyncMock(return_value=manifest)),
    ):
        update = await _run_prepare(adapter)

    assert "Summarises documents for you" in update["prepared_prompt"]


@pytest.mark.asyncio
async def test_acp_configured_description_used_when_discovery_fails():
    """When manifest fetch raises, the configured description must appear in the prompt."""
    adapter = _make_adapter({"doc-agent": _acp_agent_config(description="Fallback description")})

    with (
        patch(f"{_ACP_MODULE}.HAS_ACP_SDK", True),
        patch(
            f"{_ACP_MODULE}.fetch_agent_manifest",
            AsyncMock(side_effect=RuntimeError("unreachable")),
        ),
    ):
        update = await _run_prepare(adapter)

    assert "Fallback description" in update["prepared_prompt"]


@pytest.mark.asyncio
async def test_acp_tool_schema_has_only_task():
    """The ACP delegation tool must expose only `task: str` — no variables parameter."""
    manifest = _make_manifest(description="ACP agent")
    # Use a plain name so the delegate tool name is predictable (no hyphens)
    adapter = _make_adapter({"acpagent": _acp_agent_config(agent_name="acpagent")})

    with (
        patch(f"{_ACP_MODULE}.HAS_ACP_SDK", True),
        patch(f"{_ACP_MODULE}.fetch_agent_manifest", AsyncMock(return_value=manifest)),
    ):
        update = await _run_prepare(adapter)

    prompt = update["prepared_prompt"]
    assert "task: str" in prompt
    # The ACP tool block must NOT expose a `variables` parameter.
    # Locate the section of the prompt covering this tool and check within it.
    tool_start = prompt.find("delegate_to_acpagent")
    assert tool_start != -1, "ACP delegation tool not found in prompt"
    snippet = prompt[tool_start : tool_start + 300]
    assert "variables" not in snippet


@pytest.mark.asyncio
async def test_a2a_variables_behavior_unchanged():
    """A2A agents with pass_variables_a2a=True must still expose the variables parameter."""
    adapter = _make_adapter({"a2a-worker": _a2a_agent_config(description="A2A agent")})

    fake_card = MagicMock()
    fake_card.name = "a2a-worker"
    fake_card.description = "A2A agent"
    fake_card.capabilities = None
    fake_card.skills = None

    with (
        patch(f"{_A2A_MODULE}.HAS_A2A_SDK", True),
        patch(f"{_A2A_MODULE}.fetch_agent_card", AsyncMock(return_value=fake_card)),
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_supervisor.nodes.prepare_agents_and_prompt"
            ".settings.supervisor.pass_variables_a2a",
            True,
        ),
    ):
        update = await _run_prepare(adapter)

    assert "variables" in update["prepared_prompt"]
