"""Deterministic MCP Apps server used by CUGA's tests.

Tools, by MCP Apps ``visibility``:

- ``open_form`` (model + app) declares the UI resource ``FORM_URI``: calling it opens a form.
- ``submit_form`` (app only) is what the form calls; the agent never sees it.
- ``plain_tool`` (model only) is an ordinary tool that apps may not call.

The resource is a tiny HTML page with the MCP Apps MIME type. Same input, same output.
"""

from __future__ import annotations

import hashlib
import json
import socket
import threading
import time
from contextlib import contextmanager
from typing import Iterator

from fastmcp import FastMCP
from fastmcp.apps.config import AppConfig
from fastmcp.tools import ToolResult
from mcp.types import TextContent

SERVER_NAME = "mock_forms"
FORM_URI = "ui://mock-forms/form"
FORM_HTML = "<!doctype html><html><head><title>Mock form</title></head><body><h1>Mock form</h1></body></html>"

mcp = FastMCP(SERVER_NAME)


def session_id_for(title: str, fields: list[str]) -> str:
    digest = hashlib.sha256(json.dumps([title, fields]).encode()).hexdigest()[:12]
    return f"form-{digest}"


@mcp.tool(
    app=AppConfig(resource_uri=FORM_URI, visibility=["model", "app"]),
    description="Open a form asking the user for the given fields.",
)
def open_form(title: str, fields: list[str]) -> ToolResult:
    form = {"session_id": session_id_for(title, fields), "title": title, "fields": fields}
    text = f"Opened the form '{title}' with {len(fields)} fields. Waiting for the user."
    return ToolResult(content=[TextContent(type="text", text=text)], structured_content={"form": form})


@mcp.tool(app=AppConfig(visibility=["app"]), description="Form action: submit the user's values.")
def submit_form(session_id: str, values: dict[str, str]) -> ToolResult:
    result = {"status": "completed", "session_id": session_id, "values": dict(sorted(values.items()))}
    text = f"Accepted {len(values)} values."
    return ToolResult(content=[TextContent(type="text", text=text)], structured_content=result)


@mcp.tool(app=AppConfig(visibility=["model"]), description="Double a number.")
def plain_tool(x: int) -> dict:
    return {"double": 2 * x}


@mcp.resource(FORM_URI, name="Mock form", app=AppConfig(prefers_border=False))
def form_page() -> str:
    return FORM_HTML


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def serve_http(app, port: int, path_ready: str = "/") -> Iterator[str]:
    """Run an ASGI app with uvicorn in a thread; yields its base URL."""
    import httpx
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 30
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError(f"server on port {port} did not start")
        time.sleep(0.05)
    try:
        httpx.get(base + path_ready, timeout=5)
        yield base
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@contextmanager
def run_mock_server() -> Iterator[str]:
    """Serve the mock over streamable HTTP; yields the MCP endpoint URL."""
    port = free_port()
    with serve_http(mcp.http_app(path="/mcp"), port) as base:
        yield f"{base}/mcp"
