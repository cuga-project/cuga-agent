"""CUGA authorization boundary for Evolve's native router.

The bundled worker owns the client, storage and scheduler. External deployments
continue using MCP for chat and existing memory APIs; native retention requires
the bundled HTTP worker. A missing private API never falls back to
local storage. Keep application-specific inventory enrichment in memory_routes.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.parse import quote

import aiohttp
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from cuga.backend.evolve.integration import EvolveIntegration, normalize_evolve_identifier
from cuga.backend.server.auth import require_chat_access, require_manage_access
from cuga.config import get_service_instance_id

# Deliberate exposure list: no fact extraction or manually supplied source-deletion
# events. CUGA's transactional conversation-deletion outbox owns those events.
_NATIVE = [
    (r"/memory/entities/[^/]+/metadata", {"PATCH"}),
    (r"/memory/entities/[^/]+", {"DELETE"}),
    (r"/memory/access", {"POST"}),
    (r"/manage/memory/entities/[^/]+/metadata", {"PATCH"}),
    (r"/manage/retention/policies", {"GET", "POST"}),
    (r"/manage/retention/policies/[^/]+", {"GET", "PATCH", "PUT", "DELETE"}),
    (r"/manage/retention/policies/[^/]+/rules", {"GET", "POST"}),
    (r"/manage/retention/policies/[^/]+/rules/[^/]+", {"PATCH", "DELETE"}),
    (r"/manage/retention/policies/[^/]+/(mark|sweep)", {"POST"}),
    (r"/manage/retention/(candidates|audit)", {"GET"}),
    (r"/manage/retention/runs", {"GET", "POST"}),
    (r"/manage/retention/runs/[^/]+", {"GET"}),
    (r"/manage/retention/schedules", {"GET", "POST"}),
    (r"/manage/retention/schedules/[^/]+", {"GET", "PUT", "PATCH", "DELETE"}),
    (r"/manage/retention/schedules/[^/]+/(start|stop)", {"POST"}),
    (r"/manage/retention/jobs", {"GET"}),
    (r"/manage/retention/jobs/[^/]+", {"GET"}),
    (r"/manage/retention/jobs/[^/]+/(cancel|acknowledge-interrupted)", {"POST"}),
]


def bundled_api_token() -> str | None:
    # Only the container supervisor creates this credential. An explicit external
    # mode/URL must never be sent the credential or use this local transport.
    from cuga.config import settings

    if EvolveIntegration._get_mode() != "direct":
        return None
    if str(settings.evolve.url).rstrip("/") != "http://127.0.0.1:8201/sse":
        return None
    return os.environ.get("CUGA_EVOLVE_API_TOKEN") or None


def native_path(request: Request) -> str:
    return request.url.path.removeprefix("/api")


def exposed(path: str, method: str) -> bool:
    return any(method in methods and re.fullmatch(pattern, path) for pattern, methods in _NATIVE)


async def authorize_agent(agent_id: str | None) -> None:
    if agent_id is not None and not isinstance(agent_id, str):
        raise HTTPException(422, "Invalid agent identity")
    if agent_id is None or agent_id == "cuga-default":
        return
    from cuga.backend.server import agent_registry
    from cuga.backend.server.config_store import list_agents_with_configs

    # CUGA's agent catalogue is shared by authorized chat users. There is no
    # separate per-agent ACL; unknown/disabled-registry agents are inaccessible.
    if not agent_registry.is_agent_registry_enabled() or agent_id not in {
        row["agent_id"] for row in await list_agents_with_configs()
    }:
        raise HTTPException(404, "Agent not found")


async def memory_scope(request: Request, path: str) -> dict[str, Any]:
    if not EvolveIntegration.is_enabled():
        raise HTTPException(404, "Evolve memory is disabled")
    manage = path.startswith("/manage/")
    user = await (require_manage_access(request) if manage else require_chat_access(request))
    user_id = normalize_evolve_identifier(user.sub if user else None)
    # Auth-disabled CUGA uses default_user, which Evolve explicitly rejects.
    # Do not invent an identity that could expose previously anonymous memories.
    if user_id is None:
        raise HTTPException(401, "A signed-in user is required for memory operations")
    namespace_id = normalize_evolve_identifier(get_service_instance_id())
    if namespace_id is None:
        raise HTTPException(503, "Memory requires a service-instance identity")
    agent_id = request.query_params.get("agent_id")
    if agent_id is None and not path.startswith("/manage/retention/"):
        agent_id = "cuga-default"
    if agent_id is not None and (not agent_id.strip() or len(agent_id) > 200):
        raise HTTPException(422, "Invalid agent identity")
    await authorize_agent(agent_id)
    return dict(namespace_id=namespace_id, user_id=user_id, agent_id=agent_id, can_manage=manage)


def safe_payload(value: Any) -> Any:
    """Preserve native contracts but never return backend exceptions or credentials."""
    if isinstance(value, list):
        return [safe_payload(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key in {"error", "last_error"} and item:
            result[key] = "Evolve could not complete the operation."
        elif key in {"errors", "warnings"} and item:
            result[key] = ["Evolve reported an operation failure or warning."]
        else:
            result[key] = safe_payload(item)
    return result


async def forward(request: Request, path: str) -> JSONResponse:
    if not exposed(path, request.method):
        raise HTTPException(404, "Memory operation not found")
    scope = await memory_scope(request, path)
    token = bundled_api_token()
    if not token:
        raise HTTPException(503, "Native retention APIs require the bundled Evolve HTTP service")
    from cuga.backend.server import memory_routes

    if path.endswith(("/mark", "/sweep", "/candidates", "/audit")):
        await memory_routes._require_durable_retention()
    raw = await request.body()
    try:
        body = json.loads(raw) if raw else None
    except ValueError:
        raise HTTPException(422, "Invalid JSON body") from None
    if body is not None and not isinstance(body, dict):
        raise HTTPException(422, "Expected a JSON object")
    if body and body.get("additional_matches"):
        raise HTTPException(422, "Retention matches must be derived from the saved policy")
    if path.endswith(("/candidates", "/audit")):
        try:
            limit = int(request.query_params.get("limit", "100"))
        except ValueError:
            raise HTTPException(422, "Invalid limit") from None
        if not 1 <= limit <= 1000:
            raise HTTPException(422, "Limit must be between 1 and 1000")
    # Reject scope tampering even on native operations that otherwise ignore it.
    if body and {"namespace_id", "user_id", "can_manage"}.intersection(body):
        raise HTTPException(422, "Memory scope is supplied by CUGA")
    if body and isinstance(body.get("definition"), dict):
        await authorize_agent(body["definition"].get("agent_id"))
    if body and isinstance(body.get("changes"), dict) and "agent_id" in body["changes"]:
        await authorize_agent(body["changes"]["agent_id"])
    params = [(key, value) for key, value in request.query_params.multi_items() if key != "agent_id"]
    if any(key in {"namespace_id", "user_id", "can_manage"} for key, _ in params):
        raise HTTPException(422, "Memory scope is supplied by CUGA")
    definition = body.get("definition") if body else None
    requested_policy = body.get("policy_id") if body else None
    if isinstance(definition, dict):
        requested_policy = definition.get("policy_id")
    needs_default = requested_policy == "cuga-standard" and (path.endswith("/runs") or "/schedules" in path)
    if path.endswith("/policies/cuga-standard/mark"):
        needs_default = True
    if (request.method == "GET" and path.endswith("/policies")) or needs_default:
        await memory_routes._retention_policies()
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
            async with session.request(
                request.method,
                "http://127.0.0.1:8201/private" + quote(path, safe="/"),
                params=params,
                json=body,
                headers={"Authorization": f"Bearer {token}", "X-Cuga-Memory-Scope": json.dumps(scope)},
                allow_redirects=False,
            ) as response:
                status = response.status
                if status >= 400:
                    messages = {
                        401: "Memory authentication required",
                        403: "Memory access denied",
                        404: "Memory not found",
                        409: "Memory configuration changed or is in use; refresh and retry",
                        422: "Invalid memory request",
                    }
                    # Keep only a validated operation reference; never expose
                    # provider exception text or arbitrary structured details.
                    try:
                        failure = await response.json()
                    except (ValueError, aiohttp.ContentTypeError):
                        failure = {}
                    if isinstance(failure, dict) and isinstance(failure.get("detail"), dict):
                        failure = failure["detail"]
                    run_id = failure.get("run_id") if isinstance(failure, dict) else None
                    if isinstance(run_id, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", run_id):
                        return JSONResponse(
                            {
                                "detail": "Retention operation did not complete; inspect its run history",
                                "run_id": run_id,
                            },
                            status_code=status if status < 500 else 502,
                        )
                    raise HTTPException(
                        status if status < 500 else 502, messages.get(status, "Memory request rejected")
                    )
                if status >= 300:
                    raise HTTPException(502, "Invalid Evolve service response")
                result = await response.json()
    except (aiohttp.ClientError, TimeoutError, ValueError):
        raise HTTPException(503, "Evolve memory service is unavailable") from None
    if not isinstance(result, dict):
        raise HTTPException(502, "Invalid Evolve service response")
    if request.method == "PATCH" and re.fullmatch(r"/(manage/)?memory/entities/[^/]+/metadata", path):
        # Native owns the mutation; CUGA adds conversation links and usage through
        # its existing scoped detail enrichment, which native projections omit.
        from cuga.backend.server.auth.models import UserInfo

        entity_id = path.split("/")[-2]
        user = UserInfo(sub=scope["user_id"])
        if scope["can_manage"]:
            return await memory_routes.get_admin_memory_entity(entity_id, scope["agent_id"], user)
        return await memory_routes.get_user_memory_entity(entity_id, scope["agent_id"], user)
    return JSONResponse(safe_payload(result), status_code=status)


class MemoryServiceRoute(APIRoute):
    """Memory mutations delegate to the native service when it is bundled."""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            path = native_path(request)
            if bundled_api_token():
                if exposed(path, request.method):
                    return await forward(request, path)
                # Enrichment/capability/preview handlers still use the MCP
                # singleton, but must enforce the same host identity boundary.
                await memory_scope(request, path)
            return await original(request)

        return handler


router = APIRouter(prefix="/api")


@router.api_route("/manage/retention/{operation:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def native_retention(request: Request):
    return await forward(request, native_path(request))
