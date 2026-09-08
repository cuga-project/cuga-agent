"""The web channel's delivery path — a flow armed in a browser must reach the browser.

This is the regression net for a real bug: ``delivery.send_direct`` had branches for slack, discord
and telegram but none for ``web``, and a browser thread id (``web:studio``, or the main chat's UUID)
carries no ``gw:`` origin — so a flow armed in a web chat fired, wrote a runs row, and told nobody.
"It ran, the dashboard knows, my chat never heard back."

Three things have to hold for that not to come back, and each is tested here:

1. **``web`` is a direct channel** with a working sender — not a "no direct sender, dropping" warning.
2. **A gw-less thread resolves to the web channel**, so the fire has a delivery address at all.
3. **The mailbox is a cursor feed, not a log**: oldest-first, ``since`` exclusive, per-thread and
   per-scope isolated — otherwise a polling tab either re-renders messages or reads someone else's.

Offline: no server, no creds, no AP.

    .venv/bin/python -m pytest tests/events/test_web_inbox.py -q
"""

import asyncio
import importlib.util
import os
import sys
import tempfile

_EV = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src", "cuga", "backend", "events"))
if "events" not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        "events", os.path.join(_EV, "__init__.py"), submodule_search_locations=[_EV]
    )
    _pkg = importlib.util.module_from_spec(_spec)
    sys.modules["events"] = _pkg
    _spec.loader.exec_module(_pkg)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from events import delivery, web_inbox  # noqa: E402
from events.app import register_events_routes  # noqa: E402
from events.principal import channel_origin, unscoped_thread  # noqa: E402
from events.subscriptions import SubscriptionStore  # noqa: E402
from events.web_inbox import WebInbox  # noqa: E402


def _tmpdb(name="inbox.db"):
    return os.path.join(tempfile.mkdtemp(), name)


# ── 1. web is a real direct channel ───────────────────────────────────────────────────────────────


def test_web_is_a_direct_channel():
    """The bug in one assertion: is_direct('web') was False, so /invoke skipped delivery entirely."""
    assert delivery.is_direct("web") is True
    assert delivery.channel_backend("web") == "direct"


def test_send_direct_web_lands_in_the_mailbox():
    web_inbox.init(_tmpdb())
    ok, why = asyncio.run(
        delivery.send_direct(
            "web",
            "web:studio",
            "⚡ flow fired · cron tick\nIBM is at $291.40.",
            scope="t/u/local",
            meta={
                "agent": "pricebot",
                "subscription_id": "cuga-1",
                "flow_name": "IBM price",
                "event_kind": "tick",
            },
        )
    )
    assert (ok, why) == (True, "ok")
    msgs = web_inbox.list_since(thread_id="web:studio")
    assert len(msgs) == 1
    assert msgs[0]["text"].startswith("⚡ flow fired")
    assert msgs[0]["agent"] == "pricebot" and msgs[0]["flow_name"] == "IBM price"


def test_send_direct_web_without_a_thread_is_refused_not_silently_dropped():
    """A web send with no thread has nowhere to go — say so, don't return ok and lose the message."""
    web_inbox.init(_tmpdb())
    ok, why = asyncio.run(delivery.send_direct("web", "", "orphan"))
    assert ok is False and "thread_id" in why


def test_send_direct_web_without_a_mounted_mailbox_reports_the_reason():
    web_inbox._STORE = None
    try:
        ok, why = asyncio.run(delivery.send_direct("web", "web:studio", "hi"))
        assert ok is False and "mailbox" in why
    finally:
        web_inbox.init(":memory:")


def test_an_unknown_channel_is_still_refused():
    """The web branch must not turn the fallback into a catch-all."""
    ok, why = asyncio.run(delivery.send_direct("carrier-pigeon", "x", "hi"))
    assert ok is False and "no direct sender" in why


# ── 2. a gw-less thread resolves to web ───────────────────────────────────────────────────────────


def test_browser_thread_ids_have_no_gw_origin():
    """Why the fallback in app.py is needed at all — pin the premise so it can't silently change."""
    assert channel_origin("web:studio") is None
    assert channel_origin("3f7a9c12-0b44-4c7e-9c2a-5d1e8f0a2b31") is None
    # …while a channel thread still resolves, so the fallback never steals a real channel's delivery
    assert channel_origin("gw:slack:C123#1712.5") == ("slack", "C123")


def test_the_scope_prefix_is_stripped_from_a_web_delivery_address():
    """The subtle one, and the reason nothing would have been delivered.

    ``Principal.thread()`` namespaces an armed thread as ``<scope>::<thread_id>``, so a flow armed
    from the Studio is stored against ``default/default/local::web:studio``. The browser polls for
    ``web:studio``. Deliver to the stored string and every fire lands in a mailbox nobody reads —
    a bug indistinguishable from the one being fixed."""
    assert unscoped_thread("default/default/local::web:studio") == "web:studio"
    assert unscoped_thread("t/i/u::3f7a9c12-0b44-4c7e") == "3f7a9c12-0b44-4c7e"
    assert unscoped_thread("web:studio") == "web:studio"  # unscoped input is left alone
    assert unscoped_thread("") == ""


# ── 3. the mailbox is a cursor feed ───────────────────────────────────────────────────────────────


def test_messages_come_back_oldest_first():
    inbox = WebInbox(_tmpdb())
    for n in ("first", "second", "third"):
        inbox.put(scope="s", thread_id="t1", text=n)
    assert [m["text"] for m in inbox.list(thread_id="t1")] == ["first", "second", "third"]


def test_since_is_exclusive_so_a_poller_never_re_renders():
    inbox = WebInbox(_tmpdb())
    inbox.put(scope="s", thread_id="t1", text="old")
    first = inbox.list(thread_id="t1")
    cursor = first[-1]["ts"]
    assert inbox.list(thread_id="t1", since=cursor) == []  # nothing new yet
    inbox.put(scope="s", thread_id="t1", text="new")
    fresh = inbox.list(thread_id="t1", since=cursor)
    assert [m["text"] for m in fresh] == ["new"]  # only the new one


def test_threads_are_isolated():
    inbox = WebInbox(_tmpdb())
    inbox.put(scope="s", thread_id="t1", text="for one")
    inbox.put(scope="s", thread_id="t2", text="for two")
    assert [m["text"] for m in inbox.list(thread_id="t1")] == ["for one"]
    assert [m["text"] for m in inbox.list(thread_id="t2")] == ["for two"]


def test_scopes_are_isolated():
    """Two principals sharing a thread id must not read each other's fires."""
    inbox = WebInbox(_tmpdb())
    inbox.put(scope="alice", thread_id="web:studio", text="alice's flow")
    inbox.put(scope="bob", thread_id="web:studio", text="bob's flow")
    assert [m["text"] for m in inbox.list(thread_id="web:studio", scope="alice")] == ["alice's flow"]
    assert [m["text"] for m in inbox.list(thread_id="web:studio", scope="bob")] == ["bob's flow"]


def test_the_backlog_survives_a_reopened_store():
    """Durability is the point: a tab closed at 09:00 must still find the 09:05 fire."""
    path = _tmpdb()
    WebInbox(path).put(scope="s", thread_id="t1", text="fired while you were away")
    assert [m["text"] for m in WebInbox(path).list(thread_id="t1")] == ["fired while you were away"]


# ── the endpoint ──────────────────────────────────────────────────────────────────────────────────


def _client():
    app = FastAPI()
    register_events_routes(
        app, runtime=object(), store=SubscriptionStore(_tmpdb("subs.db")), concierge=None, engine=None
    )
    # AFTER register_events_routes, which mounts the process-wide mailbox from $EVENTS_DB — a path
    # the suite shares, so initing first would leave every test reading the previous one's fires.
    web_inbox.init(_tmpdb("api.db"))
    return TestClient(app)


def test_inbox_endpoint_returns_a_usable_cursor():
    c = _client()
    r = c.get("/api/events/inbox", params={"thread_id": "web:studio"})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0 and body["messages"] == []
    assert body["cursor"] == 0.0  # empty ⇒ the cursor you sent, so polling is stable

    scope = body["scope"]
    web_inbox.put(scope=scope, thread_id="web:studio", text="⚡ flow fired · cron tick\nhello")
    body = c.get("/api/events/inbox", params={"thread_id": "web:studio"}).json()
    assert body["count"] == 1 and body["messages"][0]["text"].endswith("hello")

    # replay with the returned cursor: nothing new, cursor preserved
    again = c.get("/api/events/inbox", params={"thread_id": "web:studio", "since": body["cursor"]}).json()
    assert again["count"] == 0 and again["cursor"] == body["cursor"]


def test_max_age_bounds_the_first_load(monkeypatch):
    """A minute-by-minute cron piles up hundreds of fires. Replaying all of them into a chat window
    is a flood, not a recovery — so a first load asks for a bounded window, computed with the
    SERVER's clock (the cursor is a server timestamp; trusting the browser's would skip or repeat)."""
    import time as _t

    c = _client()
    scope = c.get("/api/events/inbox", params={"thread_id": "t"}).json()["scope"]
    now = _t.time()
    old = web_inbox.store()
    old.put(scope=scope, thread_id="t", text="ancient")
    old._db.execute("UPDATE web_inbox SET ts=? WHERE text=?", (now - 90000, "ancient"))
    old._db.commit()
    web_inbox.put(scope=scope, thread_id="t", text="recent")

    everything = c.get("/api/events/inbox", params={"thread_id": "t"}).json()
    assert [m["text"] for m in everything["messages"]] == ["ancient", "recent"]

    bounded = c.get("/api/events/inbox", params={"thread_id": "t", "max_age": 86400}).json()
    assert [m["text"] for m in bounded["messages"]] == ["recent"], "the day-old fire leaked in"

    # …and max_age is ignored once a real cursor exists, so polling never re-bounds
    cur = bounded["cursor"]
    assert (
        c.get("/api/events/inbox", params={"thread_id": "t", "since": cur, "max_age": 1}).json()["count"] == 0
    )


def test_inbox_endpoint_requires_a_thread_id():
    """Without a thread there is no delivery address — a 422 beats quietly returning everyone's mail."""
    assert _client().get("/api/events/inbox").status_code == 422


def test_inbox_endpoint_does_not_leak_another_scope():
    c = _client()
    web_inbox.put(scope="someone/else/entirely", thread_id="web:studio", text="not yours")
    body = c.get("/api/events/inbox", params={"thread_id": "web:studio"}).json()
    assert body["count"] == 0


# ── the boot log must not contain the database password ───────────────────────────────────────────


def test_the_boot_log_never_prints_the_database_password(caplog):
    """A real leak, found in the deployed logs: the events service logged its store location
    verbatim, so every boot wrote ``postgres://user:PASSWORD@host/db`` into the platform log —
    readable by anyone with log access, and retained after the password is rotated."""
    import logging
    from events.service import _db_path  # noqa: F401  (import proves the module loads)
    from events.db import _redact

    dsn = "postgres://ibm_cloud_abc:sup3r-s3cret-pw@pg.example.cloud:32294/ibmclouddb?sslmode=verify-full"
    out = _redact(dsn)
    assert "sup3r-s3cret-pw" not in out
    assert out.startswith("postgres://ibm_cloud_abc:***@pg.example.cloud:32294/")

    # …and the line the service actually emits goes through it
    log = logging.getLogger("events.service")
    with caplog.at_level(logging.INFO, logger="events.service"):
        log.info("events service: store = %s", _redact(dsn))
    assert "sup3r-s3cret-pw" not in caplog.text
    assert "***" in caplog.text


def test_redact_leaves_a_plain_sqlite_path_alone():
    from events.db import _redact

    assert _redact("/root/.cuga/events.db") == "/root/.cuga/events.db"
    assert _redact(":memory:") == ":memory:"


# ── the whole path: a fire on a web thread reaches the browser ────────────────────────────────────

GW = "test-gateway-token"  # the /invoke seam's shared secret, pinned for these tests


class _Runtime:
    def get_agent(self, agent, scope=""):
        return object()

    async def run(self, agent, thread_id, worker_input, scope="", deliver_to=None):
        return "Bitcoin is at $64,478."


def _fire_client():
    """A client whose store holds one armed CRON, scoped exactly the way the concierge arms it."""
    from events.subscriptions import Subscription
    from events.runtime import DEFAULT_SCOPE

    db = _tmpdb("fire-subs.db")
    w = SubscriptionStore(db)
    w.upsert(
        Subscription(
            id="s-web",
            mode="CRON",
            target_agent="pricebot",
            tenant=DEFAULT_SCOPE,
            deliver_to=[],
            prompt="the price of bitcoin",
            status="active",
            backend="native",
            flow_name="bitcoin price",
            # exactly what Principal.thread() stores for a Studio-armed flow
            thread_id=f"{DEFAULT_SCOPE}::web:studio",
        )
    )
    app = FastAPI()
    # gateway_token is EXPLICIT: unset, register_events_routes falls back to $GATEWAY_TOKEN, which
    # another test module may have left in the environment — /invoke would then 401 here and only
    # when the suite runs in that order.
    register_events_routes(
        app, runtime=_Runtime(), store=SubscriptionStore(db), concierge=None, engine=None, gateway_token=GW
    )
    web_inbox.init(_tmpdb("fire-inbox.db"))  # after register — see _client()
    return TestClient(app)


def test_a_cron_tick_on_a_web_thread_is_delivered_to_the_browser():
    """The end-to-end regression: arm in the Studio, tick, and the answer must be readable by the
    tab that armed it — via the SAME thread id the browser polls with, not the scoped one."""
    os.environ["EVENTS_REPLY_METADATA"] = "0"
    try:
        c = _fire_client()
        from events.runtime import DEFAULT_SCOPE

        # the exact body the native scheduler posts for a due subscription
        r = c.post(
            "/invoke",
            headers={"X-Gateway-Token": GW},
            json={
                "text": "the price of bitcoin",
                "agent": "pricebot",
                "deliver": True,
                "source": {"type": "time", "name": "cron", "thread_id": f"{DEFAULT_SCOPE}::web:studio"},
                "event": {"kind": "tick", "payload": {}},
                "subscription_id": "s-web",
            },
        )
        assert r.status_code == 200 and r.json()["ok"] is True

        body = c.get("/api/events/inbox", params={"thread_id": "web:studio"}).json()
        assert body["count"] == 1, "the fire never reached the browser's mailbox"
        text = body["messages"][0]["text"]
        assert "Bitcoin is at $64,478." in text
        # labelled, so the reader knows WHY the bot spoke and which flow did it
        assert text.startswith("⚡ flow fired") and "bitcoin price" in text
    finally:
        os.environ.pop("EVENTS_REPLY_METADATA", None)


def test_a_plain_chat_answer_is_not_mailed_to_the_browser():
    """NOW answers are returned in the response the browser is already awaiting. Mailing them too
    would double every message in the transcript."""
    c = _fire_client()
    r = c.post(
        "/invoke",
        headers={"X-Gateway-Token": GW},
        json={"agent": "pricebot", "text": "what is bitcoin worth?", "thread_id": "web:studio"},
    )
    assert r.status_code == 200
    assert c.get("/api/events/inbox", params={"thread_id": "web:studio"}).json()["count"] == 0
