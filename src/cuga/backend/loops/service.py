"""Loops service singleton: scheduler + in-process agent registry.

The service owns:
  - An APScheduler AsyncIOScheduler running on the current event loop.
  - A map `agent_name -> async_invoke(prompt, thread_id) -> str` so the
    runner can dispatch when a job fires.

Agents register themselves at construction (see CugaAgent / CugaSupervisor
when `enable_loops=True`). On service startup, all ACTIVE loops in the
registry are re-armed; loops whose owning agent isn't registered are marked
ORPHANED at fire time (the registry row stays so it shows up in the UI).
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import time
from typing import Awaitable, Callable, Dict, Optional

from cuga.backend.loops.models import Loop, LoopStatus, RunOutcome, TriggerKind, LoopRun
from cuga.backend.loops.registry import LoopsRegistry

logger = logging.getLogger(__name__)

# Context vars set during agent.invoke(), read by the schedule_* tools so
# they know which agent + thread the new loop should be bound to.
current_agent_name: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "cuga_loops_current_agent", default=None
)
current_thread_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "cuga_loops_current_thread", default=None
)
current_app_name: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "cuga_loops_current_app", default="cuga"
)
# Set by the runner before each fire so the registered invoke callable
# can identify which loop fired (e.g. to stamp source="loop", loop_id=…
# into a per-app run-history file).
current_loop_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "cuga_loops_current_loop_id", default=None
)

InvokeFn = Callable[[str, str], Awaitable[str]]  # (prompt, thread_id) -> answer

_singleton: Optional["LoopsService"] = None


def get_loops_service() -> "LoopsService":
    global _singleton
    if _singleton is None:
        _singleton = LoopsService()
    return _singleton


class LoopsService:
    def __init__(self):
        self.registry = LoopsRegistry()
        self._scheduler = None  # AsyncIOScheduler — lazy
        self._agents: Dict[str, InvokeFn] = {}
        self._app_name: str = "cuga"  # default app label
        self._started = False
        self._lock = asyncio.Lock()

    def set_app_name(self, app_name: str) -> None:
        """Set the app label used when this process creates new loops."""
        self._app_name = app_name

    @property
    def app_name(self) -> str:
        return self._app_name

    def register_agent(self, name: str, invoke_fn: InvokeFn) -> None:
        """Register an agent so the runner can dispatch loop fires to it."""
        if name in self._agents:
            logger.debug(f"agent '{name}' already registered with loops service — replacing")
        self._agents[name] = invoke_fn
        logger.info(f"registered agent '{name}' with loops service")

    def get_agent(self, name: str) -> Optional[InvokeFn]:
        return self._agents.get(name)

    async def start(self) -> None:
        """Start the scheduler and re-arm any ACTIVE loops in the registry."""
        async with self._lock:
            if self._started:
                return
            try:
                from apscheduler.schedulers.asyncio import AsyncIOScheduler
            except ImportError as e:
                raise RuntimeError(
                    "CUGA loops require APScheduler. Install it with: pip install apscheduler"
                ) from e
            self._scheduler = AsyncIOScheduler()
            self._scheduler.start()
            self._started = True
            logger.info("loops scheduler started")
            await self.registry.initialize()
            # Re-arm active loops, refreshing each one's next_fire_at
            loops = await self.registry.list_active_for_revival()
            for loop in loops:
                self._arm_loop(loop)
                nxt = self._next_fire_ts(loop.id)
                if nxt is not None:
                    await self.registry.update_next_fire(loop.id, nxt)
            logger.info(f"re-armed {len(loops)} active loops on startup")

    async def shutdown(self) -> None:
        async with self._lock:
            if not self._started:
                return
            try:
                self._scheduler.shutdown(wait=False)
            except Exception as e:
                logger.warning(f"scheduler shutdown error: {e}")
            self._scheduler = None
            self._started = False

    async def _ensure_started(self) -> None:
        if not self._started:
            await self.start()

    def _arm_loop(self, loop: Loop):
        """Register an APScheduler job for a loop. Returns the Job (or None)."""
        from apscheduler.triggers.cron import CronTrigger
        from apscheduler.triggers.interval import IntervalTrigger
        from apscheduler.triggers.date import DateTrigger
        from datetime import datetime, timedelta

        if loop.status != LoopStatus.ACTIVE:
            return None
        if loop.expires_at and loop.expires_at < time.time():
            asyncio.create_task(self.registry.update_status(loop.id, LoopStatus.EXPIRED))
            return None

        if loop.trigger_kind == TriggerKind.DELAY:
            # One-shot at next_fire_at
            run_at = (
                datetime.fromtimestamp(loop.next_fire_at)
                if loop.next_fire_at
                else datetime.now() + timedelta(seconds=loop.interval_seconds or 60)
            )
            trigger = DateTrigger(run_date=run_at)
        elif loop.trigger_kind == TriggerKind.INTERVAL:
            trigger = IntervalTrigger(seconds=loop.interval_seconds or 60)
        elif loop.trigger_kind == TriggerKind.CRON:
            trigger = CronTrigger.from_crontab(loop.cron)
        else:
            logger.error(f"unknown trigger kind {loop.trigger_kind} for loop {loop.id}")
            return None

        from cuga.backend.loops.runner import fire_loop

        end_date = datetime.fromtimestamp(loop.expires_at) if loop.expires_at else None
        try:
            return self._scheduler.add_job(
                fire_loop,
                trigger=trigger,
                kwargs={"loop_id": loop.id},
                id=loop.id,
                replace_existing=True,
                max_instances=1,
                misfire_grace_time=60,
                end_date=end_date,
            )
        except Exception as e:
            logger.exception(f"failed to arm loop {loop.id}: {e}")
            return None

    def _next_fire_ts(self, loop_id: str) -> Optional[float]:
        """Read APScheduler's idea of when this loop fires next (epoch sec)."""
        if not self._scheduler:
            return None
        try:
            job = self._scheduler.get_job(loop_id)
            if job and job.next_run_time:
                return job.next_run_time.timestamp()
        except Exception:
            pass
        return None

    async def register_loop(self, loop: Loop) -> None:
        await self._ensure_started()
        self._arm_loop(loop)
        nxt = self._next_fire_ts(loop.id)
        if nxt is not None:
            await self.registry.update_next_fire(loop.id, nxt)

    async def pause_loop(self, loop_id: str) -> bool:
        await self._ensure_started()
        loop = await self.registry.get(loop_id)
        if not loop:
            return False
        await self.registry.update_status(loop_id, LoopStatus.PAUSED)
        await self.registry.update_next_fire(loop_id, None)
        try:
            self._scheduler.remove_job(loop_id)
        except Exception:
            pass
        return True

    async def resume_loop(self, loop_id: str) -> bool:
        await self._ensure_started()
        loop = await self.registry.get(loop_id)
        if not loop:
            return False
        await self.registry.update_status(loop_id, LoopStatus.ACTIVE)
        loop.status = LoopStatus.ACTIVE
        self._arm_loop(loop)
        nxt = self._next_fire_ts(loop_id)
        if nxt is not None:
            await self.registry.update_next_fire(loop_id, nxt)
        return True

    async def cancel_loop(self, loop_id: str) -> bool:
        await self._ensure_started()
        try:
            self._scheduler.remove_job(loop_id)
        except Exception:
            pass
        await self.registry.update_status(loop_id, LoopStatus.CANCELLED)
        return True

    async def delete_loop(self, loop_id: str) -> bool:
        await self._ensure_started()
        try:
            self._scheduler.remove_job(loop_id)
        except Exception:
            pass
        await self.registry.delete(loop_id)
        return True

    async def run_now(self, loop_id: str) -> bool:
        """Trigger one immediate execution outside the schedule (for tests / UI)."""
        from cuga.backend.loops.runner import fire_loop
        loop = await self.registry.get(loop_id)
        if not loop:
            return False
        asyncio.create_task(fire_loop(loop_id=loop_id))
        return True
