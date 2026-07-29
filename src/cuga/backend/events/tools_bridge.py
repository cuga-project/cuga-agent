"""Tool binding for the ReactRuntime — built-in LangChain tools + MCP servers.

Ported from event-agent-ap ``agent/tools.py``. Imports langchain lazily-at-module-top
(it's a CUGA dependency), so this is only imported by ``ReactRuntime`` — never by the
dependency-light core. Returns one merged ``list[BaseTool]`` for ``create_react_agent``.
"""

from __future__ import annotations

import logging

log = logging.getLogger("cuga.events.tools")


def _builtins():
    import httpx
    from langchain_core.tools import tool

    @tool
    def current_time() -> str:
        """Return the current UTC time in ISO-8601 format."""
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()

    @tool
    def http_get(url: str) -> str:
        """HTTP GET a URL. Returns 'status=<code> body=<first 800 chars>'."""
        try:
            r = httpx.get(url, timeout=15, follow_redirects=True)
            return f"status={r.status_code} body={r.text[:800]}"
        except Exception as e:  # noqa: BLE001
            return f"error: {e}"

    @tool
    def arxiv_search(query: str, max_results: int = 5) -> str:
        """Search arXiv for recent papers matching a query. Returns title lines."""
        import re
        try:
            r = httpx.get("http://export.arxiv.org/api/query",
                          params={"search_query": f"all:{query}", "max_results": max_results,
                                  "sortBy": "submittedDate", "sortOrder": "descending"},
                          timeout=20)
            titles = re.findall(r"<title>(.*?)</title>", r.text, re.S)[1:]
            return "\n".join(f"- {t.strip()}" for t in titles[:max_results]) or "no results"
        except Exception as e:  # noqa: BLE001
            return f"error: {e}"

    return {"current_time": current_time, "http_get": http_get, "arxiv_search": arxiv_search}


async def load_mcp_tools(mcp_servers: dict) -> list:
    """Load tools from MCP servers via langchain-mcp-adapters. Returns [] on failure."""
    if not mcp_servers:
        return []
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
        client = MultiServerMCPClient(mcp_servers)
        tools = await client.get_tools()
        log.info("loaded %d MCP tools from %s", len(tools), list(mcp_servers))
        return tools
    except Exception:  # noqa: BLE001
        log.exception("failed to load MCP tools from %s", list(mcp_servers))
        return []


async def build_tools(builtin_names: list, mcp_servers: dict | None = None,
                      tool_filter: list | None = None) -> list:
    """Merge built-in tools + MCP-server tools into one list."""
    builtins = _builtins()
    tools = [builtins[n] for n in (builtin_names or []) if n in builtins]
    mcp_tools = await load_mcp_tools(mcp_servers or {})
    if tool_filter:
        allow = set(tool_filter)
        mcp_tools = [t for t in mcp_tools if t.name in allow]
    tools.extend(mcp_tools)
    return tools
