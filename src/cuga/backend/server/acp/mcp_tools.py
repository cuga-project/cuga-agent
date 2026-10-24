"""Adapt ACP-provided MCP servers to CUGA's LangChain tool provider."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any


def _identifier(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9_]", "_", value)
    return f"_{result}" if result and result[0].isdigit() else result


def effective_mcp_servers(servers: list[Any], config_path: Path | None = None) -> list[Any]:
    """Merge managed HTTP/SSE servers, preserving ACP's per-session settings."""
    result = list(servers)
    supplied_names = {getattr(server, "name", None) for server in result}

    path = config_path or Path.home() / ".mcp.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return result
    except (OSError, ValueError) as exc:
        raise ValueError("invalid DAM MCP configuration") from exc
    entries = config.get("mcpServers") if isinstance(config, dict) else None
    if not isinstance(entries, dict):
        raise ValueError("invalid DAM MCP configuration")
    from acp.schema import HttpHeader, HttpMcpServer, SseMcpServer

    for name, entry in entries.items():
        if name in supplied_names:
            continue
        if not name or not isinstance(entry, dict):
            raise ValueError("invalid managed MCP server")
        kind = entry.get("type")
        url = entry.get("url")
        raw_headers = entry.get("headers", {})
        if kind not in {"http", "sse"} or not isinstance(url, str) or not url:
            raise ValueError("invalid managed MCP server")
        if not isinstance(raw_headers, dict) or any(
            not isinstance(key, str) or not isinstance(value, str) for key, value in raw_headers.items()
        ):
            raise ValueError("invalid managed MCP headers")
        headers = [HttpHeader(name=key, value=value) for key, value in raw_headers.items()]
        server_class = HttpMcpServer if kind == "http" else SseMcpServer
        result.append(server_class(type=kind, name=name, url=url, headers=headers))
    return result


def server_connections(servers: list[Any]) -> dict[str, dict[str, Any]]:
    """Use only ACP-advertised remote transports; never launch server commands."""
    connections: dict[str, dict[str, Any]] = {}
    for server in servers:
        kind = getattr(server, "type", None)
        if kind not in {"http", "sse"}:
            raise ValueError("unsupported ACP MCP server transport")
        name = getattr(server, "name", None)
        url = getattr(server, "url", None)
        if not isinstance(name, str) or not name or not isinstance(url, str):
            raise ValueError("invalid ACP MCP server")
        if name in connections:
            raise ValueError("duplicate ACP MCP server name")
        headers = {header.name: header.value for header in getattr(server, "headers", [])}
        connections[name] = {
            "transport": "streamable_http" if kind == "http" else "sse",
            "url": url,
            "headers": headers,
        }
    return connections


async def load_mcp_tools(servers: list[Any]) -> tuple[list[Any], str]:
    """Return uniquely named tools and a prompt mapping DAM names to Python names."""
    from langchain_mcp_adapters.client import MultiServerMCPClient

    connections = server_connections(servers)
    client = MultiServerMCPClient(connections)
    tools: list[Any] = []
    aliases: list[str] = []
    names: set[str] = set()
    for server_name in connections:
        for tool in await client.get_tools(server_name=server_name):
            original = f"mcp__{server_name}__{tool.name}"
            alias = f"mcp__{_identifier(server_name)}__{_identifier(tool.name)}"
            if alias in names:
                raise ValueError("colliding ACP MCP tool names")
            names.add(alias)
            copy = tool.model_copy()
            copy.name = alias
            copy.description = f"{tool.description or ''}\nDAM tool: {original}."
            tools.append(copy)
            aliases.append(f"{original} -> {alias}")
    instructions = "DAM MCP tools use these CUGA names: " + "; ".join(aliases) + "." if aliases else ""
    if "mcp__platform_outbound__reply" in names:
        instructions += (
            " For Slack turns, plain answer text is not delivered. Follow the prompt's "
            "how-to-respond block using the CUGA tool names above: call "
            "mcp__platform_outbound__reply with threadTs when replying."
        )
        if "mcp__platform_outbound__no_reply_needed" in names:
            instructions += " Call mcp__platform_outbound__no_reply_needed when silence is appropriate."
        instructions += " Do not end a Slack turn with only plain answer text."
    if "mcp__platform_outbound__send_channel_message" in names:
        instructions += (
            " When explicitly asked to send a new top-level message to a connected Slack channel, "
            "call mcp__platform_outbound__send_channel_message with channel='slack' "
            "and text set to the message."
        )
        if "mcp__platform_outbound__describe_channel" in names:
            instructions += (
                " If the request names a Slack channel, first call "
                "mcp__platform_outbound__describe_channel with channel='slack'. "
                "Match the requested name (ignoring a leading '#') to exactly one "
                "chats[].title in the result, then pass that chat's id as chatId. "
                "If there is no unique match, ask for clarification without sending."
            )
        else:
            instructions += " For a named channel, set chatId to its ID."
        instructions += (
            " For the agent's default chat, omit chatId. "
            "Use reply with threadTs for a thread response instead."
        )
    return tools, instructions
