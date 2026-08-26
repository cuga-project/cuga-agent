"""Context Forge workspace tool catalog: browse and attach to the draft config."""

import os
import re
from typing import Any, Optional

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from loguru import logger

from cuga.backend.server.manage_routes.router import router
from cuga.backend.server.manage_routes.helpers import agent_draft_lock, save_draft_section_unlocked


def _forge_settings():
    from cuga.config import settings

    return getattr(settings, "context_forge", None)


def _resolve_forge_token(cf: Any) -> Optional[str]:
    """Resolve the bearer credential CUGA presents to Forge. Never returned to the browser.

    Raises:
        NotImplementedError: token_source is "broker" — not implemented yet.
            The CUGA login flow's own IAM-proxy exchange
            (OIDCClient.exchange_service_token) is scoped to this service's
            own instance_id, not a caller-supplied audience, and there's no
            per-request access to the session's raw token to feed it anyway
            (see auth/dependencies.py — get_current_user reads the raw token
            locally and never attaches it to request.state). Whether the real
            broker can even issue a Forge-scoped (mcp-gw.<tenant>) token at
            all is still an open question — see the mapping doc.
    """
    token_source = (getattr(cf, "token_source", "env") or "env").lower()
    if token_source == "env":
        env_var = getattr(cf, "token_env_var", "CONTEXT_FORGE_TOKEN") or "CONTEXT_FORGE_TOKEN"
        return os.environ.get(env_var)
    if token_source == "broker":
        raise NotImplementedError("context_forge.token_source=broker is not implemented yet")
    raise ValueError(f"Unknown context_forge.token_source: {token_source!r}")


def _safe_tool_name(gateway_slug: str) -> str:
    """Build a tools[] entry name the manage UI will accept.

    AddToolModal validates with /^[a-z][a-z0-9_]*$/, so a raw
    f"forge-{gateway_slug}" fails the moment anyone opens the entry for
    editing — gateway slugs routinely contain hyphens.
    """
    slug = re.sub(r"[^a-z0-9_]+", "_", (gateway_slug or "").lower()).strip("_")
    return f"forge_{slug}" if slug else "forge_workspace"


def _forge_client_kwargs(cf: Any) -> dict[str, Any]:
    verify = bool(getattr(cf, "verify_ssl", True))
    ca_bundle = getattr(cf, "ca_bundle", "") or None
    return {"verify": ca_bundle if (verify and ca_bundle) else verify, "timeout": 15.0}


@router.get("/forge/catalog")
async def get_forge_catalog(request: Request, agent_id: Optional[str] = None):
    """Workspace tool catalog from Context Forge, grouped by gateway.

    {enabled: false} when the section is off or misconfigured — the single
    flag the UI keys on to show/hide the browse-catalog entry point. Mirrors
    the shape GET /api/tools/list already returns.
    """
    cf = _forge_settings()
    if not cf or not getattr(cf, "enabled", False) or not getattr(cf, "url", ""):
        return JSONResponse({"enabled": False, "gateways": []})

    try:
        token = _resolve_forge_token(cf)
    except (NotImplementedError, ValueError) as e:
        logger.warning(f"Context Forge catalog unavailable: {e}")
        return JSONResponse({"enabled": False, "gateways": [], "error": str(e)})
    if not token:
        logger.warning(f"Context Forge enabled but no token from token_source={cf.token_source!r}")
        return JSONResponse({"enabled": False, "gateways": [], "error": "no credential configured"})

    base_url = str(cf.url).rstrip("/")
    gateways: dict[str, dict[str, Any]] = {}
    try:
        async with httpx.AsyncClient(**_forge_client_kwargs(cf)) as client:
            r = await client.get(f"{base_url}/v1/tools", headers={"Authorization": f"Bearer {token}"})
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        # Per-request failure, not per-gateway — Forge answers /v1/tools in
        # one call across every gateway the token can see, unlike
        # get_tools_list's per-app fan-out. Nothing to isolate here.
        logger.warning(f"Failed to fetch Context Forge catalog: {e}")
        return JSONResponse({"enabled": True, "gateways": [], "error": str(e)})

    items = data if isinstance(data, list) else data.get("tools", data.get("data", []))
    for t in items or []:
        if not isinstance(t, dict):
            continue
        slug = t.get("gatewaySlug") or "default"
        gw = gateways.setdefault(slug, {"slug": slug, "name": slug, "tools": []})
        gw["tools"].append(
            {
                # `id` is what comes back in attach's tool_ids and lands in the
                # entry's `include` list — so it must be the value CUGA's
                # filter matches on. providers/combined._filter_tools_by_include
                # compares against the registry tool NAME (Forge's full
                # "<gateway-slug>-<tool>"), never Forge's internal uuid. Sending
                # the uuid filtered every tool out: "Loaded 0 tools (filtered by
                # include)" while the server had just retrieved 2.
                "id": t.get("name") or t.get("id"),
                "name": t.get("customName") or t.get("name"),
                "description": t.get("description") or "",
            }
        )

    return JSONResponse({"enabled": True, "gateways": list(gateways.values())})


@router.post("/forge/attach")
async def attach_forge_tools(request: Request, agent_id: Optional[str] = None):
    """Attach selected tools from one Forge gateway into the draft config.

    {gateway_slug, tool_ids[]} -> writes one tools[] entry
    (name=_safe_tool_name(gateway_slug)) through the normal draft helpers, so the
    usual draft -> publish flow applies. Re-attaching the same gateway
    replaces its entry rather than duplicating it.
    """
    if agent_id is None:
        agent_id = "cuga-default"

    cf = _forge_settings()
    if not cf or not getattr(cf, "enabled", False):
        raise HTTPException(status_code=400, detail="Context Forge is not enabled")

    data = await request.json()
    gateway_slug = data.get("gateway_slug")
    tool_ids = data.get("tool_ids")
    if not gateway_slug or not isinstance(tool_ids, list) or not tool_ids:
        raise HTTPException(status_code=422, detail="gateway_slug and a non-empty tool_ids[] are required")

    try:
        token = _resolve_forge_token(cf)
    except (NotImplementedError, ValueError) as e:
        raise HTTPException(status_code=501, detail=str(e))
    if not token:
        raise HTTPException(status_code=400, detail="No Context Forge credential configured")

    base_url = str(cf.url).rstrip("/")
    entry_name = _safe_tool_name(gateway_slug)

    # Store a secret *reference*, not the token itself. resolve_secret() maps
    # env://NAME through EnvBackend, which is appended in every secrets mode
    # (see secret_resolver._active_backends), and mcp_manager resolves it at
    # connection time via apply_authentication.
    #
    # Writing the raw token here would be wrong three ways:
    #   1. it lands in the draft config, which the manage UI round-trips —
    #      GET redacts secret-named fields, so a subsequent autosave would
    #      PATCH the blanked value straight back over the real one and break
    #      the tool;
    #   2. the token would sit in persisted config rather than only in pod env;
    #   3. refreshing an expired token would mean rewriting config instead of
    #      just restarting with a new env value.
    auth_value = (
        f"env://{getattr(cf, 'token_env_var', 'CONTEXT_FORGE_TOKEN') or 'CONTEXT_FORGE_TOKEN'}"
        if (getattr(cf, "token_source", "env") or "env").lower() == "env"
        else token
    )
    entry = {
        "name": entry_name,
        "type": "mcp",
        # Without this the app reaches the agent with description=None, which
        # the CugaLite prompt builder used to crash on. That is fixed
        # defensively in prompt_utils.format_apps_for_prompt, but an app the
        # model is asked to choose between should still describe itself.
        "description": f"Context Forge workspace tools from gateway '{gateway_slug}'",
        "url": f"{base_url}/mcp",
        "transport": "http",  # explicit — mcp_manager auto-detect falls back to "sse" for a bare url
        "auth": {"type": "bearer", "value": auth_value},
        "include": tool_ids,
    }

    try:
        from cuga.backend.server.config_store import _parse_agent_id, load_draft
        from cuga.backend.tools_env.registry.utils.api_utils import get_registry_base_url

        # Drop any prior entry for this gateway before appending the new one.
        # Matching only on entry_name is not enough: entries written before the
        # name was sanitised used the raw f"forge-{slug}" form, so a rename from
        # hyphens to underscores would leave the old, broken entry orphaned in
        # the draft — still failing to initialise on every registry reload, with
        # nothing in the UI tying it to this gateway.
        stale_names = {entry_name, f"forge-{gateway_slug}"}
        async with agent_draft_lock(agent_id):
            existing_draft = await load_draft(agent_id) or {}
            tools_list = [
                t
                for t in (existing_draft.get("tools") or [])
                if not (isinstance(t, dict) and t.get("name") in stale_names)
            ]
            tools_list.append(entry)
            full_draft = await save_draft_section_unlocked(agent_id, "tools", tools_list)

        state = getattr(request.app.state, "draft_app_state", None)
        tool_errors: dict[str, Any] = {}
        draft_agent_id = None
        try:
            base_agent_id = _parse_agent_id(str(agent_id))
            draft_agent_id = f"{base_agent_id}--draft"
            registry_url = get_registry_base_url()
            async with httpx.AsyncClient() as client:
                r = await client.post(f"{registry_url}/reload?agent_id={draft_agent_id}", timeout=10.0)
                r.raise_for_status()
                reload_data = r.json()
                if reload_data.get("status") == "partial" and "errors" in reload_data:
                    tool_errors = reload_data["errors"]
        except Exception as reload_err:
            logger.warning(f"Failed to reload registry after Forge attach: {reload_err}")

        if state:
            try:
                from cuga.backend.server.manage_routes.draft_ops import rebuild_agent_from_config

                draft_agent = getattr(state, "agent", None)
                if draft_agent:
                    await rebuild_agent_from_config(draft_agent, full_draft)
            except Exception as rebuild_err:
                logger.error(f"Failed to rebuild draft agent graph after Forge attach: {rebuild_err}")

            try:
                from cuga.backend.server.main import warm_shortlister_catalogue

                await warm_shortlister_catalogue(agent_id=draft_agent_id)
            except Exception as warm_err:
                logger.warning(f"Shortlister re-warm after Forge attach skipped: {warm_err}")

        response_data = {
            "status": "partial" if tool_errors else "success",
            "version": "draft",
            "agent_id": agent_id,
            "gateway_slug": gateway_slug,
            "tool_ids": tool_ids,
            # The entry exactly as persisted, so the UI mirrors server truth
            # instead of fabricating one. Safe to return: auth.value is a
            # secret reference (env://NAME), never the credential itself.
            "entry": entry,
        }
        if tool_errors:
            response_data["tool_errors"] = tool_errors
        return JSONResponse(response_data)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to attach Forge tools: {e}")
        raise HTTPException(status_code=500, detail=str(e))
