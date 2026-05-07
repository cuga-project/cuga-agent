"""Job executor — what APScheduler calls when a loop fires.

Looks up the loop in the registry, finds the registered agent by name,
invokes it on the loop's thread_id, and records the run.
"""
from __future__ import annotations

import logging
import time

from cuga.backend.loops.models import LoopRun, LoopStatus, RunOutcome, TriggerKind
from cuga.backend.loops.service import current_loop_id, get_loops_service

logger = logging.getLogger(__name__)


async def fire_loop(*, loop_id: str) -> None:
    svc = get_loops_service()
    loop = await svc.registry.get(loop_id)
    started = time.time()

    if not loop:
        logger.warning(f"fire_loop: loop {loop_id} not found")
        return

    if loop.status != LoopStatus.ACTIVE:
        logger.debug(f"fire_loop: loop {loop_id} not active (status={loop.status}), skipping")
        return

    invoke_fn = svc.get_agent(loop.agent_name)
    if invoke_fn is None:
        logger.warning(
            f"fire_loop: agent '{loop.agent_name}' not registered in this process — marking orphaned"
        )
        await svc.registry.update_status(loop_id, LoopStatus.ORPHANED)
        await svc.registry.insert_run(
            LoopRun(
                loop_id=loop_id,
                started_at=started,
                finished_at=time.time(),
                outcome=RunOutcome.MISSED,
                error=f"agent '{loop.agent_name}' not registered",
            )
        )
        return

    logger.info(
        f"fire_loop: firing {loop_id} (agent={loop.agent_name}, thread={loop.thread_id})"
    )

    answer: str = ""
    err: str | None = None
    ok = True
    # Set context var so the invoke callable can identify the firing loop
    # (e.g. to stamp source="loop"/loop_id into per-app run history).
    current_loop_id.set(loop_id)
    try:
        answer = await invoke_fn(loop.prompt, loop.thread_id)
    except Exception as e:
        ok = False
        err = f"{type(e).__name__}: {e}"
        logger.exception(f"fire_loop {loop_id} raised: {e}")

    finished = time.time()
    summary = (answer or "")[:2000] if answer else None
    await svc.registry.record_fire(loop_id, ok=ok, summary=summary, error=err)
    await svc.registry.insert_run(
        LoopRun(
            loop_id=loop_id,
            started_at=started,
            finished_at=finished,
            outcome=RunOutcome.OK if ok else RunOutcome.ERROR,
            summary=summary,
            error=err,
        )
    )

    # One-shot delays self-cancel after firing; recurring loops update
    # next_fire_at so the UI reflects APScheduler's next computed run time.
    if loop.trigger_kind == TriggerKind.DELAY:
        await svc.registry.update_status(loop_id, LoopStatus.CANCELLED)
        await svc.registry.update_next_fire(loop_id, None)
    else:
        nxt = svc._next_fire_ts(loop_id)
        await svc.registry.update_next_fire(loop_id, nxt)
