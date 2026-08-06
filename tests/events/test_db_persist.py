"""Durable state: snapshot → instance replaced → restore. The regression these guard is real —
a cron armed from Slack at 11:12 on 2026-08-05 vanished when Code Engine started a new pod at
11:24, because the container filesystem is ephemeral and nothing copied the DB anywhere.
"""

import asyncio
import os
import sqlite3
import time

import pytest

from cuga.backend.events import db_persist
from cuga.backend.events.subscriptions import Subscription, SubscriptionStore


def _cron(sub_id="cuga-043b82", tenant="default/default/admin"):
    now = time.time()
    return Subscription(
        id=sub_id, mode="CRON", target_agent="cuga", tenant=tenant, backend="native",
        source_type="time", source_connector="interval", ap_flow_id=None, deliver_to=["slack"],
        thread_id=f"{tenant}::gw:slack:C0BEYJ9NATB#1785942739", prompt="The IBM stock price.",
        dedup_key=f"cuga|interval|300|{sub_id}", flow_name=f"native-cron-{sub_id}",
        interval_seconds=300, cron_expr="", next_fire=now + 300, expires_at=0.0, config={})


# ── the headline case ─────────────────────────────────────────────────────────────────────────
def test_armed_flow_survives_instance_replacement(tmp_path):
    """THE regression: arm on pod A, pod A is replaced by pod B with an empty disk, flow is back."""
    backup = str(tmp_path / "store" / "events.db")
    pod_a = str(tmp_path / "podA" / "events.db")
    os.makedirs(os.path.dirname(pod_a), exist_ok=True)

    store = SubscriptionStore(pod_a)
    store.upsert(_cron())
    assert len(store.list(status="active")) == 1
    assert db_persist.snapshot(pod_a, backup) is True

    # Pod B: a brand-new container. Different local path, nothing on disk.
    pod_b = str(tmp_path / "podB" / "events.db")
    assert not os.path.exists(pod_b)
    assert db_persist.restore(pod_b, backup) is True

    revived = SubscriptionStore(pod_b).list(status="active")
    assert len(revived) == 1
    assert revived[0].id == "cuga-043b82"
    assert revived[0].interval_seconds == 300          # still schedulable → the scheduler resumes it
    assert revived[0].prompt == "The IBM stock price."


def test_without_backup_configured_the_flow_is_lost(tmp_path):
    """The behaviour we shipped with, pinned so nobody mistakes it for working."""
    pod_a = str(tmp_path / "podA" / "events.db")
    os.makedirs(os.path.dirname(pod_a), exist_ok=True)
    SubscriptionStore(pod_a).upsert(_cron())

    pod_b = str(tmp_path / "podB" / "events.db")
    assert db_persist.restore(pod_b, "") is False       # no EVENTS_DB_BACKUP → nothing to restore
    os.makedirs(os.path.dirname(pod_b), exist_ok=True)
    assert SubscriptionStore(pod_b).list(status="active") == []


# ── safety envelope ───────────────────────────────────────────────────────────────────────────
def test_restore_never_clobbers_a_live_local_db(tmp_path):
    """A restore over a populated local DB would CAUSE the loss it prevents. Newer local wins."""
    backup = str(tmp_path / "store" / "events.db")
    old = str(tmp_path / "old" / "events.db")
    os.makedirs(os.path.dirname(old), exist_ok=True)
    SubscriptionStore(old).upsert(_cron("cuga-old"))
    db_persist.snapshot(old, backup)

    live = str(tmp_path / "live" / "events.db")
    os.makedirs(os.path.dirname(live), exist_ok=True)
    SubscriptionStore(live).upsert(_cron("cuga-live"))

    assert db_persist.restore(live, backup) is False
    ids = {s.id for s in SubscriptionStore(live).list(status="active")}
    assert ids == {"cuga-live"}


def test_refuses_to_snapshot_onto_itself(tmp_path):
    """EVENTS_DB_BACKUP == EVENTS_DB would run SQLite on object storage — the corruption case."""
    same = str(tmp_path / "events.db")
    SubscriptionStore(same).upsert(_cron())
    assert db_persist.snapshot(same, same) is False
    assert db_persist.restore(same, same) is False


def test_snapshot_is_atomic_and_consistent_under_writes(tmp_path):
    """Snapshot mid-write must produce a readable DB, and never truncate an existing good one."""
    backup = str(tmp_path / "store" / "events.db")
    local = str(tmp_path / "pod" / "events.db")
    os.makedirs(os.path.dirname(local), exist_ok=True)
    store = SubscriptionStore(local)
    for i in range(50):
        store.upsert(_cron(f"cuga-{i:03d}"))
        if i % 10 == 0:
            assert db_persist.snapshot(local, backup) is True
            # every intermediate snapshot is a valid, queryable database
            db = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
            assert db.execute("SELECT COUNT(*) FROM subscription").fetchone()[0] >= 1
            db.close()
    db_persist.snapshot(local, backup)
    assert len(SubscriptionStore(backup).list(status="active")) == 50


def test_corrupt_snapshot_does_not_block_boot(tmp_path):
    """A bad backup must degrade to 'start fresh', never crash the service on startup."""
    backup = str(tmp_path / "store" / "events.db")
    os.makedirs(os.path.dirname(backup), exist_ok=True)
    with open(backup, "wb") as fh:
        fh.write(b"this is not a sqlite database, not even close")
    local = str(tmp_path / "pod" / "events.db")
    assert db_persist.restore(local, backup) is False   # no exception


def test_tenant_scope_survives_the_round_trip(tmp_path):
    """Restoring must not collapse tenants — that would resurrect flows into the wrong scope."""
    backup = str(tmp_path / "store" / "events.db")
    local = str(tmp_path / "pod" / "events.db")
    os.makedirs(os.path.dirname(local), exist_ok=True)
    store = SubscriptionStore(local)
    store.upsert(_cron("cuga-t1", tenant="acme/prod/alice"))
    store.upsert(_cron("cuga-t2", tenant="globex/prod/bob"))
    db_persist.snapshot(local, backup)

    pod_b = str(tmp_path / "podB" / "events.db")
    db_persist.restore(pod_b, backup)
    got = {s.id: s.tenant for s in SubscriptionStore(pod_b).list(status="active")}
    assert got == {"cuga-t1": "acme/prod/alice", "cuga-t2": "globex/prod/bob"}


# ── the loop ──────────────────────────────────────────────────────────────────────────────────
def test_snapshot_loop_persists_a_change(tmp_path):
    backup = str(tmp_path / "store" / "events.db")
    local = str(tmp_path / "pod" / "events.db")
    os.makedirs(os.path.dirname(local), exist_ok=True)
    store = SubscriptionStore(local)

    async def drive():
        task = asyncio.create_task(db_persist.run_snapshot_loop(local, backup, interval=0.05))
        await asyncio.sleep(0.15)
        store.upsert(_cron("cuga-late"))            # armed AFTER the loop started
        await asyncio.sleep(0.4)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(drive())
    assert "cuga-late" in {s.id for s in SubscriptionStore(backup).list(status="active")}


def test_loop_is_a_noop_when_persistence_is_off(tmp_path):
    """No EVENTS_DB_BACKUP → the loop returns immediately rather than spinning."""
    local = str(tmp_path / "pod" / "events.db")
    os.makedirs(os.path.dirname(local), exist_ok=True)
    SubscriptionStore(local)
    asyncio.run(asyncio.wait_for(db_persist.run_snapshot_loop(local, "", interval=0.01), timeout=2))


def test_status_reports_durability_honestly(tmp_path, monkeypatch):
    local = str(tmp_path / "pod" / "events.db")
    monkeypatch.delenv("EVENTS_DB_BACKUP", raising=False)
    assert db_persist.status(local)["durable"] is False

    backup = str(tmp_path / "store" / "events.db")
    os.makedirs(os.path.dirname(local), exist_ok=True)
    SubscriptionStore(local).upsert(_cron())
    db_persist.snapshot(local, backup)
    monkeypatch.setenv("EVENTS_DB_BACKUP", backup)
    st = db_persist.status(local)
    assert st["durable"] is True and st["subscriptions_in_snapshot"] == 1


@pytest.mark.parametrize("local", [":memory:", ""])
def test_memory_db_is_never_snapshotted(local, tmp_path):
    assert db_persist.snapshot(local, str(tmp_path / "b.db")) is False
    assert db_persist.restore(local, str(tmp_path / "b.db")) is False


# ── Postgres: the snapshot machinery must stand down, and say so honestly ─────────────────────
PG_DSN = "postgresql://cuga:pw@db.example.com:5432/cuga_events"


def test_postgres_dsn_is_never_treated_as_a_file(tmp_path, monkeypatch):
    """A DSN handed to sqlite3.connect creates a FILE named 'postgresql://…' and reports success —
    durability that protects nothing. Both directions must refuse."""
    monkeypatch.chdir(tmp_path)
    assert db_persist.snapshot(PG_DSN, str(tmp_path / "b.db")) is False
    assert db_persist.restore(PG_DSN, str(tmp_path / "b.db")) is False
    assert not any(p.name.startswith("postgres") for p in tmp_path.iterdir())


def test_postgres_reports_durable_without_a_backup(monkeypatch):
    """Postgres IS durable; reporting false because EVENTS_DB_BACKUP is unset is backwards."""
    monkeypatch.delenv("EVENTS_DB_BACKUP", raising=False)
    st = db_persist.status(PG_DSN)
    assert st["durable"] is True
    assert st["backend"] == "postgres" and st["mechanism"] == "database"


def test_sqlite_without_backup_reports_not_durable(tmp_path, monkeypatch):
    monkeypatch.delenv("EVENTS_DB_BACKUP", raising=False)
    st = db_persist.status(str(tmp_path / "events.db"))
    assert st["durable"] is False and st["backend"] == "sqlite"
    assert "postgresql://" in st["reason"]          # points at the real fix


def test_snapshot_loop_is_a_noop_on_postgres(monkeypatch):
    monkeypatch.setenv("EVENTS_DB_BACKUP", "/mnt/state/events.db")
    asyncio.run(asyncio.wait_for(db_persist.run_snapshot_loop(PG_DSN, interval=0.01), timeout=2))
