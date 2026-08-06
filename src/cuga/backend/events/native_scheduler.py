"""The NATIVE scheduler — AP-free cron/poll, in-process.

This is the timer half of the "watch anything without Activepieces" story. A native subscription
(``backend='native'``, see :mod:`subscriptions`) carries its own schedule; this loop fires it by
POSTing the same ``/invoke`` envelope an AP schedule flow would — so everything downstream (the agent,
delivery, the runs log) is identical whether the tick came from AP or from here.

Design mirrors the existing patterns:
  • durable next-fire lives in the DB (survives restart), like the bounded-run expiry gate;
  • **lazy catch-up** — a tick missed while the server was down fires ONCE, not N times (no storm);
  • gated by ``EVENTS_SCHEDULER`` (``native`` default | ``ap`` to keep the old AP-schedule path).

Only ``backend='native'`` rows participate — AP-backed flows are still fired by Activepieces. The
core (``next_fire_after`` + ``process_due``) is pure/injectable so it unit-tests with no network.
No third-party deps: a compact standard-cron implementation is included.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

import httpx

log = logging.getLogger("events.native_scheduler")


def enabled() -> bool:
    """EVENTS_SCHEDULER: 'native' (default) runs this loop; 'ap' keeps cron/poll on the AP schedule
    piece (the legacy path). Read live so tests can toggle."""
    return os.environ.get("EVENTS_SCHEDULER", "native").split(" #", 1)[0].strip().lower() != "ap"


# ── standard cron (minute hour day-of-month month day-of-week) ─────────────────────────────────────
def _field_matches(spec: str, value: int, lo: int, hi: int) -> bool:
    """Does ``value`` match one cron field? Supports '*', 'a', 'a-b', 'a,b,c', '*/n', 'a-b/n'."""
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            step = int(step_s)
        if part == "*":
            start, end = lo, hi
        elif "-" in part:
            a, b = part.split("-", 1)
            start, end = int(a), int(b)
        else:
            start = end = int(part)
        if start <= value <= end and (value - start) % step == 0:
            return True
    return False


def _matches(expr: str, t: time.struct_time) -> bool:
    mn, hr, dom, mon, dow = expr.split()
    # cron day-of-week: 0 and 7 are both Sunday; python tm_wday is Mon=0..Sun=6
    py_dow = t.tm_wday
    cron_dow = 0 if py_dow == 6 else py_dow + 1  # → Sun=0..Sat=6
    dow_ok = _field_matches(dow.replace("7", "0"), cron_dow, 0, 6)
    return (
        _field_matches(mn, t.tm_min, 0, 59)
        and _field_matches(hr, t.tm_hour, 0, 23)
        and _field_matches(dom, t.tm_mday, 1, 31)
        and _field_matches(mon, t.tm_mon, 1, 12)
        and dow_ok
    )


def next_cron(expr: str, after: float) -> float:
    """Next epoch strictly after ``after`` matching a 5-field cron expression (local time). Scans
    minute-by-minute (bounded to ~366 days). Runs once per fire, not per tick, so the scan is cheap."""
    # start at the next whole minute after `after`
    t = int(after) - (int(after) % 60) + 60
    for _ in range(366 * 24 * 60):
        if _matches(expr, time.localtime(t)):
            return float(t)
        t += 60
    raise ValueError(f"cron {expr!r} produced no time within a year")


def next_fire_after(sub, now: float) -> float:
    """Compute a native subscription's next fire epoch. Interval schedules use lazy catch-up
    (``now + interval``, never a backlog of missed ticks); cron uses the next matching minute."""
    if sub.interval_seconds and sub.interval_seconds > 0:
        return now + sub.interval_seconds
    if sub.cron_expr:
        return next_cron(sub.cron_expr, now)
    return 0.0  # not schedulable → disables further firing


# ── the loop ───────────────────────────────────────────────────────────────────────────────────────
def _invoke_body(sub) -> dict:
    """The /invoke envelope for one scheduled tick — same shape a scheduled AP flow posts, so the
    downstream path (agent run, delivery, runs log) doesn't know the difference."""
    return {
        "text": sub.prompt,
        "agent": sub.target_agent,
        "deliver": True,  # CUGA-owned delivery (no AP send step)
        "source": {"type": "time", "name": sub.mode.lower() or "cron", "thread_id": sub.thread_id},
        "event": {"kind": "tick", "payload": {}},
        "subscription_id": sub.id,
        "expires_at": sub.expires_at or None,
    }


async def _fire_invoke(sub, *, port: str, token: str) -> None:
    body = _invoke_body(sub)
    headers = {"X-Gateway-Token": token} if token else {}
    async with httpx.AsyncClient(timeout=200) as c:
        r = await c.post(f"http://127.0.0.1:{port}/invoke", headers=headers, json=body)
        if r.status_code != 200:
            log.warning("native fire %s → HTTP %s %s", sub.id, r.status_code, r.text[:160])


async def process_due(store, now: float, fire_fn) -> list[str]:
    """Fire every due native subscription ONCE and reschedule it. Pure/injectable (``fire_fn(sub)``)
    so it unit-tests without a server. Returns the ids that fired. A bounded run whose NEXT fire would
    fall past ``expires_at`` is deleted instead (lazy end-of-life, mirrors the /invoke expiry gate)."""
    fired: list[str] = []
    for sub in store.due(now):
        if sub.expires_at and now > sub.expires_at:  # already past its deadline → retire, don't run
            store.delete(sub.id)
            log.info("native scheduler retired expired %s", sub.id)
            continue
        try:
            await fire_fn(sub)
            fired.append(sub.id)
        except Exception as e:  # noqa: BLE001 — one bad fire must not stall the whole loop
            # Some transport exceptions (httpx timeouts, cancelled tasks) carry an EMPTY str(), so
            # "native fire X failed: " told an operator nothing at all. Always name the type.
            log.warning("native fire %s failed: %s: %s", sub.id, type(e).__name__, e or "(no detail)")
        nxt = next_fire_after(sub, now)
        if not nxt or (sub.expires_at and nxt > sub.expires_at):
            store.delete(sub.id)  # bounded run complete (or unschedulable)
            log.info("native scheduler completed %s (no further fire)", sub.id)
        else:
            store.mark_fired(sub.id, last_fire=now, next_fire=nxt)
    return fired


async def _await_loopback(port: str, *, timeout: float = 60.0, interval: float = 0.5) -> bool:
    """Block until this process's own HTTP port accepts a connection (or ``timeout``).

    Returns True if it came up. On timeout we log and proceed anyway rather than disabling the
    scheduler: a slow boot must not cost every future tick.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            _r, w = await asyncio.open_connection("127.0.0.1", int(port))
            w.close()
            try:
                await w.wait_closed()
            except Exception:  # noqa: BLE001 — closing is best-effort
                pass
            return True
        except (OSError, ValueError):
            await asyncio.sleep(interval)
    log.warning("native scheduler: :%s never accepted within %.0fs — firing anyway", port, timeout)
    return False


async def run_scheduler(
    store, *, port: str, token: str, tick: float = 10.0, stop: asyncio.Event | None = None
) -> None:
    """Background loop: every ``tick`` seconds, fire due native subscriptions. Registered at boot the
    same way the channel pollers are. Cheap when idle (one indexed query per tick)."""
    if store is None:
        return
    log.info("native scheduler started (tick=%ss)", tick)
    # WAIT FOR OUR OWN FRONT DOOR. This loop is launched from the lifespan hook, which runs BEFORE
    # uvicorn starts accepting — and a subscription that was already due at boot fires on the very
    # first tick, into a socket that is not listening yet. That fire was lost outright: the failure
    # is caught per-sub and next_fire has already advanced, so nothing retries it. Harmless for a
    # 1-minute cron, a whole missed day for a daily one.
    await _await_loopback(port)

    async def _fire(sub):
        await _fire_invoke(sub, port=port, token=token)

    while not (stop and stop.is_set()):
        try:
            await process_due(store, time.time(), _fire)
        except Exception as e:  # noqa: BLE001
            log.warning("native scheduler tick error: %s", e)
        # sleep in small slices so `stop` is honored promptly
        slept = 0.0
        while slept < tick and not (stop and stop.is_set()):
            await asyncio.sleep(min(1.0, tick - slept))
            slept += 1.0
