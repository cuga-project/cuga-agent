"""MCP gateway instance discovery for the Manage page (Connect / Refresh)."""

from typing import Optional

from fastapi import HTTPException
from pydantic import BaseModel

from cuga.backend.server.manage_routes.helpers import resolve_agent_id
from cuga.backend.server.manage_routes.router import router

_SECRET_REF_PREFIXES = ("db://", "vault://", "aws://")


class GatewayDiscoverRequest(BaseModel):
    url: str
    # Either the key itself (not stored by this call) or a saved secret ref.
    api_key: Optional[str] = None
    api_key_ref: Optional[str] = None
    agent_id: Optional[str] = None


@router.post("/gateway/discover")
async def discover_gateway(body: GatewayDiscoverRequest):
    """List the MCP servers and tools of a gateway instance, as its API key sees them.

    The key is sent only to the platform IAM (``auth.iam_proxy_url``); the gateway receives the
    exchanged instance token.
    """
    from cuga.backend.secrets import resolve_secret
    from cuga.backend.tools_env.registry import mcp_gateway

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
        raise HTTPException(status_code=400, detail=str(e)) from e
    except mcp_gateway.GatewayError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
