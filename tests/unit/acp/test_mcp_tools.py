"""ACP MCP servers become callable CUGA tools with DAM-compatible names."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from acp.schema import HttpHeader, HttpMcpServer
from langchain_core.tools import StructuredTool

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


@pytest.mark.unit
async def test_platform_tools_are_callable_with_safe_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    from cuga.backend.server.acp.mcp_tools import load_mcp_tools

    seen: dict = {}

    async def reply(threadTs: str, text: str) -> str:
        """Reply to a Slack thread."""
        seen["reply"] = (threadTs, text)
        return "posted"

    async def describe_channel(channel: str) -> dict:
        """List available Slack chats."""
        seen["describe"] = channel
        return {"chats": [{"id": "C123", "title": "team-updates"}]}

    async def send_channel_message(channel: str, text: str, chatId: str | None = None) -> str:
        """Send a new top-level Slack channel message."""
        seen["send"] = (channel, chatId, text)
        return "Message sent"

    async def create_schedule(name: str) -> str:
        """Create a schedule unrelated to chat."""
        seen["schedule"] = name
        return name

    class FakeClient:
        def __init__(self, connections):
            seen["connections"] = connections

        async def get_tools(self, *, server_name: str):
            assert server_name == "platform-outbound"
            return [
                StructuredTool.from_function(coroutine=reply),
                StructuredTool.from_function(coroutine=describe_channel),
                StructuredTool.from_function(coroutine=send_channel_message),
                StructuredTool.from_function(coroutine=create_schedule),
            ]

    monkeypatch.setattr("langchain_mcp_adapters.client.MultiServerMCPClient", FakeClient)
    servers = [
        HttpMcpServer(
            type="http",
            name="platform-outbound",
            url="http://platform.local/mcp",
            headers=[HttpHeader(name="x-test", value="token-value")],
        )
    ]

    tools, instructions = await load_mcp_tools(servers)

    assert seen["connections"]["platform-outbound"] == {
        "transport": "streamable_http",
        "url": "http://platform.local/mcp",
        "headers": {"x-test": "token-value"},
    }
    assert [tool.name for tool in tools] == [
        "mcp__platform_outbound__reply",
        "mcp__platform_outbound__describe_channel",
        "mcp__platform_outbound__send_channel_message",
        "mcp__platform_outbound__create_schedule",
    ]
    assert "mcp__platform-outbound__reply -> mcp__platform_outbound__reply" in instructions
    assert (
        "mcp__platform-outbound__describe_channel -> mcp__platform_outbound__describe_channel" in instructions
    )
    assert (
        "mcp__platform-outbound__send_channel_message -> mcp__platform_outbound__send_channel_message"
        in instructions
    )
    assert "new top-level message" in instructions
    assert "chats[].title" in instructions
    assert "pass that chat's id as chatId" in instructions
    assert "For the agent's default chat, omit chatId" in instructions
    assert "Do not end a Slack turn with only plain answer text" in instructions
    assert "token-value" not in instructions
    assert (
        "mcp__platform-outbound__create_schedule -> mcp__platform_outbound__create_schedule" in instructions
    )
    assert await tools[0].ainvoke({"threadTs": "123.456", "text": "Hello"}) == "posted"
    assert seen["reply"] == ("123.456", "Hello")
    assert await tools[1].ainvoke({"channel": "slack"}) == {
        "chats": [{"id": "C123", "title": "team-updates"}]
    }
    assert seen["describe"] == "slack"
    assert (
        await tools[2].ainvoke({"channel": "slack", "chatId": "C123", "text": "Hello channel"})
        == "Message sent"
    )
    assert seen["send"] == ("slack", "C123", "Hello channel")
    assert await tools[2].ainvoke({"channel": "slack", "text": "Hello default chat"}) == "Message sent"
    assert seen["send"] == ("slack", None, "Hello default chat")
    assert await tools[3].ainvoke({"name": "Daily report"}) == "Daily report"
    assert seen["schedule"] == "Daily report"


@pytest.mark.unit
async def test_tools_from_other_managed_servers_remain_callable(monkeypatch: pytest.MonkeyPatch) -> None:
    from cuga.backend.server.acp.mcp_tools import load_mcp_tools

    async def search(query: str) -> str:
        """Search a connected knowledge base."""
        return f"Found: {query}"

    original = StructuredTool.from_function(coroutine=search)

    class FakeClient:
        def __init__(self, connections):
            assert set(connections) == {"knowledge-bases", "connected-service"}

        async def get_tools(self, *, server_name: str):
            return [original]

    monkeypatch.setattr("langchain_mcp_adapters.client.MultiServerMCPClient", FakeClient)
    servers = [
        HttpMcpServer(type="http", name=name, url=f"http://platform.local/{name}", headers=[])
        for name in ("knowledge-bases", "connected-service")
    ]

    tools, instructions = await load_mcp_tools(servers)

    assert [tool.name for tool in tools] == [
        "mcp__knowledge_bases__search",
        "mcp__connected_service__search",
    ]
    assert original.name == "search"
    assert "mcp__knowledge-bases__search -> mcp__knowledge_bases__search" in instructions
    for tool in tools:
        assert await tool.ainvoke({"query": "DAM"}) == "Found: DAM"


@pytest.mark.unit
def test_all_managed_servers_are_added_when_acp_omits_them(tmp_path) -> None:
    from cuga.backend.server.acp.mcp_tools import effective_mcp_servers, server_connections

    config = tmp_path / ".mcp.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "platform-outbound": {
                        "type": "http",
                        "url": "http://platform.local/mcp",
                        "headers": {"Authorization": "test-secret"},
                    },
                    "knowledge-bases": {"type": "http", "url": "http://platform.local/kb"},
                    "connected-service": {"type": "sse", "url": "http://service.local/sse"},
                }
            }
        ),
        encoding="utf-8",
    )
    servers = effective_mcp_servers([], config)

    assert server_connections(servers) == {
        "platform-outbound": {
            "transport": "streamable_http",
            "url": "http://platform.local/mcp",
            "headers": {"Authorization": "test-secret"},
        },
        "knowledge-bases": {
            "transport": "streamable_http",
            "url": "http://platform.local/kb",
            "headers": {},
        },
        "connected-service": {
            "transport": "sse",
            "url": "http://service.local/sse",
            "headers": {},
        },
    }


@pytest.mark.unit
def test_acp_server_takes_precedence_without_skipping_other_managed_servers(tmp_path) -> None:
    from cuga.backend.server.acp.mcp_tools import effective_mcp_servers, server_connections

    server = HttpMcpServer(
        type="http",
        name="platform-outbound",
        url="http://acp.local/mcp",
        headers=[HttpHeader(name="x-platform-session-ref", value="session-ref")],
    )
    config = tmp_path / ".mcp.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "platform-outbound": {"type": "http", "url": "http://managed.local/mcp"},
                    "knowledge-bases": {"type": "http", "url": "http://managed.local/kb"},
                }
            }
        ),
        encoding="utf-8",
    )
    servers = effective_mcp_servers([server], config)

    assert servers[0] is server
    assert server_connections(servers) == {
        "platform-outbound": {
            "transport": "streamable_http",
            "url": "http://acp.local/mcp",
            "headers": {"x-platform-session-ref": "session-ref"},
        },
        "knowledge-bases": {
            "transport": "streamable_http",
            "url": "http://managed.local/kb",
            "headers": {},
        },
    }


@pytest.mark.unit
def test_missing_managed_config_does_not_add_a_server(tmp_path) -> None:
    from cuga.backend.server.acp.mcp_tools import effective_mcp_servers

    assert effective_mcp_servers([], tmp_path / "missing.json") == []


@pytest.mark.unit
def test_invalid_managed_outbound_transport_is_rejected(tmp_path) -> None:
    from cuga.backend.server.acp.mcp_tools import effective_mcp_servers

    config = tmp_path / ".mcp.json"
    config.write_text(
        json.dumps({"mcpServers": {"platform-outbound": {"type": "stdio", "command": "sh"}}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="invalid managed MCP server"):
        effective_mcp_servers([], config)


@pytest.mark.unit
async def test_stdio_mcp_server_is_rejected_without_running_a_command() -> None:
    from cuga.backend.server.acp.mcp_tools import server_connections

    with pytest.raises(ValueError, match="unsupported ACP MCP server transport"):
        server_connections([SimpleNamespace(type="stdio", name="unsafe", command="sh")])
