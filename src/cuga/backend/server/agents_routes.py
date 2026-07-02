"""Multi-agent registry endpoints: list, create, delete agents (dashboard)."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel

from cuga.backend.server.auth import require_manage_access
from cuga.backend.server.config_store import (
    delete_all_configs,
    get_latest_version,
    list_agents_with_configs,
    load_config,
    load_draft,
    save_draft,
)

router = APIRouter(prefix="/api/agents", tags=["agents"], dependencies=[Depends(require_manage_access)])

DEFAULT_AGENT_ID = "cuga-default"
_DEFAULT_NAME = "CUGA Default Agent"
_DEFAULT_DESCRIPTION = "Default CUGA agent with policy engine, tools, and chat."


class CreateAgentRequest(BaseModel):
    name: str
    description: str = ""
    kind: str = "single"


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


async def _describe_agent(agent_id: str) -> dict[str, Any]:
    config, _ = await load_config(None, agent_id)
    if not config:
        config = await load_draft(agent_id)
    agent_meta = (config or {}).get("agent") or {}
    is_default = agent_id == DEFAULT_AGENT_ID

    name = agent_meta.get("name") or (_DEFAULT_NAME if is_default else agent_id)
    description = agent_meta.get("description") or (_DEFAULT_DESCRIPTION if is_default else "")
    kind = agent_meta.get("kind") or "single"
    tools_count = len((config or {}).get("tools") or [])
    latest_version, latest_version_created_at = await get_latest_version(agent_id)
    return {
        "id": agent_id,
        "name": name,
        "description": description,
        "kind": kind,
        "tools_count": tools_count,
        "latest_version": latest_version,
        "latest_version_created_at": latest_version_created_at,
    }


@router.get("")
async def list_agents():
    """List all registered agents. cuga-default always appears, even before any config is saved."""
    try:
        rows = await list_agents_with_configs()
        agent_ids = [r["agent_id"] for r in rows]
        if DEFAULT_AGENT_ID not in agent_ids:
            agent_ids = [DEFAULT_AGENT_ID] + agent_ids
        agents = [await _describe_agent(agent_id) for agent_id in agent_ids]
        return JSONResponse({"agents": agents})
    except Exception as e:
        logger.error(f"Failed to list agents: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("")
async def create_agent(body: CreateAgentRequest):
    """Create a new agent (single or supervisor) with a seeded draft config."""
    if body.kind not in ("single", "supervisor"):
        raise HTTPException(status_code=400, detail="kind must be 'single' or 'supervisor'")
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    agent_id = _slugify(name)
    if not agent_id:
        raise HTTPException(status_code=400, detail="name must contain at least one alphanumeric character")

    existing_ids = {r["agent_id"] for r in await list_agents_with_configs()}
    if agent_id == DEFAULT_AGENT_ID or agent_id in existing_ids:
        raise HTTPException(status_code=409, detail=f"Agent '{agent_id}' already exists")

    config = {
        "agent": {
            "name": name,
            "description": body.description.strip(),
            "kind": body.kind,
        }
    }
    if body.kind == "supervisor":
        config["supervisor"] = {"subAgents": [], "planApproval": False}
    await save_draft(config, agent_id)
    logger.info(f"Created agent '{agent_id}' (kind={body.kind})")
    return JSONResponse({"id": agent_id, "name": name, "kind": body.kind})


@router.delete("/{agent_id}")
async def delete_agent(agent_id: str, request: Request):
    """Delete an agent and all its stored config versions. cuga-default cannot be deleted."""
    if agent_id == DEFAULT_AGENT_ID:
        raise HTTPException(status_code=400, detail="Cannot delete the default agent")
    existing_ids = {r["agent_id"] for r in await list_agents_with_configs()}
    if agent_id not in existing_ids:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
    await delete_all_configs(agent_id)
    app_state = getattr(request.app.state, "app_state", None)
    cache = getattr(app_state, "agent_graphs_cache", None)
    if cache:
        cache.pop((agent_id, True), None)
        cache.pop((agent_id, False), None)
    logger.info(f"Deleted agent '{agent_id}'")
    return JSONResponse({"status": "success"})
