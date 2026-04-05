"""Knowledge-specific auth dependency.

Supports two modes:
- External (browser/SDK): OIDC auth for user/tenant, headers for agent/thread
- Internal (MCP subprocess): internal token + all identity from headers

External mode derives user_id/tenant_id from OIDC auth context ONLY.
X-User-ID/X-Tenant-ID headers are ALWAYS IGNORED in external mode.
"""

from __future__ import annotations

import logging
import re
import secrets
from dataclasses import dataclass

from fastapi import Request, HTTPException

logger = logging.getLogger("cuga.knowledge")


@dataclass
class KnowledgeIdentity:
    """Resolved identity for a knowledge request."""

    user_id: str | None
    tenant_id: str | None
    agent_id: str
    thread_id: str | None  # Only required for scope=session
    auth_mode: str  # "external" or "internal"


def _scope_enabled_for_request(scope: str, request: Request | None) -> bool:
    if request is None:
        return True

    app_state = getattr(request.app.state, "app_state", None)
    engine = getattr(app_state, "knowledge_engine", None) if app_state else None
    config = getattr(engine, "_config", None) if engine else None
    if not config:
        return False
    if not getattr(config, "enabled", False):
        return False
    if scope == "agent":
        return bool(getattr(config, "agent_level_enabled", True))
    if scope == "session":
        return bool(getattr(config, "session_level_enabled", True))
    return False


def _scope_disabled_detail(scope: str) -> str:
    if scope == "session":
        return "Session-level knowledge is disabled for this agent"
    return "Agent-level knowledge is disabled for this agent"


async def require_internal_or_auth(request: Request) -> KnowledgeIdentity:
    """Knowledge-only auth dependency. Applied ONLY to knowledge routes.

    Two modes determined by X-Internal-Token header:
    - Internal: trust identity from headers (MCP subprocess, localhost only)
    - External: derive user/tenant from OIDC, agent/thread from headers
    """
    internal_token = request.headers.get("X-Internal-Token")
    _app_state = getattr(request.app.state, "app_state", None)
    app_token = getattr(_app_state, "internal_token", None) if _app_state else None

    # --- Internal mode (MCP subprocess) ---
    if internal_token and app_token:
        client_host = request.client.host if request.client else ""
        if client_host in ("127.0.0.1", "::1") and secrets.compare_digest(internal_token, app_token):
            agent_id = request.headers.get("X-Agent-ID", "")
            if not agent_id:
                raise HTTPException(status_code=400, detail="X-Agent-ID required")

            return KnowledgeIdentity(
                user_id=request.headers.get("X-User-ID") or None,
                tenant_id=request.headers.get("X-Tenant-ID") or None,
                agent_id=agent_id,
                thread_id=request.headers.get("X-Thread-ID") or None,
                auth_mode="internal",
            )

    # --- External mode (browser/SDK) ---
    # Check if auth is enabled
    from cuga.backend.server.auth.dependencies import _auth_enabled, get_current_user

    if _auth_enabled():
        try:
            current_user = await get_current_user(request)
        except Exception:
            raise HTTPException(status_code=401, detail="unauthorized")

        if current_user is None:
            raise HTTPException(status_code=401, detail="unauthorized")

        user_id = getattr(current_user, "sub", None)
        tenant_id = getattr(current_user, "tenant_id", None)
    else:
        # Auth disabled — allow unauthenticated access (dev mode)
        user_id = None
        tenant_id = None

    agent_id = request.headers.get("X-Agent-ID", "")
    if not agent_id:
        raise HTTPException(status_code=400, detail="X-Agent-ID required")

    return KnowledgeIdentity(
        user_id=user_id,
        tenant_id=tenant_id,
        agent_id=agent_id,
        thread_id=request.headers.get("X-Thread-ID") or None,
        auth_mode="external",
    )


def resolve_collection(identity: KnowledgeIdentity, scope: str,
                       request: Request | None = None) -> str:
    """Resolve collection name from identity and scope.

    Enforces session identity requirements and ownership before allowing access.
    """
    if not _scope_enabled_for_request(scope, request):
        raise HTTPException(status_code=403, detail=_scope_disabled_detail(scope))

    if scope == "session":
        if not identity.thread_id:
            raise HTTPException(status_code=400, detail="X-Thread-ID required")

        # Enforce session ownership if provider is available
        # Internal mode (MCP subprocess) is already authenticated via localhost + token,
        # so thread_id alone is sufficient to identify the session.
        if request:
            _as = getattr(request.app.state, "app_state", None)
            provider = getattr(_as, "knowledge_provider", None) if _as else None
            if provider and identity.user_id and identity.tenant_id:
                # Ensure session state exists (creates on first access with owner)
                provider.get_or_create_session(
                    identity.thread_id,
                    user_id=identity.user_id,
                    tenant_id=identity.tenant_id,
                )
                if not provider.check_session_access(
                    identity.thread_id, identity.user_id, identity.tenant_id
                ):
                    raise HTTPException(status_code=403, detail="access denied to session")

        return f"kb_sess_{_sanitize(identity.thread_id)}"
    elif scope == "agent":
        base = f"kb_agent_{_sanitize(identity.agent_id)}"
        # Append vector-config hash when available so each distinct embedding /
        # chunking configuration gets its own isolated collection.
        if request:
            _as = getattr(request.app.state, "app_state", None)
            config_hash = getattr(_as, "knowledge_config_hash", None) if _as else None
            if config_hash:
                return f"{base}_{config_hash}"
        return base
    else:
        raise HTTPException(status_code=400, detail="scope must be 'agent' or 'session'")


def _sanitize(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", value)
