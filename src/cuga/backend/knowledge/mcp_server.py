"""Knowledge MCP server — 7 tools exposed to the agent.

Supports both HTTP and stdio transports (configurable via knowledge_settings.toml).
Forwards requests to the backend via HTTP.
Run identity is supplied through authenticated MCP request metadata.
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path
from typing import Any

import httpx
from fastmcp import Context, FastMCP

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.filesystem.paths import (
    VIRTUAL_WORKSPACE_ROOT,
    resolve_workspace_path,
)
from cuga.backend.knowledge.internal_token import read_token_file, resolve_env

logger = logging.getLogger("cuga.knowledge")

# --- Configuration ---


_resolve_env = resolve_env


def _default_backend_url() -> str:
    require_https = os.getenv("DYNACONF_AUTH__REQUIRE_HTTPS", "").lower() in ("true", "1", "yes", "on")
    ssl_enabled = bool(os.getenv("SSL_KEYFILE", "").strip() and os.getenv("SSL_CERTFILE", "").strip())
    scheme = "https" if require_https or ssl_enabled else "http"
    return f"{scheme}://localhost:7860"


CUGA_BACKEND_URL = _resolve_env("CUGA_BACKEND_URL", _default_backend_url()).rstrip("/")
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
    return read_token_file(CUGA_INTERNAL_TOKEN_FILE)


# --- HTTP client ---

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=CUGA_BACKEND_URL,
            timeout=httpx.Timeout(60.0, connect=10.0),
            verify=False,
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


_LEGACY_VIRTUAL_ROOTS = ("/tmp", "/private/tmp")


def _is_allowed_virtual_absolute(posix_path: str) -> bool:
    if posix_path == VIRTUAL_WORKSPACE_ROOT or posix_path.startswith(f"{VIRTUAL_WORKSPACE_ROOT}/"):
        return True
    return any(
        posix_path == legacy or posix_path.startswith(f"{legacy}/") for legacy in _LEGACY_VIRTUAL_ROOTS
    )


def _resolve_ingest_file_path(file_path: str, *, thread_id: str) -> Path:
    """Resolve and confine ingest paths to the agent workspace (CWE-22/73)."""
    raw = (file_path or "").strip()
    if not raw:
        raise ValueError("file_path is required")

    posix = raw.replace("\\", "/")
    if posix.startswith("/") and not _is_allowed_virtual_absolute(posix):
        raise ValueError(
            "file_path must be under /workspace; "
            f"absolute paths outside the workspace are not allowed: {file_path!r}"
        )

    resolved = resolve_workspace_path(
        raw,
        thread_id=thread_id or None,
        operation="ingest_knowledge",
    )
    if not resolved.is_file():
        raise FileNotFoundError(f"File not found: {file_path}")
    return resolved


def _identity_headers(ctx: Context | None) -> dict[str, str]:
    """Forward the run credential outside model-controlled tool arguments."""
    from cuga.backend.knowledge.run_context import RUN_TOKEN_HEADER, RUN_TOKEN_META

    request_context = ctx.request_context if ctx is not None else None
    meta = getattr(request_context, "meta", None)
    token = meta.get(RUN_TOKEN_META) if isinstance(meta, dict) else None
    return {RUN_TOKEN_HEADER: token} if isinstance(token, str) else {}


# --- MCP Server ---

mcp = FastMCP(
    "Knowledge",
    instructions="Knowledge service for semantic document search and management",
)


@mcp.tool()
async def search_knowledge(
    query: str,
    scope: str = "all",
    thread_id: str = "",
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Search documents in the knowledge base.

    Scopes: ``"all"`` (default — both agent + session), ``"agent"``,
    ``"session"``. See the Knowledge Tool Contract block in your system
    prompt for query-writing rules, scope-restriction rules, how to read
    ``by_source`` / ``scope_legend`` / ``partial`` / ``error``, and the
    iterative-search budget — this docstring is intentionally terse to
    keep the contract the single source of truth.
    """
    try:
        resp = await _request(
            "POST",
            "/api/knowledge/search",
            headers=_identity_headers(ctx),
            json={"scope": scope, "query": query},
        )
        return resp.json()
    except httpx.HTTPStatusError as e:
        # Convert HTTP errors into a structured response so the LLM can
        # decide what to do (retry, fall back, ask the user) instead of
        # seeing an opaque exception trace. Preserve the backend's detail
        # string when it's a JSON body, otherwise the raw text.
        #
        # We do NOT set ``partial=True`` here: with ``results=[]`` the
        # call returned nothing usable, and the canonical contract tells
        # the LLM partial means "at least one scope succeeded — answer
        # with what you have." Hallucinating from an empty set would be
        # the worst possible failure mode. The presence of ``error``
        # alone is the signal to retry or move on.
        detail: Any
        try:
            detail = e.response.json().get("detail", e.response.text)
        except Exception:
            detail = e.response.text
        return {
            "scope": scope,
            "results": [],
            "error": f"HTTP {e.response.status_code}: {detail}",
        }
    except httpx.HTTPError as e:
        # Network-level: DNS, connection refused, timeout. Same contract.
        return {
            "scope": scope,
            "results": [],
            "error": f"Knowledge service unreachable: {e}",
        }


@mcp.tool()
async def ingest_knowledge(
    file_path: str,
    scope: str = "agent",
    replace_duplicates: bool = True,
    thread_id: str = "",
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Upload a document file to the knowledge base.

    Supports PDF, DOCX, XLSX, PPTX, HTML, Markdown, images (with OCR), and more.
    Use only scopes enabled for the current agent.
    When using scope="session", thread_id is required.

    ``file_path`` must resolve under the agent workspace (``/workspace/...`` or a
    relative path within it). Host-absolute paths such as ``/etc/passwd`` are rejected.
    """
    headers = _identity_headers(ctx)
    # Authenticate before opening a file; the supplied thread_id is untrusted.
    context_response = await _request("GET", "/api/knowledge/run-context", headers=headers)
    bound_thread = context_response.json().get("thread_id") or ""
    try:
        resolved = _resolve_ingest_file_path(file_path, thread_id=bound_thread)
    except (ValueError, FileNotFoundError) as exc:
        return {"error": str(exc)}

    with open(resolved, "rb") as f:
        resp = await _request(
            "POST",
            "/api/knowledge/documents",
            headers=headers,
            files={"files": (resolved.name, f)},
            data={"scope": scope, "replace_duplicates": str(replace_duplicates).lower()},
        )
    return resp.json()


@mcp.tool()
async def ingest_knowledge_url(
    url: str,
    scope: str = "agent",
    thread_id: str = "",
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Ingest a document from a URL into the knowledge base.

    Crawls the URL and indexes its content for search.
    Use only scopes enabled for the current agent. When using scope="session", thread_id is required.
    """
    resp = await _request(
        "POST",
        "/api/knowledge/documents/url",
        headers=_identity_headers(ctx),
        json={"scope": scope, "url": url},
    )
    return resp.json()


@mcp.tool()
async def list_knowledge_documents(
    scope: str = "agent",
    thread_id: str = "",
    ctx: Context | None = None,
) -> dict[str, Any]:
    """List all documents in the knowledge base.

    Use only scopes enabled for the current agent.
    When using scope="session", thread_id is required to identify the conversation.
    """
    resp = await _request(
        "GET",
        "/api/knowledge/documents",
        headers=_identity_headers(ctx),
        params={"scope": scope},
    )
    return resp.json()


@mcp.tool()
async def delete_knowledge_document(
    filename: str,
    scope: str = "agent",
    thread_id: str = "",
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Delete a document from the knowledge base by filename.

    Use only scopes enabled for the current agent. When using scope="session", thread_id is required.
    """
    resp = await _request(
        "DELETE",
        "/api/knowledge/documents",
        headers=_identity_headers(ctx),
        json={"scope": scope, "filename": filename},
    )
    return resp.json()


@mcp.tool()
async def get_ingestion_status(
    task_id: str,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Check the status of a document ingestion task.

    Returns progress information including per-file status.
    """
    resp = await _request(
        "GET",
        f"/api/knowledge/tasks/{task_id}",
        headers=_identity_headers(ctx),
    )
    return resp.json()


@mcp.tool()
async def get_knowledge_status(ctx: Context | None = None) -> dict[str, Any]:
    """Check if the knowledge service is healthy and get current settings."""
    resp = await _request(
        "GET",
        "/api/knowledge/health",
        headers=_identity_headers(ctx),
    )
    return resp.json()


# --- Entry point ---


def _token_matches(provided: str) -> bool:
    """Constant-time check of a presented token against the backend's."""
    if not provided:
        return False
    try:
        if secrets.compare_digest(provided, _get_token()):
            return True
        # The backend rewrites the token file when it restarts. Re-read once
        # before rejecting so a rotation does not strand a live client.
        return secrets.compare_digest(provided, _reload_token())
    except RuntimeError:
        # Token file missing — the backend has not finished starting. Fail
        # closed: "cannot verify" is not "no verification needed".
        return False


class _RequireInternalToken:
    """Require the internal token on the HTTP transport.

    In HTTP mode this server listens on 127.0.0.1 and holds the internal token,
    which it presents to the backend on every forwarded call. Without this guard
    it is an unauthenticated local endpoint that performs authenticated writes
    for whoever asks — the caller needs no credential of its own, which makes
    reaching the port equivalent to holding the token (GHSA-ffh4-qv86-xfxv).
    Requiring the token here means a caller must already have what the port
    would otherwise have handed them.

    The stdio transport needs no equivalent: its only client is the process that
    spawned it and owns the pipe.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # ASGI header names arrive lowercased.
        raw = dict(scope.get("headers") or ()).get(b"x-internal-token", b"")
        if not _token_matches(raw.decode("latin-1")):
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})
            return

        await self.app(scope, receive, send)


def run_http(host: str = "127.0.0.1", port: int = 8113):
    """Start the knowledge MCP server in HTTP mode (called from main process)."""
    from starlette.middleware import Middleware

    mcp.run(
        transport="http",
        host=host,
        port=port,
        middleware=[Middleware(_RequireInternalToken)],
    )


if __name__ == "__main__":
    transport = os.getenv("CUGA_KNOWLEDGE_TRANSPORT", "stdio")
    if transport == "http":
        port = int(os.getenv("CUGA_KNOWLEDGE_PORT", "8113"))
        run_http(host="127.0.0.1", port=port)
    else:
        mcp.run(transport="stdio")
