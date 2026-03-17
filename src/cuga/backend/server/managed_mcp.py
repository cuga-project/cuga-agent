"""Managed MCP config: path, bootstrap YAML, and tools list → YAML for registry."""

import os
import yaml
from typing import Any

from cuga.config import DBS_DIR

MANAGED_MCP_FILENAME = "managed_mcp_servers.yaml"


def get_managed_mcp_path() -> str:
    os.makedirs(DBS_DIR, exist_ok=True)
    return os.path.join(DBS_DIR, MANAGED_MCP_FILENAME)


BOOTSTRAP_YAML = {
    "services": [],
    "mcpServers": {
        "filesystem": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "./cuga_workspace"],
            "transport": "stdio",
            "description": "File system operations for workspace management",
        }
    },
}


def ensure_managed_mcp_file_exists(path: str | None = None) -> str:
    """Create managed MCP YAML with bootstrap content if missing. Return path."""
    p = path or get_managed_mcp_path()
    if not os.path.exists(p):
        with open(p, "w") as f:
            yaml.dump(BOOTSTRAP_YAML, f, default_flow_style=False, sort_keys=False)
    return p


def tools_to_registry_yaml(tools: list[dict[str, Any]]) -> dict[str, Any]:
    """Convert manage-config tools list to registry YAML (services + mcpServers)."""
    services: list[dict[str, Any]] = []
    mcp_servers: dict[str, dict[str, Any]] = {}
    for t in tools or []:
        name = t.get("name") or "unknown"
        typ = (t.get("type") or "mcp").lower()
        entry: dict[str, Any] = {
            "description": t.get("description") or "",
        }
        if t.get("auth"):
            entry["auth"] = t["auth"]
        include = t.get("include")
        if isinstance(include, list) and len(include) > 0:
            entry["include"] = include
        if typ == "openapi" and t.get("url"):
            entry["url"] = t["url"]
            services.append({name: entry})
        else:
            if t.get("url"):
                entry["url"] = t["url"]
                entry["transport"] = t.get("transport") or "sse"
            if t.get("command"):
                entry["command"] = t["command"]
                entry["args"] = t.get("args") or []
                entry["transport"] = t.get("transport") or "stdio"
            if t.get("env"):
                entry["env"] = t["env"]
            mcp_servers[name] = entry
    return {"services": services, "mcpServers": mcp_servers}


def read_managed_mcp_servers(path: str | None = None) -> dict[str, dict[str, Any]]:
    """Read managed MCP YAML and return mcpServers dict (name -> entry with command/args/transport)."""
    p = path or get_managed_mcp_path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p) as f:
            data = yaml.safe_load(f) or {}
        return data.get("mcpServers") or {}
    except Exception:
        return {}


def _merge_existing_mcp_servers(new_data: dict[str, Any], path: str) -> None:
    """In-place: fill in command/args/transport from existing YAML when new entry has no command."""
    if not os.path.exists(path):
        return
    try:
        with open(path) as f:
            existing = yaml.safe_load(f) or {}
    except Exception:
        return
    existing_mcp = existing.get("mcpServers") or {}
    for name, entry in (new_data.get("mcpServers") or {}).items():
        if not entry.get("command") and name in existing_mcp:
            existing_entry = existing_mcp[name]
            if isinstance(existing_entry, dict):
                for key in ("command", "args", "transport", "env", "description"):
                    if key in existing_entry and key not in entry:
                        entry[key] = existing_entry[key]
    existing_svc = existing.get("services") or []
    new_services = new_data.get("services") or []
    for svc in new_services:
        if not isinstance(svc, dict):
            continue
        for name, entry in svc.items():
            if not isinstance(entry, dict) or entry.get("url"):
                continue
            for es in existing_svc:
                if isinstance(es, dict) and name in es and isinstance(es[name], dict):
                    if es[name].get("url"):
                        entry["url"] = es[name]["url"]
                    break
    return None


def write_managed_mcp_yaml(config: dict[str, Any], path: str | None = None) -> str:
    """Write tools from manage config to managed MCP YAML. Returns path written."""
    p = path or get_managed_mcp_path()
    tools = (config or {}).get("tools") if isinstance(config, dict) else None
    data = tools_to_registry_yaml(tools) if tools else BOOTSTRAP_YAML
    _merge_existing_mcp_servers(data, p)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
    return p


# ============================================================================
# Database-backed MCP management (by agent_id) - reads from agent config
# ============================================================================


async def get_tools_from_agent_config(agent_id: str) -> list[dict[str, Any]]:
    """Get all tools for an agent from their config in database."""
    from cuga.backend.server.config_store import get_agent_tools

    return await get_agent_tools(agent_id)


async def get_registry_yaml_from_agent_config(agent_id: str) -> dict[str, Any]:
    """Convert agent config tools to registry YAML format."""
    tools = await get_tools_from_agent_config(agent_id)
    return tools_to_registry_yaml(tools)


async def write_registry_yaml_from_agent_config(agent_id: str, path: str | None = None) -> str:
    """Write registry YAML from agent config tools."""
    p = path or get_managed_mcp_path()
    data = await get_registry_yaml_from_agent_config(agent_id)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
    return p
