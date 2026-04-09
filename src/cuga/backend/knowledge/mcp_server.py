"""Knowledge MCP server — 7 tools exposed to the agent.

Supports both HTTP and stdio transports (configurable via knowledge_settings.toml).
Forwards requests to the backend via HTTP.
agent_id is auto-discovered from the backend's /api/agent/context endpoint.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import httpx
from fastmcp import FastMCP

logger = logging.getLogger("cuga.knowledge")

# --- Configuration ---


def _resolve_env(key: str, default: str) -> str:
    """Resolve env var, handling the case where the value is itself an env var name.

    When the MCP subprocess receives env like CUGA_INTERNAL_TOKEN_FILE=CUGA_INTERNAL_TOKEN_FILE
    (literal string instead of a path), detect this circular reference and fall back to default.
    """
    val = os.getenv(key, default)
    # Circular: env var value equals its own key name → unresolved, use default
    if val == key:
        return default
    # If the value looks like an unresolved env var name (no path separators,
    # all uppercase+underscore), try resolving it from the real environment
    if val and "/" not in val and "\\" not in val and val == val.upper() and "_" in val:
        resolved = os.getenv(val)
        if resolved and resolved != val:
            return resolved
        return default
    return val


CUGA_BACKEND_URL = _resolve_env("CUGA_BACKEND_URL", "http://localhost:7860").rstrip("/")
CUGA_INTERNAL_TOKEN_FILE = _resolve_env(
    "CUGA_INTERNAL_TOKEN_FILE", str(Path.cwd() / ".cuga" / ".internal_token")
)

_cached_token: str | None = None


def _get_token() -> str:
    """Read internal token from file (lazy, with retry on 401)."""
    global _cached_token
    if _cached_token:
        return _cached_token
    _cached_token = _read_token_file()
    return _cached_token


def _reload_token() -> str:
    """Force re-read token from file (called on 401)."""
    global _cached_token
    _cached_token = _read_token_file()
    return _cached_token


def _read_token_file() -> str:
    import time

    for attempt in range(3):
        try:
            with open(CUGA_INTERNAL_TOKEN_FILE) as f:
                return f.read().strip()
        except FileNotFoundError:
            if attempt < 2:
                time.sleep(1)
    raise RuntimeError(f"Knowledge backend not ready — token file not found: {CUGA_INTERNAL_TOKEN_FILE}")


# --- HTTP client ---

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=CUGA_BACKEND_URL,
            timeout=httpx.Timeout(60.0, connect=10.0),
            trust_env=False,
        )
    return _client


async def _request(method: str, path: str, **kwargs) -> httpx.Response:
    """Make authenticated request with 401 retry."""
    client = _get_client()
    headers = kwargs.pop("headers", {})
    headers["X-Internal-Token"] = _get_token()

    resp = await client.request(method, path, headers=headers, **kwargs)

    # Retry once on 401 (token may have rotated after backend restart)
    if resp.status_code == 401:
        headers["X-Internal-Token"] = _reload_token()
        resp = await client.request(method, path, headers=headers, **kwargs)

    resp.raise_for_status()
    return resp


# --- Agent ID discovery ---


def _get_agent_id() -> str:
    """Discover current agent_id from the backend API.

    Called per tool invocation (not cached) so agent switches are reflected immediately.
    The /api/agent/context endpoint is lightweight — returns a string from memory.
    """
    try:
        resp = httpx.get(
            f"{CUGA_BACKEND_URL}/api/agent/context",
            headers={"X-Internal-Token": _get_token()},
            timeout=5.0,
        )
        if resp.status_code == 200:
            return resp.json().get("agent_id", "cuga-default")
    except Exception:
        pass
    return "cuga-default"


def _identity_headers(agent_id: str = "", thread_id: str = "") -> dict[str, str]:
    """Build identity headers. Uses explicit agent_id if provided, else auto-discovers."""
    aid = agent_id if agent_id else _get_agent_id()
    headers = {"X-Agent-ID": aid}
    if thread_id:
        headers["X-Thread-ID"] = thread_id
    return headers


# --- MCP Server ---

mcp = FastMCP(
    "Knowledge",
    instructions="Knowledge service for semantic document search and management",
)


@mcp.tool()
async def search_knowledge(
    query: str,
    scope: str = "agent",
    agent_id: str = "",
    thread_id: str = "",
) -> dict[str, Any]:
    """Search documents in the knowledge base.

    Use only scopes enabled for the current agent. Disabled scopes will fail.
    When using scope="session", thread_id is required to identify the conversation.
    Returns results with text, filename, and page number.
    """
    resp = await _request(
        "POST",
        "/api/knowledge/search",
        headers=_identity_headers(agent_id, thread_id),
        json={"scope": scope, "query": query},
    )
    return resp.json()


@mcp.tool()
async def ingest_knowledge(
    file_path: str,
    scope: str = "agent",
    replace_duplicates: bool = True,
    agent_id: str = "",
    thread_id: str = "",
) -> dict[str, Any]:
    """Upload a document file to the knowledge base.

    Supports PDF, DOCX, XLSX, PPTX, HTML, Markdown, images (with OCR), and more.
    Use only scopes enabled for the current agent.
    When using scope="session", thread_id is required.
    """
    import os

    if not os.path.exists(file_path):
        return {"error": f"File not found: {file_path}"}

    with open(file_path, "rb") as f:
        resp = await _request(
            "POST",
            "/api/knowledge/documents",
            headers=_identity_headers(agent_id, thread_id),
            files={"files": (os.path.basename(file_path), f)},
            data={"scope": scope, "replace_duplicates": str(replace_duplicates).lower()},
        )
    return resp.json()


@mcp.tool()
async def ingest_knowledge_url(
    url: str,
    scope: str = "agent",
    agent_id: str = "",
    thread_id: str = "",
) -> dict[str, Any]:
    """Ingest a document from a URL into the knowledge base.

    Crawls the URL and indexes its content for search.
    Use only scopes enabled for the current agent. When using scope="session", thread_id is required.
    """
    resp = await _request(
        "POST",
        "/api/knowledge/documents/url",
        headers=_identity_headers(agent_id, thread_id),
        json={"scope": scope, "url": url},
    )
    return resp.json()


@mcp.tool()
async def list_knowledge_documents(
    scope: str = "agent",
    agent_id: str = "",
    thread_id: str = "",
) -> dict[str, Any]:
    """List all documents in the knowledge base.

    Use only scopes enabled for the current agent.
    When using scope="session", thread_id is required to identify the conversation.
    """
    resp = await _request(
        "GET",
        "/api/knowledge/documents",
        headers=_identity_headers(agent_id, thread_id),
        params={"scope": scope},
    )
    return resp.json()


@mcp.tool()
async def delete_knowledge_document(
    filename: str,
    scope: str = "agent",
    agent_id: str = "",
    thread_id: str = "",
) -> dict[str, Any]:
    """Delete a document from the knowledge base by filename.

    Use only scopes enabled for the current agent. When using scope="session", thread_id is required.
    """
    resp = await _request(
        "DELETE",
        "/api/knowledge/documents",
        headers=_identity_headers(agent_id, thread_id),
        json={"scope": scope, "filename": filename},
    )
    return resp.json()


@mcp.tool()
async def get_ingestion_status(
    task_id: str,
    agent_id: str = "",
) -> dict[str, Any]:
    """Check the status of a document ingestion task.

    Returns progress information including per-file status.
    """
    resp = await _request(
        "GET",
        f"/api/knowledge/tasks/{task_id}",
        headers=_identity_headers(agent_id),
    )
    return resp.json()


@mcp.tool()
async def get_knowledge_status(
    agent_id: str = "",
) -> dict[str, Any]:
    """Check if the knowledge service is healthy and get current settings."""
    resp = await _request(
        "GET",
        "/api/knowledge/health",
        headers=_identity_headers(agent_id),
    )
    return resp.json()


# --- Entry point ---


def run_http(host: str = "127.0.0.1", port: int = 8113):
    """Start the knowledge MCP server in HTTP mode (called from main process)."""
    mcp.run(transport="http", host=host, port=port)


if __name__ == "__main__":
    transport = os.getenv("CUGA_KNOWLEDGE_TRANSPORT", "stdio")
    if transport == "http":
        port = int(os.getenv("CUGA_KNOWLEDGE_PORT", "8113"))
        run_http(host="127.0.0.1", port=port)
    else:
        mcp.run(transport="stdio")
