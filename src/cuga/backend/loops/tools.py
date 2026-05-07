"""LangChain @tool defs the agent uses to schedule itself.

Tools read the current agent's identity (agent_name, thread_id, app_name)
from context vars set by the SDK around invoke(), so the agent doesn't have
to pass these as arguments.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Optional

from langchain_core.tools import tool

from cuga.backend.loops.cron_parser import parse_cadence, parse_delay_seconds
from cuga.backend.loops.models import LoopStatus, TriggerKind
from cuga.backend.loops.service import (
    current_agent_name,
    current_app_name,
    current_thread_id,
    get_loops_service,
)

logger = logging.getLogger(__name__)


def _identity() -> tuple[str, str, str]:
    agent = current_agent_name.get()
    thread = current_thread_id.get()
    app = current_app_name.get() or "cuga"
    if not agent or not thread:
        raise RuntimeError(
            "loop tools require agent_name + thread_id context — "
            "this should be set automatically by CugaAgent.invoke(). "
            "If you're calling tools directly, set the context vars first."
        )
    return agent, thread, app


@tool
async def schedule_recurring(
    cadence: str,
    prompt: str,
    expires_in_days: Optional[int] = 7,
    metadata_json: Optional[str] = None,
) -> str:
    """Schedule yourself to wake up on a recurring schedule and re-run a prompt.

    Args:
        cadence: When to run. Accepts intervals ("5m", "2h", "1d"), raw cron
            ("0 9 * * *"), or natural shorthand ("daily", "every weekday",
            "every morning", "weekly", "hourly", "daily 9am").
        prompt: What to ask yourself when the loop fires. Use the same kind of
            instruction the user would type — the loop fire is treated like a
            new turn in your conversation thread.
        expires_in_days: Auto-cancel after this many days. Default 7. Use 0
            for no expiry (keep this rare — orphaned loops accumulate cost).
        metadata_json: Optional JSON string the loop should carry (e.g. the
            user's original query, a target URL). Stored on the loop and
            visible in the loops UI.

    Returns:
        A confirmation string with the loop id, normalized cadence, and
        next fire time. Save the loop id if you might want to cancel later.
    """
    agent, thread, app = _identity()
    kind, normalized, cron, interval_seconds = parse_cadence(cadence)
    expires_at = (time.time() + expires_in_days * 86400) if expires_in_days else None
    md = json.loads(metadata_json) if metadata_json else None

    svc = get_loops_service()
    loop = await svc.registry.create_loop(
        app_name=app,
        agent_name=agent,
        thread_id=thread,
        prompt=prompt,
        trigger_kind=kind,
        trigger_spec=normalized,
        cron=cron,
        interval_seconds=interval_seconds,
        expires_at=expires_at,
        metadata=md,
    )
    await svc.register_loop(loop)
    return (
        f"Scheduled loop {loop.id} — cadence='{normalized}', "
        f"expires={'never' if not expires_at else f'in {expires_in_days}d'}. "
        f"Cancel with cancel_loop('{loop.id}')."
    )


@tool
async def schedule_wakeup(
    delay: str,
    prompt: str,
    metadata_json: Optional[str] = None,
) -> str:
    """Schedule a single one-shot wake-up at `delay` from now.

    Args:
        delay: How long to wait before firing. Accepts seconds as int-string
            ("60", "300") or shorthand ("30s", "5m", "2h").
        prompt: What to ask yourself when the wake-up fires.
        metadata_json: Optional JSON string to attach to the wakeup.

    Returns:
        Confirmation with the loop id and fire time.
    """
    agent, thread, app = _identity()
    seconds = parse_delay_seconds(delay)
    next_fire = time.time() + seconds
    md = json.loads(metadata_json) if metadata_json else None

    svc = get_loops_service()
    loop = await svc.registry.create_loop(
        app_name=app,
        agent_name=agent,
        thread_id=thread,
        prompt=prompt,
        trigger_kind=TriggerKind.DELAY,
        trigger_spec=str(delay),
        interval_seconds=seconds,
        next_fire_at=next_fire,
        metadata=md,
    )
    await svc.register_loop(loop)
    return f"Wakeup scheduled in {seconds}s — loop id {loop.id}."


@tool
async def list_my_loops() -> str:
    """List all loops you (this agent + thread) currently have scheduled.

    Returns a JSON array describing each loop's id, status, cadence, prompt,
    and next fire time.
    """
    agent, thread, _ = _identity()
    svc = get_loops_service()
    loops = await svc.registry.list_all(thread_id=thread)
    out = [
        {
            "id": l.id,
            "status": l.status.value,
            "cadence": l.trigger_spec,
            "prompt": l.prompt,
            "next_fire_at": l.next_fire_at,
            "fire_count": l.fire_count,
            "last_error": l.last_error,
        }
        for l in loops
    ]
    return json.dumps(out, indent=2)


@tool
async def cancel_loop(loop_id: str) -> str:
    """Cancel a previously-scheduled loop by id. Idempotent."""
    svc = get_loops_service()
    ok = await svc.cancel_loop(loop_id)
    return f"Cancelled loop {loop_id}." if ok else f"No loop with id {loop_id}."


def get_loops_tools() -> list:
    """The tools to inject into agents that opt into loops."""
    return [schedule_recurring, schedule_wakeup, list_my_loops, cancel_loop]
