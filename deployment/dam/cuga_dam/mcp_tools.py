"""Merge DAM-managed MCP servers and expose their tools to CUGA."""

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


async def _discover_server_tools(config: dict[str, Any]) -> list[Any]:
    """Use CUGA's first-party LangChain MCP adapter for one remote server."""
    from fastmcp import Client
    from fastmcp.client.transports import SSETransport, StreamableHttpTransport
    from langchain.mcp import MCPAdapter

    transport_type = SSETransport if config["transport"] == "sse" else StreamableHttpTransport
    transport = transport_type(config["url"], headers=config["headers"])
    return await MCPAdapter(Client(transport)).list_tools()


async def load_mcp_tools(servers: list[Any]) -> tuple[list[Any], str]:
    """Return uniquely named tools and a prompt mapping DAM names to Python names."""
    connections = server_connections(servers)
    tools: list[Any] = []
    aliases: list[str] = []
    names: set[str] = set()
    for server_name, config in connections.items():
        for tool in await _discover_server_tools(config):
            original = f"mcp__{server_name}__{tool.name}"
            alias = f"mcp__{_identifier(server_name)}__{_identifier(tool.name)}"
            if alias in names:
                raise ValueError("colliding ACP MCP tool names")
            names.add(alias)
            copy = tool.model_copy()
            copy.name = alias
            copy.description = f"{tool.description or ''}\nOriginal MCP tool: {original}."
            tools.append(copy)
            aliases.append(f"{original} -> {alias}")
    instructions = "MCP tools use these CUGA names: " + "; ".join(aliases) + "." if aliases else ""
    return tools, instructions
