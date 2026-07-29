"""The thin **subscription index** — the only genuinely-new persistent state.

Agents live in CUGA's ``agent_configs``; connections/triggers/runs live in Activepieces.
All we keep is a small index of the AP flows the concierge built, so the UI and the
concierge's reuse logic can list/inspect them without hitting AP every time.

stdlib ``sqlite3`` + JSON columns, mirroring CUGA's storage conventions
(upsert-by-PK, migrate-on-open). Dependency-free → unit-testable in isolation.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field, asdict

MODES = ("NOW", "CRON", "PUSH", "POLL")


class DuplicateSubscription(Exception):
    """A concurrent arm with the same dedup identity won the race — treat as REUSE, not an error."""
    def __init__(self, dedup_key: str):
        super().__init__(f"a subscription with dedup_key {dedup_key!r} already exists")
        self.dedup_key = dedup_key


@dataclass
class Subscription:
    id: str
    mode: str                      # NOW | CRON | PUSH | POLL
    target_agent: str
    tenant: str = "default"        # Principal.scope — the isolation key
    backend: str = "react"         # cuga | react
    source_type: str = "time"      # channel | integration | time
    source_connector: str = "cron"
    ap_flow_id: str | None = None
    deliver_to: list = field(default_factory=list)   # sink connectors
    thread_id: str = ""
    prompt: str = ""
    status: str = "active"         # active | draft | paused
    created_at: float = 0.0
    # dedup identity: (agent, source, cadence, sink, owner-scope). Owner-scope is the TENANT for
    # all-shared-connector flows and the full user scope when any connector is per-user — so the
    # flow grain follows the credentials (a matching key → reuse instead of a duplicate flow).
    dedup_key: str = ""
    flow_name: str = ""            # the AP flow's readable display name (e.g. push-gmail-mailbot)
    # trigger grain (added with the trigger registry): WHICH event of the integration this watches
    # ("new_pr", "new_reaction", …) and its per-watch config (slots: repo/label/channel/emoji/
    # pattern/folder). Empty for legacy rows.
    event: str = ""
    config: dict = field(default_factory=dict)


class SubscriptionStore:
    """SQLite-backed index. Pass ``":memory:"`` for tests."""

    def __init__(self, db_path: str = ":memory:"):
        # check_same_thread=False: the store is shared by an async web server whose handlers may
        # run on a threadpool (and TestClient does). Ops here are short + low-concurrency (a thin
        # index), so a single shared connection is fine.
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self) -> None:
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS subscription (
                 id TEXT PRIMARY KEY,
                 mode TEXT NOT NULL,
                 target_agent TEXT NOT NULL,
                 tenant TEXT NOT NULL DEFAULT 'default',
                 backend TEXT NOT NULL DEFAULT 'react',
                 source_type TEXT NOT NULL DEFAULT 'time',
                 source_connector TEXT NOT NULL DEFAULT 'cron',
                 ap_flow_id TEXT,
                 deliver_to TEXT NOT NULL DEFAULT '[]',
                 thread_id TEXT NOT NULL DEFAULT '',
                 prompt TEXT NOT NULL DEFAULT '',
                 status TEXT NOT NULL DEFAULT 'active',
                 created_at REAL NOT NULL DEFAULT 0,
                 dedup_key TEXT NOT NULL DEFAULT '',
                 flow_name TEXT NOT NULL DEFAULT ''
               )""")
        cols = {r[1] for r in self._db.execute("PRAGMA table_info(subscription)").fetchall()}
        if "dedup_key" not in cols:
            self._db.execute("ALTER TABLE subscription ADD COLUMN dedup_key TEXT NOT NULL DEFAULT ''")
        if "flow_name" not in cols:
            self._db.execute("ALTER TABLE subscription ADD COLUMN flow_name TEXT NOT NULL DEFAULT ''")
        if "event" not in cols:
            self._db.execute("ALTER TABLE subscription ADD COLUMN event TEXT NOT NULL DEFAULT ''")
        if "config" not in cols:
            self._db.execute("ALTER TABLE subscription ADD COLUMN config TEXT NOT NULL DEFAULT '{}'")
        # Dedup used to be check-then-write with no constraint — two concurrent arms with the same
        # identity both missed the check and created duplicate AP flows. The partial UNIQUE index
        # makes the database the referee; upsert() surfaces the loser as a DuplicateSubscription.
        # try/except: a pre-existing DB may already hold duplicates — warn, don't brick the boot.
        try:
            self._db.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS uq_subscription_dedup
                     ON subscription(dedup_key) WHERE dedup_key != '' AND status != 'deleted'""")
        except sqlite3.OperationalError as e:                    # legacy dupes present
            import logging
            logging.getLogger("events.subscriptions").warning(
                "could not create the dedup unique index (%s) — legacy duplicate rows exist; "
                "dedup stays advisory for this DB", e)
        self._db.commit()

    # ---- writes ----------------------------------------------------------
    def upsert(self, sub: Subscription) -> Subscription:
        if not sub.created_at:
            sub.created_at = time.time()
        try:
            self._db.execute(
                """INSERT INTO subscription
                     (id,mode,target_agent,tenant,backend,source_type,source_connector,ap_flow_id,
                      deliver_to,thread_id,prompt,status,created_at,dedup_key,flow_name,event,config)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     mode=excluded.mode, target_agent=excluded.target_agent, tenant=excluded.tenant,
                     backend=excluded.backend, source_type=excluded.source_type,
                     source_connector=excluded.source_connector, ap_flow_id=excluded.ap_flow_id,
                     deliver_to=excluded.deliver_to, thread_id=excluded.thread_id,
                     prompt=excluded.prompt, status=excluded.status, dedup_key=excluded.dedup_key,
                     flow_name=excluded.flow_name, event=excluded.event, config=excluded.config""",
                (sub.id, sub.mode, sub.target_agent, sub.tenant, sub.backend, sub.source_type,
                 sub.source_connector, sub.ap_flow_id, json.dumps(sub.deliver_to),
                 sub.thread_id, sub.prompt, sub.status, sub.created_at, sub.dedup_key,
                 sub.flow_name, sub.event, json.dumps(sub.config or {})))
        except sqlite3.IntegrityError as e:
            if "uq_subscription_dedup" in str(e) or "dedup" in str(e):
                raise DuplicateSubscription(sub.dedup_key) from e
            raise
        self._db.commit()
        return sub

    def find_by_dedup_key(self, dedup_key: str) -> Subscription | None:
        """Reuse-or-create: an active flow with this identity already exists?"""
        if not dedup_key:
            return None
        r = self._db.execute(
            "SELECT * FROM subscription WHERE dedup_key=? AND status!='deleted' LIMIT 1",
            (dedup_key,)).fetchone()
        return self._row(r) if r else None

    def set_status(self, sub_id: str, status: str) -> None:
        self._db.execute("UPDATE subscription SET status=? WHERE id=?", (status, sub_id))
        self._db.commit()

    def delete(self, sub_id: str) -> None:
        self._db.execute("DELETE FROM subscription WHERE id=?", (sub_id,))
        self._db.commit()

    # ---- reads -----------------------------------------------------------
    def _row(self, r: sqlite3.Row) -> Subscription:
        d = dict(r)
        d["deliver_to"] = json.loads(d.get("deliver_to") or "[]")
        d["config"] = json.loads(d.get("config") or "{}")
        return Subscription(**d)

    def get(self, sub_id: str) -> Subscription | None:
        r = self._db.execute("SELECT * FROM subscription WHERE id=?", (sub_id,)).fetchone()
        return self._row(r) if r else None

    def list(self, *, status: str | None = None, scope: str | None = None) -> list[Subscription]:
        where, params = [], []
        if status:
            where.append("status=?")
            params.append(status)
        if scope is not None:                 # isolation filter
            where.append("tenant=?")
            params.append(scope)
        sql = "SELECT * FROM subscription"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at"
        return [self._row(r) for r in self._db.execute(sql, params).fetchall()]

    def by_agent(self, agent: str, *, scope: str | None = None) -> list[Subscription]:
        if scope is not None:
            rows = self._db.execute("SELECT * FROM subscription WHERE target_agent=? AND tenant=?",
                                    (agent, scope)).fetchall()
        else:
            rows = self._db.execute("SELECT * FROM subscription WHERE target_agent=?",
                                    (agent,)).fetchall()
        return [self._row(r) for r in rows]

    def as_dicts(self, *, scope: str | None = None) -> list[dict]:
        return [asdict(s) for s in self.list(scope=scope)]
