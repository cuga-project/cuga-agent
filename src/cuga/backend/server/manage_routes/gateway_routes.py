"""MCP gateway instance discovery for the Manage page (Connect / Refresh)."""

from typing import Optional

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel

from cuga.backend.server.auth import require_manage_access
from cuga.backend.server.error_responses import safe_http_exception

from cuga.backend.server.manage_routes.helpers import resolve_agent_id
from cuga.backend.server.manage_routes.router import router

_SECRET_REF_PREFIXES = ("db://", "vault://", "aws://")


class GatewayDiscoverRequest(BaseModel):
    url: str
    # Either the key itself (not stored by this call) or a saved secret ref.
    api_key: Optional[str] = None
    api_key_ref: Optional[str] = None
    agent_id: Optional[str] = None
    # "user": list with the signed-in admin's own token for the instance (no API key).
    run_as: Optional[str] = None


@router.post("/gateway/discover")
async def discover_gateway(
    body: GatewayDiscoverRequest, request: Request = None, user=Depends(require_manage_access)
):
    """List the MCP servers and tools of a gateway instance.

    Signed-in user mode: with the admin's own token for the instance (exchanged from the IdP
    token kept at sign-in). Service ID mode: with the instance API key, which is sent only to
    the platform IAM (``auth.iam_proxy_url``); the gateway receives the exchanged token.
    """
    from cuga.backend.secrets import resolve_secret
    from cuga.backend.server.auth import gateway_tokens
    from cuga.backend.tools_env.registry import mcp_gateway

    if body.run_as == mcp_gateway.RUN_AS_USER:
        try:
            instance = mcp_gateway.parse_instance_url(body.url)
        except ValueError as e:
            raise safe_http_exception(
                e, status=400, message="Enter a valid gateway instance URL with ?instance=<id>."
            ) from e
        provider = gateway_tokens.provider_from_request(request, user) if request is not None else None
        if provider is None:
            raise HTTPException(
                status_code=400,
                detail="Sign in to CUGA to connect in signed-in user mode (the sign-in token is used to list the tools).",
            )
        try:
            token = await provider.token(instance.instance_id)
            return await mcp_gateway.discover(body.url, bearer_token=token)
        except gateway_tokens.GatewayAccessError as e:
            raise safe_http_exception(
                e, status=403, message="Gateway access denied. Check your sign-in and role on the instance."
            ) from e
        except mcp_gateway.GatewayError as e:
            raise safe_http_exception(e, status=502, message="Gateway discovery failed.") from e

    api_key = (body.api_key or "").strip()
    ref = (body.api_key_ref or "").strip()
    if not api_key and ref:
        if not ref.startswith(_SECRET_REF_PREFIXES):
            raise HTTPException(
                status_code=400, detail="api_key_ref must be a saved secret (db://, vault://, aws://)"
            )
        api_key = resolve_secret(ref, agent_id=resolve_agent_id(body.agent_id)) or ""
        if not api_key:
            raise HTTPException(status_code=400, detail="The selected secret is empty or cannot be read.")
    try:
        return await mcp_gateway.discover(body.url, api_key)
    except ValueError as e:
        raise safe_http_exception(
            e, status=400, message="Enter a valid gateway instance URL with ?instance=<id>."
        ) from e
    except mcp_gateway.GatewayError as e:
        raise safe_http_exception(e, status=502, message="Gateway discovery failed.") from e
