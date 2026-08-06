"""Every store, against a REAL PostgreSQL — the proof that local and deployed run the same thing.

Skipped unless a Postgres is reachable. Bring one up with:

    podman run -d --name cuga-events-pg -e POSTGRES_USER=cuga -e POSTGRES_PASSWORD=cuga_dev_pw \\
        -e POSTGRES_DB=cuga_events -p 5433:5432 docker.io/library/postgres:16-alpine

    EVENTS_TEST_PG_DSN=postgresql://cuga:cuga_dev_pw@localhost:5433/cuga_events pytest tests/events

WHY THIS FILE EXISTS. The offline suite runs on SQLite because 360 hermetic tests must not need a
database server. That is a deliberate compromise, and it leaves a gap: the SQL that ships is not the
SQL that was tested. These tests close it by exercising each store's real queries — upserts with
ON CONFLICT, the partial unique index behind dedup, tenant scoping, JSON round-trips, the
scheduler's due() query — against the engine we actually deploy.
"""

import json
import os
import time
import uuid

import pytest

from cuga.backend.events import db as _db
from cuga.backend.events.agent_store import AgentSpec, AgentStore
from cuga.backend.events.identity import IdentityMap
from cuga.backend.events.now_runs import NowRunStore
from cuga.backend.events.oauth import OAuthAppStore
from cuga.backend.events.subscriptions import (DuplicateSubscription, Subscription,
                                               SubscriptionStore)
from cuga.backend.events.users import UserStore

DSN = os.environ.get("EVENTS_TEST_PG_DSN", "")


def _reachable(dsn: str) -> bool:
    if not dsn:
        return False
    try:
        _db.connect(dsn).execute("SELECT 1")
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(
    not _reachable(DSN),
    reason="set EVENTS_TEST_PG_DSN to a reachable PostgreSQL to run the Postgres store tests")


@pytest.fixture()
def dsn():
    """A DSN with every events table dropped, so each test starts from a real migrate-on-open."""
    conn = _db.connect(DSN)
    for t in ("subscription", "watch_state", "pending_arm", "app_user", "identity",
              "link_token", "now_run", "agent", "oauth_app"):
        conn.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    conn.commit()
    return DSN


def _sub(sub_id="cuga-a1", tenant="acme/prod/alice", dedup="k1", **kw):
    now = time.time()
    base = dict(
        id=sub_id, mode="CRON", target_agent="cuga", tenant=tenant, backend="native",
        source_type="time", source_connector="interval", ap_flow_id=None, deliver_to=["slack"],
        thread_id=f"{tenant}::gw:slack:C1#1", prompt="The IBM stock price.", dedup_key=dedup,
        flow_name=f"flow-{sub_id}", interval_seconds=300, cron_expr="", next_fire=now + 300,
        expires_at=0.0, config={"emoji": "bug", "n": 1})
    base.update(kw)
    return Subscription(**base)


# ── the seam itself ───────────────────────────────────────────────────────────────────────────
def test_connect_picks_postgres(dsn):
    assert _db.is_postgres(dsn) is True
    assert _db.connect(dsn).backend == "postgres"


def test_columns_replaces_pragma(dsn):
    SubscriptionStore(dsn)                       # migrate-on-open creates the table
    cols = _db.connect(dsn).columns("subscription")
    # every column the migrate path adds must be visible, or migrate-on-open re-ALTERs forever
    for c in ("id", "mode", "tenant", "dedup_key", "flow_name", "event", "config",
              "interval_seconds", "cron_expr", "next_fire", "last_fire", "fire_count", "expires_at"):
        assert c in cols, f"{c} missing from information_schema"
    assert _db.connect(dsn).columns("no_such_table") == set()


def test_migrate_is_idempotent(dsn):
    for _ in range(3):
        SubscriptionStore(dsn)                   # re-opening must not raise or duplicate columns
    assert "dedup_key" in _db.connect(dsn).columns("subscription")


def test_row_supports_name_and_index_access(dsn):
    conn = _db.connect(dsn)
    r = conn.execute("SELECT 1 AS a, 'x' AS b").fetchone()
    assert r["a"] == 1 and r["b"] == "x"
    assert r[0] == 1 and r[1] == "x"             # the OAuth store indexes positionally
    assert dict(r) == {"a": 1, "b": "x"}
    assert set(r.keys()) == {"a", "b"}


def test_placeholder_rewrite_handles_quotes_and_percent():
    # ? inside a literal must survive; a literal % must be escaped for psycopg
    out = _db._to_pg_placeholders("SELECT ? WHERE x = 'a?b' AND y LIKE '%z%'")
    assert out == "SELECT %s WHERE x = 'a?b' AND y LIKE '%%z%%'"


def test_failed_statement_does_not_poison_the_connection(dsn):
    """Postgres aborts the whole transaction on error; without a rollback every later query fails."""
    conn = _db.connect(dsn)
    with pytest.raises(Exception):
        conn.execute("SELECT * FROM definitely_not_a_table")
    assert conn.execute("SELECT 42 AS n").fetchone()["n"] == 42     # still usable


# ── subscriptions: the store that matters most ────────────────────────────────────────────────
def test_subscription_round_trip_with_json_columns(dsn):
    s = SubscriptionStore(dsn)
    s.upsert(_sub())
    got = s.list(status="active")
    assert len(got) == 1
    assert got[0].deliver_to == ["slack"]            # JSON column
    assert got[0].config == {"emoji": "bug", "n": 1}  # JSON column
    assert got[0].interval_seconds == 300


def test_upsert_updates_in_place(dsn):
    s = SubscriptionStore(dsn)
    s.upsert(_sub())
    s.upsert(_sub(prompt="Changed."))
    rows = s.list(status="active")
    assert len(rows) == 1 and rows[0].prompt == "Changed."


def test_dedup_unique_index_is_enforced(dsn):
    s = SubscriptionStore(dsn)
    s.upsert(_sub("cuga-a1", dedup="same"))
    with pytest.raises(DuplicateSubscription):
        s.upsert(_sub("cuga-a2", dedup="same"))


def test_tenant_isolation_on_dedup_lookup(dsn):
    """The cross-tenant leak we fixed on SQLite must not reappear on Postgres."""
    s = SubscriptionStore(dsn)
    s.upsert(_sub("cuga-a1", tenant="acme/prod/alice", dedup="shared-key"))
    assert s.find_by_dedup_key("shared-key", scope="acme/prod/alice") is not None
    assert s.find_by_dedup_key("shared-key", scope="globex/prod/bob") is None


def test_scheduler_due_query(dsn):
    s = SubscriptionStore(dsn)
    now = time.time()
    s.upsert(_sub("cuga-soon", dedup="d1", next_fire=now + 10))
    s.upsert(_sub("cuga-later", dedup="d2", next_fire=now + 10_000))
    due = {x.id for x in s.due(now + 60)}
    assert due == {"cuga-soon"}
    s.mark_fired("cuga-soon", last_fire=now + 60, next_fire=now + 360)
    assert {x.id for x in s.due(now + 60)} == set()


def test_delete_and_status_filter(dsn):
    s = SubscriptionStore(dsn)
    s.upsert(_sub("cuga-a1", dedup="d1"))
    s.upsert(_sub("cuga-a2", dedup="d2"))
    s.delete("cuga-a1")
    assert {x.id for x in s.list(status="active")} == {"cuga-a2"}


def test_pending_arm_ttl_round_trip(dsn):
    """The HITL parked draft — a confirm card must survive until it is answered or expires."""
    s = SubscriptionStore(dsn)
    s.set_pending_arm("gw:slack:C1", "confirm", {"prompt": "The IBM stock price."}, 600)
    got = s.get_pending_arm("gw:slack:C1")
    assert got and got["state"] == "confirm"
    s.set_pending_arm("gw:slack:C2", "confirm", {"prompt": "x"}, -1)   # already expired
    assert s.get_pending_arm("gw:slack:C2") is None


# ── the other five stores ─────────────────────────────────────────────────────────────────────
def test_user_store(dsn):
    u = UserStore(dsn)
    u.add("alice", email="alice@example.com", roles=["admin"], tenant="acme")
    got = u.get("alice", tenant="acme")
    assert got and got.email == "alice@example.com" and got.roles == ["admin"]
    assert u.by_email("alice@example.com", tenant="acme").user_id == "alice"
    assert u.get("alice", tenant="globex") is None          # tenant scoped
    u.add("alice", email="new@example.com", roles=["admin"], tenant="acme")   # ON CONFLICT update
    assert u.get("alice", tenant="acme").email == "new@example.com"


def test_identity_map_and_link_tokens(dsn):
    i = IdentityMap(dsn)
    i.link("acme", "slack", "U123", "alice")
    assert i.resolve("acme", "slack", "U123") == "alice"
    assert i.resolve("globex", "slack", "U123") is None     # tenant scoped
    i.link("acme", "slack", "U123", "bob")                  # ON CONFLICT update
    assert i.resolve("acme", "slack", "U123") == "bob"
    tok = i.issue_token("acme", "carol", "telegram")
    assert i.redeem_token(tok, "T999") == "carol"
    assert i.redeem_token(tok, "T999") is None              # single use
    i.unlink("acme", "slack", "U123")
    assert i.resolve("acme", "slack", "U123") is None


def test_agent_store(dsn):
    a = AgentStore(dsn)
    a.upsert("acme", AgentSpec(name="pricebot", prompt="prices", backend="cuga",
                               mcp_servers=["cuga-finance"], builtin_tools=[], channels=["slack"]))
    got = a.get("acme", "pricebot")
    assert got and got.mcp_servers == ["cuga-finance"] and got.channels == ["slack"]
    assert a.get("globex", "pricebot") is None
    assert [x.name for x in a.list("acme")] == ["pricebot"]


def test_now_run_store(dsn):
    r = NowRunStore(dsn)
    rid = r.add(scope="acme", agent="cuga", channel="slack", prompt="p", answer="a",
                mcp=["cuga-finance"], tools=["get_stock_quote"], ms=1234)
    rows = r.list(scope="acme")
    assert len(rows) == 1 and rows[0]["answer"] == "a"
    got = r.get(rid)
    assert got["prompt"] == "p"
    assert got["mcp"] == ["cuga-finance"] and got["tools"] == ["get_stock_quote"]   # JSON columns
    assert r.list(scope="globex") == []


def test_oauth_app_store_positional_row_access(dsn):
    """This store reads rows by INDEX (r[0], r[1]) — the case a dict-only row type would break."""
    o = OAuthAppStore(dsn)
    o.set("acme", "github", "cid", "csecret")
    assert o.get("acme", "github", "client_id") == "cid"
    assert o.get("acme", "github", "client_secret") == "csecret"
    assert o.get("globex", "github", "client_id") == ""
    st = {row["app"]: row for row in o.status("acme")}
    assert st["github"]["configured"] is True


# ── the whole point: state outlives the process ───────────────────────────────────────────────
def test_state_survives_a_new_process(dsn):
    """No snapshotting, no restore, no mount — a second 'process' just connects and sees the data.

    This is what makes Postgres the right answer: durability is a property of the database, not of
    a background loop that has to be running and configured correctly.
    """
    first = SubscriptionStore(dsn)
    first.upsert(_sub("cuga-durable", dedup="dur"))
    del first                                    # the process goes away

    second = SubscriptionStore(dsn)              # a brand-new pod, empty local disk
    rows = second.list(status="active")
    assert len(rows) == 1 and rows[0].id == "cuga-durable"
    assert rows[0].interval_seconds == 300       # still schedulable → the scheduler resumes it


def test_two_processes_share_one_database(dsn):
    """Two connections, as a second replica would have. SQLite-on-a-mount cannot do this at all."""
    a, b = SubscriptionStore(dsn), SubscriptionStore(dsn)
    a.upsert(_sub("cuga-x", dedup="dx"))
    assert {s.id for s in b.list(status="active")} == {"cuga-x"}
    b.delete("cuga-x")
    assert a.list(status="active") == []
