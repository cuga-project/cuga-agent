"""HTTP API the chat UI uses to render and drive MCP Apps.

Every request names an ``interaction_id`` created when the agent called the
tool. The server, resource and tools behind it come from the server-side
record (:mod:`cuga.backend.mcp_apps.store`), never from the browser.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from loguru import logger
from pydantic import BaseModel, Field

from cuga.backend.mcp_apps.protocol import csp_header
from cuga.backend.mcp_apps.store import Interaction, interaction_store
from cuga.backend.server.auth import require_chat_access
from cuga.backend.server.auth.models import UserInfo

router = APIRouter(tags=["mcp-apps"])

_PROXY_TEMPLATE = Path(__file__).with_name("sandbox_proxy.html")
_REGISTRY_TIMEOUT = 60.0
# Origin the sandbox proxy is served from. Must differ from the chat UI's origin.
# Empty: swap localhost <-> 127.0.0.1 on the chat UI's own port (local use).
SANDBOX_ORIGIN_ENV = "CUGA_MCP_APPS_SANDBOX_ORIGIN"
# Comma-separated chat UI origins allowed to embed the proxy (in addition to the local default).
HOST_ORIGINS_ENV = "CUGA_MCP_APPS_HOST_ORIGINS"


class InteractionRef(BaseModel):
    interaction_id: str


class AppToolCall(BaseModel):
    interaction_id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ModelContextUpdate(BaseModel):
    interaction_id: str
    content: Optional[list[dict[str, Any]]] = None
    structuredContent: Optional[dict[str, Any]] = None


def _interaction(interaction_id: str, request: Request, user: Optional[UserInfo]) -> Interaction:
    item = interaction_store.get(interaction_id)
    if item is None:
        raise HTTPException(status_code=404, detail="This form is no longer available.")
    thread_id = (request.headers.get("X-Thread-ID") or "").strip()
    if thread_id and item.thread_id and thread_id != item.thread_id:
        raise HTTPException(status_code=403, detail="This form belongs to another conversation.")
    user_id = user.sub if user else None
    if item.user_id and user_id and item.user_id != user_id:
        raise HTTPException(status_code=403, detail="This form belongs to another user.")
    return item


async def _registry(path: str, payload: dict[str, Any], agent_id: Optional[str]) -> Any:
    from cuga.backend.tools_env.registry.utils.api_utils import get_registry_base_url

    params = {"agent_id": agent_id} if agent_id else None
    async with httpx.AsyncClient(timeout=_REGISTRY_TIMEOUT) as client:
        response = await client.post(f"{get_registry_base_url()}{path}", json=payload, params=params)
    if response.status_code != 200:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = response.text
        raise HTTPException(status_code=response.status_code, detail=detail or "Registry error")
    return response.json()


@router.get("/api/mcp-apps/config")
async def mcp_apps_config(_user: Optional[UserInfo] = Depends(require_chat_access)):
    return {"sandbox_origin": os.environ.get(SANDBOX_ORIGIN_ENV, "").strip() or None}


@router.post("/api/mcp-apps/resource")
async def read_resource(
    body: InteractionRef, request: Request, user: Optional[UserInfo] = Depends(require_chat_access)
):
    """The app's HTML and sandbox settings, plus the tool input/result to hand it."""
    item = _interaction(body.interaction_id, request, user)
    content = await _registry(
        "/mcp_apps/resources/read", {"server": item.server, "uri": item.resource_uri}, item.agent_id
    )
    html = content.get("text")
    if html is None and content.get("blob"):
        import base64

        html = base64.b64decode(content["blob"]).decode("utf-8")
    ui = ((content.get("_meta") or {}).get("ui") or {}) if isinstance(content.get("_meta"), dict) else {}
    return {
        "interaction_id": item.interaction_id,
        "status": item.status,
        "tool": item.tool,
        "resource_uri": item.resource_uri,
        "html": html or "",
        "csp": ui.get("csp") or {},
        "permissions": ui.get("permissions") or {},
        "prefers_border": ui.get("prefersBorder"),
        "tool_input": item.tool_input,
        "tool_result": item.tool_result,
    }


@router.post("/api/mcp-apps/tools/call")
async def call_tool(
    body: AppToolCall, request: Request, user: Optional[UserInfo] = Depends(require_chat_access)
):
    """``tools/call`` from the app, to its own server only."""
    item = _interaction(body.interaction_id, request, user)
    if item.status != "pending":
        raise HTTPException(status_code=409, detail="This form is already complete.")
    result = await _registry(
        "/mcp_apps/tools/call",
        {"server": item.server, "name": body.name, "arguments": body.arguments},
        item.agent_id,
    )
    interaction_store.record_app_result(item.interaction_id, body.name, body.arguments, result)
    logger.info(f"MCP App {item.server}: {body.name} called from interaction {item.interaction_id}")
    return result


@router.post("/api/mcp-apps/model-context")
async def update_model_context(
    body: ModelContextUpdate, request: Request, user: Optional[UserInfo] = Depends(require_chat_access)
):
    """``ui/update-model-context``: kept until the user's next message resumes the agent."""
    item = _interaction(body.interaction_id, request, user)
    if item.status != "pending":
        raise HTTPException(status_code=409, detail="This form is already complete.")
    interaction_store.set_model_context(
        item.interaction_id, body.model_dump(include={"content", "structuredContent"}, exclude_none=True)
    )
    return {}


def _allowed_host_origins(request: Request) -> list[str]:
    origins = {o.strip().rstrip("/") for o in os.environ.get(HOST_ORIGINS_ENV, "").split(",") if o.strip()}
    url = urlsplit(str(request.base_url))
    port = f":{url.port}" if url.port else ""
    swap = {"localhost": "127.0.0.1", "127.0.0.1": "localhost"}
    if url.hostname in swap:
        origins.add(f"{url.scheme}://{swap[url.hostname]}{port}")
    return sorted(origins)


@lru_cache(maxsize=1)
def _proxy_template() -> str:
    return _PROXY_TEMPLATE.read_text(encoding="utf-8")


@router.get("/mcp-apps/sandbox_proxy.html", include_in_schema=False)
async def sandbox_proxy(request: Request, csp: Optional[str] = Query(None)):
    """The outer sandbox frame. Its CSP (inherited by the app) comes from the resource's ``_meta.ui.csp``."""
    try:
        declared = json.loads(csp) if csp else {}
    except ValueError:
        declared = {}
    origins = _allowed_host_origins(request)
    html = _proxy_template().replace("/*__ALLOWED_HOST_ORIGINS__*/[]", json.dumps(origins))
    policy = csp_header(declared if isinstance(declared, dict) else {})
    ancestors = " ".join(origins) or "'none'"
    return HTMLResponse(
        html,
        headers={
            "Content-Security-Policy": f"{policy}; frame-ancestors {ancestors}",
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )
