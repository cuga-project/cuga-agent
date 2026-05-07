"""FastAPI router exposing the loops registry + UI.

Mount with:
    from cuga.backend.loops.api import router as loops_router
    app.include_router(loops_router)

The router serves:
  - GET  /cuga/loops/api/list             — JSON of all loops
  - GET  /cuga/loops/api/{loop_id}        — single loop details + recent runs
  - POST /cuga/loops/api/{loop_id}/pause  — pause an active loop
  - POST /cuga/loops/api/{loop_id}/resume — resume a paused loop
  - POST /cuga/loops/api/{loop_id}/run    — fire one execution now
  - DELETE /cuga/loops/api/{loop_id}      — delete a loop + its runs
  - GET  /cuga/loops/                     — single-page HTML UI

The UI shows ALL loops in the shared registry across apps (when apps share
the same CUGA_DBS_DIR). Pause/resume/delete only takes effect for loops
whose owning agent is registered in this process; other loops can still be
deleted from the registry.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from cuga.backend.loops.cron_parser import parse_cadence, parse_delay_seconds
from cuga.backend.loops.models import TriggerKind
from cuga.backend.loops.service import get_loops_service

router = APIRouter(prefix="/cuga/loops", tags=["cuga-loops"])

_UI_HTML_PATH = Path(__file__).parent / "ui.html"


class CreateLoopRequest(BaseModel):
    """Body for POST /cuga/loops/api/create.

    Exactly one of `cadence` (recurring) or `delay` (one-shot) must be set.
    `agent_name` should match an agent that has registered with the loops
    service in some live process; if no such agent is registered when the
    loop fires, the loop is marked ORPHANED.
    """
    agent_name: str = Field(..., description="Loops-service-registered agent to dispatch fires to")
    thread_id: str = Field(..., description="Thread the loop fires invoke")
    prompt: str = Field(..., description="Prompt the agent receives on each fire")
    cadence: Optional[str] = Field(None, description="Recurring cadence (e.g. '5m', 'weekly', '0 9 * * *')")
    delay: Optional[str] = Field(None, description="One-shot delay (e.g. '60', '5m', '2h')")
    app_name: Optional[str] = Field(None, description="App label (defaults to this process's app_name)")
    expires_in_days: Optional[int] = Field(7, description="Auto-cancel after N days (0 = never)")
    metadata: Optional[dict] = Field(None, description="Arbitrary JSON to attach")


@router.post("/api/create")
async def create_loop(req: CreateLoopRequest):
    """Create a loop without an LLM in the loop.

    Useful for UI 'Watch this' buttons, external schedulers, tests, or any
    deterministic path that wants to schedule an agent run."""
    if (req.cadence is None) == (req.delay is None):
        raise HTTPException(400, "exactly one of `cadence` or `delay` is required")

    svc = get_loops_service()
    await svc.start()  # idempotent

    expires_at = (
        time.time() + req.expires_in_days * 86400
        if req.expires_in_days and req.expires_in_days > 0
        else None
    )

    if req.cadence:
        try:
            kind, normalized, cron, interval_seconds = parse_cadence(req.cadence)
        except ValueError as e:
            raise HTTPException(400, str(e))
        loop = await svc.registry.create_loop(
            app_name=req.app_name or svc.app_name,
            agent_name=req.agent_name,
            thread_id=req.thread_id,
            prompt=req.prompt,
            trigger_kind=kind,
            trigger_spec=normalized,
            cron=cron,
            interval_seconds=interval_seconds,
            expires_at=expires_at,
            metadata=req.metadata,
        )
    else:
        try:
            seconds = parse_delay_seconds(req.delay)
        except ValueError as e:
            raise HTTPException(400, str(e))
        loop = await svc.registry.create_loop(
            app_name=req.app_name or svc.app_name,
            agent_name=req.agent_name,
            thread_id=req.thread_id,
            prompt=req.prompt,
            trigger_kind=TriggerKind.DELAY,
            trigger_spec=str(req.delay),
            interval_seconds=seconds,
            next_fire_at=time.time() + seconds,
            expires_at=expires_at,
            metadata=req.metadata,
        )

    await svc.register_loop(loop)
    return {"loop": loop.model_dump(mode="json"), "agent_registered_here": svc.get_agent(req.agent_name) is not None}


@router.get("/api/agents")
async def list_registered_agents():
    """List agents currently registered with this process's loops service.

    Useful for the UI's `agent_name` autocomplete in the Watch button."""
    svc = get_loops_service()
    return {
        "current_app": svc.app_name,
        "agents": sorted(svc._agents.keys()),
    }


@router.get("/api/list")
async def list_loops(app_name: Optional[str] = None, status: Optional[str] = None):
    svc = get_loops_service()
    from cuga.backend.loops.models import LoopStatus
    status_enum = LoopStatus(status) if status else None
    loops = await svc.registry.list_all(app_name=app_name, status=status_enum)
    return {
        "current_app": svc.app_name,
        "loops": [l.model_dump(mode="json") for l in loops],
    }


@router.get("/api/{loop_id}")
async def get_loop(loop_id: str):
    svc = get_loops_service()
    loop = await svc.registry.get(loop_id)
    if not loop:
        raise HTTPException(404, f"loop {loop_id} not found")
    runs = await svc.registry.list_runs(loop_id, limit=25)
    return {
        "loop": loop.model_dump(mode="json"),
        "runs": [r.model_dump(mode="json") for r in runs],
        "agent_registered_here": svc.get_agent(loop.agent_name) is not None,
    }


@router.post("/api/{loop_id}/pause")
async def pause(loop_id: str):
    svc = get_loops_service()
    ok = await svc.pause_loop(loop_id)
    if not ok:
        raise HTTPException(404, f"loop {loop_id} not found")
    return {"ok": True, "status": "paused"}


@router.post("/api/{loop_id}/resume")
async def resume(loop_id: str):
    svc = get_loops_service()
    ok = await svc.resume_loop(loop_id)
    if not ok:
        raise HTTPException(404, f"loop {loop_id} not found")
    return {"ok": True, "status": "active"}


@router.post("/api/{loop_id}/run")
async def run_now(loop_id: str):
    svc = get_loops_service()
    ok = await svc.run_now(loop_id)
    if not ok:
        raise HTTPException(404, f"loop {loop_id} not found")
    return {"ok": True, "status": "queued"}


@router.delete("/api/{loop_id}")
async def delete(loop_id: str):
    svc = get_loops_service()
    await svc.delete_loop(loop_id)
    return {"ok": True}


@router.get("/", response_class=HTMLResponse)
@router.get("", response_class=HTMLResponse)
async def ui():
    if not _UI_HTML_PATH.exists():
        raise HTTPException(500, "loops UI html missing")
    return HTMLResponse(_UI_HTML_PATH.read_text(encoding="utf-8"))
