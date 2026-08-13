"""Offline tests for the NATIVE scheduler (AP-free cron/poll) — Phases 0-2.

Covers: the DB schema + scheduler queries, the cron parser + next-fire + lazy catch-up + bounded-run
retirement (pure, no network), and the concierge routing (a cron arms NATIVE with no AP flow; a gmail
push declines clearly when AP is absent). No integrations, no live server — API/logic level.
"""

import asyncio
import os
import sys
import time

_EVENTS = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "src", "cuga", "backend", "events")
)
if _EVENTS not in sys.path:
    sys.path.insert(0, _EVENTS)

os.environ.setdefault("EVENTS_VERIFY_ACTIONS", "0")  # deterministic (no LLM verifier)

import pytest  # noqa: E402
import native_scheduler as ns  # noqa: E402
from subscriptions import SubscriptionStore, Subscription  # noqa: E402


# ── schema + store queries ──────────────────────────────────────────────────
def test_native_fields_persist_and_due_query():
    s = SubscriptionStore(":memory:")
    now = 1_000_000.0
    s.upsert(
        Subscription(
            id="c1",
            mode="CRON",
            target_agent="pricebot",
            backend="native",
            interval_seconds=300,
            next_fire=now,
            prompt="check",
        )
    )
    # an AP-backed row must NOT show up in due() — only native rows
    s.upsert(
        Subscription(
            id="ap1", mode="CRON", target_agent="x", backend="react", ap_flow_id="flow-9", next_fire=now
        )
    )
    due = s.due(now + 1)
    assert [d.id for d in due] == ["c1"]
    assert due[0].interval_seconds == 300 and due[0].next_fire == now


def test_mark_fired_advances_and_counts():
    s = SubscriptionStore(":memory:")
    now = 1_000_000.0
    s.upsert(
        Subscription(
            id="c1",
            mode="POLL",
            target_agent="x",
            backend="native",
            interval_seconds=120,
            next_fire=now,
            prompt="p",
        )
    )
    s.mark_fired("c1", last_fire=now, next_fire=now + 120)
    g = s.get("c1")
    assert g.fire_count == 1 and g.next_fire == now + 120 and g.last_fire == now
    assert s.due(now + 1) == []  # no longer due until now+120
    assert [d.id for d in s.due(now + 121)] == ["c1"]


# ── cron parser + next-fire ──────────────────────────────────────────────────
def test_cron_daily_and_step():
    # next 08:00 daily
    t = time.localtime(ns.next_cron("0 8 * * *", time.time()))
    assert t.tm_hour == 8 and t.tm_min == 0
    # */5 minutes: 10:02 → 10:05
    base = time.mktime((2026, 1, 1, 10, 2, 0, 0, 0, -1))
    assert time.localtime(ns.next_cron("*/5 * * * *", base)).tm_min == 5


def test_cron_weekday_field():
    # "0 9 * * 1-5" = weekdays 9am. From a Saturday, next must be Monday.
    sat = time.mktime((2026, 1, 3, 12, 0, 0, 0, 0, -1))  # 2026-01-03 is a Saturday
    nxt = time.localtime(ns.next_cron("0 9 * * 1-5", sat))
    assert nxt.tm_wday == 0 and nxt.tm_hour == 9  # Monday 09:00


def test_next_fire_after_interval_is_lazy():
    sub = Subscription(
        id="x", mode="POLL", backend="native", target_agent="a", interval_seconds=300, next_fire=1.0
    )
    assert ns.next_fire_after(sub, 1_000_000.0) == 1_000_300.0  # now+interval, not backlog


# ── process_due: fire-once, reschedule, catch-up, bounded run ────────────────
def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


def test_process_due_fires_and_reschedules():
    s = SubscriptionStore(":memory:")
    now = time.time()
    s.upsert(
        Subscription(
            id="c1",
            mode="CRON",
            target_agent="x",
            backend="native",
            interval_seconds=300,
            next_fire=now,
            prompt="p",
        )
    )
    fired = []
    got = asyncio.run(ns.process_due(s, now + 1, lambda sub: _noop(fired, sub)))
    assert got == ["c1"] and fired == ["c1"]
    assert abs(s.get("c1").next_fire - (now + 1 + 300)) < 0.01


async def _noop(acc, sub):
    acc.append(sub.id)


def test_process_due_lazy_catch_up_fires_once():
    s = SubscriptionStore(":memory:")
    now = time.time()
    s.upsert(
        Subscription(
            id="c2",
            mode="POLL",
            target_agent="x",
            backend="native",
            interval_seconds=120,
            next_fire=now - 3600,
            prompt="p",
        )
    )  # 1h overdue
    fired = []
    asyncio.run(ns.process_due(s, now, lambda sub: _noop(fired, sub)))
    assert fired == ["c2"]  # fired ONCE, not 30x
    assert abs(s.get("c2").next_fire - (now + 120)) < 0.01


def test_process_due_bounded_run_retires_after_deadline():
    s = SubscriptionStore(":memory:")
    now = time.time()
    # next fire (now+300) is past expires_at (now+10) → delete after this fire
    s.upsert(
        Subscription(
            id="c3",
            mode="CRON",
            target_agent="x",
            backend="native",
            interval_seconds=300,
            next_fire=now,
            prompt="p",
            expires_at=now + 10,
        )
    )
    asyncio.run(ns.process_due(s, now + 1, lambda sub: _noop([], sub)))
    assert s.get("c3") is None


def test_cron_step_zero_is_refused_by_the_parser():
    """`*/0` is not a schedule. It used to reach the modulo inside _field_matches and raise
    ZeroDivisionError from deep inside a tick; now it is refused as a ValueError like any other
    malformed field, so a bad expression fails where it is PARSED rather than when it first fires."""
    for expr in ("*/0 * * * *", "* */0 * * *", "0 0 * * */0", "*/-1 * * * *"):
        with pytest.raises(ValueError):
            ns.next_cron(expr, time.time())


def test_process_due_retires_an_unschedulable_row_without_stalling_the_tick():
    """A malformed cron must cost ONE subscription, not every subscription behind it.

    The reschedule used to sit outside the per-subscription try. `next_cron` raises ValueError for
    an expression it can never satisfy and `_field_matches` raises on a bad token or `*/0`, so the
    exception escaped `process_due` entirely: every row after the bad one was skipped, and because
    the bad row stayed due it repeated on EVERY tick. One typo silently froze every later schedule.

    Ordering is explicit here — the bad row must come FIRST, or the test passes without the fix.
    """
    s = SubscriptionStore(":memory:")
    now = time.time()
    for sid, cron, interval in (("bad", "*/0 * * * *", 0), ("good", "", 300)):
        s.upsert(
            Subscription(
                id=sid,
                mode="CRON",
                target_agent="x",
                backend="native",
                interval_seconds=interval,
                cron_expr=cron,
                next_fire=now - 1,  # both due
                prompt="p",
            )
        )
    fired = []
    got = asyncio.run(ns.process_due(s, now, lambda sub: _noop(fired, sub)))

    assert "good" in got, "a later subscription was stalled by the unschedulable one"
    assert s.get("bad") is None, "the unschedulable row must be retired, not left due forever"
    assert s.get("good") is not None and s.get("good").next_fire > now


def test_scheduler_gate():
    os.environ["EVENTS_SCHEDULER"] = "ap"
    assert ns.enabled() is False
    os.environ["EVENTS_SCHEDULER"] = "native"
    assert ns.enabled() is True
    del os.environ["EVENTS_SCHEDULER"]
    assert ns.enabled() is True  # default native


# ── concierge routing: cron arms NATIVE (no AP); gmail push declines w/o AP ──
def _tools(engine):
    from agent_store import AgentStore
    from runtime import AgentStoreRuntime, AgentSpec
    from subscriptions import SubscriptionStore as _S
    import concierge
    import principal as _principal_mod

    rt = AgentStoreRuntime(agent_store=AgentStore(":memory:"))
    # agent_scope (<tenant>/<instance>) is where lookups happen; a bare "default" never matches and
    # sends the concierge down its live-LLM fallback. See test_arming_hitl._client.
    rt.upsert_agent(
        AgentSpec(name="cuga", prompt="x", integrations=[]), scope=_principal_mod.DEFAULT.agent_scope
    )
    store = _S(":memory:")
    tools = concierge.make_concierge_tools(rt, store=store, engine=engine, users=None)
    focf = next(t for t in tools if t.name == "find_or_create_flow")
    concierge._principal.set(_principal_mod.DEFAULT)
    concierge._origin.set("web:local")
    concierge._utterance.set("")
    return focf, store


def test_cron_arms_native_no_ap():
    os.environ["EVENTS_SCHEDULER"] = "native"
    focf, store = _tools(engine=None)  # engine=None → prove NO AP is needed
    reply = asyncio.run(
        focf.ainvoke({"agent": "cuga", "kind": "cron", "prompt": "say hello", "every_minutes": 5})
    )
    assert "native" in reply.lower() and "ARMED" in reply
    subs = store.list()
    assert len(subs) == 1
    sub = subs[0]
    assert sub.backend == "native" and sub.ap_flow_id is None
    assert sub.interval_seconds == 300 and sub.next_fire > 0
    del os.environ["EVENTS_SCHEDULER"]


def test_arming_refuses_an_unsatisfiable_cron_instead_of_storing_it():
    """A cron nobody can satisfy must be refused at ARM time, with a message a human can act on.

    Previously the first-fire calculation happened outside any guard, so `*/0` either raised out of
    the tool (a stack trace to the user) or — before the parser rejected it — armed a row that blew
    up the scheduler the moment it came due. Nothing should be stored either way.
    """
    os.environ["EVENTS_SCHEDULER"] = "native"
    focf, store = _tools(engine=None)
    reply = asyncio.run(
        focf.ainvoke({"agent": "cuga", "kind": "cron", "prompt": "say hello", "cron": "*/0 * * * *"})
    )
    assert "error" in reply.lower() and "schedule" in reply.lower(), reply
    assert store.list() == [], "an unsatisfiable schedule must not be armed"
    del os.environ["EVENTS_SCHEDULER"]


def test_poll_arms_native_no_ap():
    os.environ["EVENTS_SCHEDULER"] = "native"
    focf, store = _tools(engine=None)
    reply = asyncio.run(
        focf.ainvoke({"agent": "cuga", "kind": "poll", "prompt": "watch the value", "every_minutes": 2})
    )
    assert "native" in reply.lower()
    assert store.list()[0].backend == "native"
    del os.environ["EVENTS_SCHEDULER"]


def test_poll_arm_seeds_watch_state_threshold():
    """A POLL seeds a watch_state row (stateful delta); a CRON does not (Tier-0 always report)."""
    os.environ["EVENTS_SCHEDULER"] = "native"
    os.environ["EVENTS_POLL_LLM"] = "0"  # heuristic spec (offline)
    try:
        focf, store = _tools(engine=None)
        concierge_mod_utterance("ping me if IBM stock moves by 5%")
        asyncio.run(
            focf.ainvoke({"agent": "cuga", "kind": "poll", "prompt": "check IBM stock", "every_minutes": 2})
        )
        sid = store.list()[0].id
        ws = store.get_watch_state(sid)
        assert ws is not None and ws["kind"] == "threshold" and abs(ws["threshold"] - 0.05) < 1e-9
        # a CRON seeds nothing
        focf2, store2 = _tools(engine=None)
        asyncio.run(focf2.ainvoke({"agent": "cuga", "kind": "cron", "prompt": "say hi", "every_minutes": 5}))
        assert store2.get_watch_state(store2.list()[0].id) is None
    finally:
        del os.environ["EVENTS_SCHEDULER"]
        del os.environ["EVENTS_POLL_LLM"]


def concierge_mod_utterance(text):
    import concierge

    concierge._utterance.set(text)


def test_gmail_push_declines_without_ap():
    focf, store = _tools(engine=None)  # no AP engine
    reply = asyncio.run(
        focf.ainvoke(
            {
                "agent": "cuga",
                "kind": "push",
                "prompt": "when a new email arrives, summarize it",
                "source": "gmail",
                "event": "new_email",
            }
        )
    )
    low = reply.lower()
    assert "gmail" in low and ("activepieces" in low or "make up" in low)
    assert store.list() == []  # nothing armed


# ── startup race: the scheduler must not fire into a socket that isn't listening yet ──────────
def test_scheduler_waits_for_its_own_port_before_the_first_tick():
    """run_scheduler is launched from the lifespan hook, which runs BEFORE uvicorn accepts. A
    subscription already due at boot fired into a closed socket and was LOST — the per-sub except
    swallows it and next_fire has already advanced, so nothing retries. Observed live as
    `native fire cuga-e8493b failed: ConnectError: All connection attempts failed`.
    """

    async def scenario():
        srv_holder = {}

        async def _handler(reader, writer):
            writer.close()

        # 1. nothing is listening → the wait must NOT return immediately
        free = _free_port()
        waiter = asyncio.ensure_future(ns._await_loopback(str(free), timeout=10.0, interval=0.05))
        await asyncio.sleep(0.3)
        assert not waiter.done(), "returned before anything was listening"

        # 2. the server comes up → the wait resolves
        srv_holder["s"] = await asyncio.start_server(_handler, "127.0.0.1", free)
        assert await asyncio.wait_for(waiter, timeout=5.0) is True
        srv_holder["s"].close()

    asyncio.run(scenario())


def test_scheduler_gives_up_waiting_rather_than_disabling_itself():
    """A slow boot must not cost every future tick — time out and proceed."""
    t0 = time.time()
    got = asyncio.run(ns._await_loopback(str(_free_port()), timeout=0.5, interval=0.05))
    assert got is False and time.time() - t0 < 5.0


def _free_port() -> int:
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port
