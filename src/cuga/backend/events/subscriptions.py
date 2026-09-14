"""The thin **subscription index** — the only genuinely-new persistent state.

Agents live in CUGA's ``agent_configs``; connections/triggers/runs live in Activepieces.
All we keep is a small index of the AP flows the concierge built, so the UI and the
concierge's reuse logic can list/inspect them without hitting AP every time.

SQLite **or** Postgres via ``db.connect`` (see db.py) + JSON columns, mirroring CUGA's conventions
(upsert-by-PK, migrate-on-open). Dependency-free → unit-testable in isolation.
"""

from __future__ import annotations

import json

try:
    from . import db as _db
except ImportError:  # flat load (tests put the events dir on sys.path)
    import db as _db
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
    mode: str  # NOW | CRON | PUSH | POLL
    target_agent: str
    tenant: str = "default"  # Principal.scope — the isolation key
    backend: str = "react"  # cuga | react
    source_type: str = "time"  # channel | integration | time
    source_connector: str = "cron"
    ap_flow_id: str | None = None
    deliver_to: list = field(default_factory=list)  # sink connectors
    thread_id: str = ""
    prompt: str = ""
    status: str = "active"  # active | draft | paused
    created_at: float = 0.0
    # dedup identity: (agent, source, cadence, sink, owner-scope). Owner-scope is the TENANT for
    # all-shared-connector flows and the full user scope when any connector is per-user — so the
    # flow grain follows the credentials (a matching key → reuse instead of a duplicate flow).
    dedup_key: str = ""
    flow_name: str = ""  # the AP flow's readable display name (e.g. push-gmail-mailbot)
    # trigger grain (added with the trigger registry): WHICH event of the integration this watches
    # ("new_pr", "new_reaction", …) and its per-watch config (slots: repo/label/channel/emoji/
    # pattern/folder). Empty for legacy rows.
    event: str = ""
    config: dict = field(default_factory=dict)
    # ── NATIVE scheduler fields (backend='native') — the AP-free cron/poll/tool-watch path. A native
    # subscription carries its own schedule; native_scheduler fires it (no AP flow). All epoch seconds.
    # First-class columns (not buried in config) so the Studio/visualization can sort & show them.
    interval_seconds: int = 0  # cadence for interval schedules; 0 → use cron_expr
    cron_expr: str = ""  # cron expression (named cron_expr to avoid clashing with source_connector='cron')
    next_fire: float = 0.0  # scheduler: next epoch to fire; 0 → not natively scheduled
    last_fire: float = 0.0  # scheduler: last epoch fired (for the timeline view)
    fire_count: int = 0  # how many times it has fired (for the timeline view)
    expires_at: float = 0.0  # bounded-run end epoch; 0 → never


class SubscriptionStore:
    """The subscription index. ``db_path`` is a SQLite path or a ``postgresql://`` URL
    (see db.py); ``":memory:"`` for tests."""

    def __init__(self, db_path: str = ":memory:"):
        # check_same_thread=False: the store is shared by an async web server whose handlers may
        # run on a threadpool (and TestClient does). Ops here are short + low-concurrency (a thin
        # index), so a single shared connection is fine.
        self._db = _db.connect(db_path)
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
               )"""
        )
        cols = self._db.columns("subscription")
        if "dedup_key" not in cols:
            self._db.execute("ALTER TABLE subscription ADD COLUMN dedup_key TEXT NOT NULL DEFAULT ''")
        if "flow_name" not in cols:
            self._db.execute("ALTER TABLE subscription ADD COLUMN flow_name TEXT NOT NULL DEFAULT ''")
        if "event" not in cols:
            self._db.execute("ALTER TABLE subscription ADD COLUMN event TEXT NOT NULL DEFAULT ''")
        if "config" not in cols:
            self._db.execute("ALTER TABLE subscription ADD COLUMN config TEXT NOT NULL DEFAULT '{}'")
        # native scheduler columns (migrate-on-open; _row builds Subscription(**cols) so every column
        # must be a dataclass field). Added together so a fresh or legacy DB both end up complete.
        for _col, _decl in (
            ("interval_seconds", "INTEGER NOT NULL DEFAULT 0"),
            ("cron_expr", "TEXT NOT NULL DEFAULT ''"),
            ("next_fire", "REAL NOT NULL DEFAULT 0"),
            ("last_fire", "REAL NOT NULL DEFAULT 0"),
            ("fire_count", "INTEGER NOT NULL DEFAULT 0"),
            ("expires_at", "REAL NOT NULL DEFAULT 0"),
        ):
            if _col not in cols:
                self._db.execute(f"ALTER TABLE subscription ADD COLUMN {_col} {_decl}")
        # index the scheduler's hot query (due native subscriptions)
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS ix_subscription_next_fire "
            "ON subscription(next_fire) WHERE next_fire > 0"
        )
        # watch_state — per-subscription DELTA state for stateful native polls (Phase 3). Created now
        # so the schema is complete for visualization; the deterministic Option-1 poller populates it.
        #   kind         'identity' (seen-set of ids) | 'threshold' (scalar baseline)
        #   seen_keys    JSON array of already-fired identities (identity watches)
        #   baseline     last reference value (threshold watches)
        #   reset_policy 'ratchet' (from last alert) | 'absolute' | 'per_tick'
        #   value_path   JSONPath to the comparable value in the tool output
        #   threshold    fractional move that fires (0.05 = 5%)
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS watch_state (
                 subscription_id TEXT PRIMARY KEY,
                 kind TEXT NOT NULL DEFAULT 'identity',
                 seen_keys TEXT NOT NULL DEFAULT '[]',
                 baseline REAL,
                 reset_policy TEXT NOT NULL DEFAULT 'ratchet',
                 value_path TEXT NOT NULL DEFAULT '',
                 threshold REAL NOT NULL DEFAULT 0,
                 updated_at REAL NOT NULL DEFAULT 0
               )"""
        )
        # HITL arming: the half-finished arming dialogue for a thread (see arming.py). Lives here,
        # in the SAME db/connection as subscriptions, so it survives a restart (it used to be a
        # module-level dict — a redeploy silently dropped every in-flight arm) and so ":memory:"
        # tests still see one shared database.
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS pending_arm (
                 thread TEXT PRIMARY KEY,
                 state TEXT NOT NULL,
                 payload TEXT NOT NULL DEFAULT '{}',
                 expires_at REAL NOT NULL DEFAULT 0
               )"""
        )
        # Dedup used to be check-then-write with no constraint — two concurrent arms with the same
        # identity both missed the check and created duplicate AP flows. The partial UNIQUE index
        # makes the database the referee; upsert() surfaces the loser as a DuplicateSubscription.
        # try/except: a pre-existing DB may already hold duplicates — warn, don't brick the boot.
        try:
            self._db.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS uq_subscription_dedup
                     ON subscription(dedup_key) WHERE dedup_key != '' AND status != 'deleted'"""
            )
        except _db.OperationalError as e:  # legacy dupes present
            import logging

            logging.getLogger("events.subscriptions").warning(
                "could not create the dedup unique index (%s) — legacy duplicate rows exist; "
                "dedup stays advisory for this DB",
                e,
            )
        self._db.commit()

    # ---- writes ----------------------------------------------------------
    def upsert(self, sub: Subscription) -> Subscription:
        if not sub.created_at:
            sub.created_at = time.time()
        try:
            self._db.execute(
                """INSERT INTO subscription
                     (id,mode,target_agent,tenant,backend,source_type,source_connector,ap_flow_id,
                      deliver_to,thread_id,prompt,status,created_at,dedup_key,flow_name,event,config,
                      interval_seconds,cron_expr,next_fire,last_fire,fire_count,expires_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     mode=excluded.mode, target_agent=excluded.target_agent, tenant=excluded.tenant,
                     backend=excluded.backend, source_type=excluded.source_type,
                     source_connector=excluded.source_connector, ap_flow_id=excluded.ap_flow_id,
                     deliver_to=excluded.deliver_to, thread_id=excluded.thread_id,
                     prompt=excluded.prompt, status=excluded.status, dedup_key=excluded.dedup_key,
                     flow_name=excluded.flow_name, event=excluded.event, config=excluded.config,
                     interval_seconds=excluded.interval_seconds, cron_expr=excluded.cron_expr,
                     next_fire=excluded.next_fire, last_fire=excluded.last_fire,
                     fire_count=excluded.fire_count, expires_at=excluded.expires_at""",
                (
                    sub.id,
                    sub.mode,
                    sub.target_agent,
                    sub.tenant,
                    sub.backend,
                    sub.source_type,
                    sub.source_connector,
                    sub.ap_flow_id,
                    json.dumps(sub.deliver_to),
                    sub.thread_id,
                    sub.prompt,
                    sub.status,
                    sub.created_at,
                    sub.dedup_key,
                    sub.flow_name,
                    sub.event,
                    json.dumps(sub.config or {}),
                    sub.interval_seconds,
                    sub.cron_expr,
                    sub.next_fire,
                    sub.last_fire,
                    sub.fire_count,
                    sub.expires_at,
                ),
            )
        except _db.IntegrityError as e:
            if "uq_subscription_dedup" in str(e) or "dedup" in str(e):
                raise DuplicateSubscription(sub.dedup_key) from e
            raise
        self._db.commit()
        return sub

    def find_by_dedup_key(self, dedup_key: str, *, scope: str | None = None) -> Subscription | None:
        """Reuse-or-create: an active flow with this identity already exists **for this tenant**?

        ISOLATION. This used to search every tenant, so an arm could be answered "REUSING existing
        flow (subscription X)" where X belonged to somebody else — invisible in the caller's Flows
        list, undeletable by them, and delivering to the other tenant's channel. Observed live: a
        flow owned by `default/default/local` was handed to `default/default/admin`, which saw zero
        subscriptions and was told one had been reused.

        `scope=None` keeps the old cross-tenant behaviour for callers that genuinely have no
        principal; every concierge path passes one.
        """
        if not dedup_key:
            return None
        sql = "SELECT * FROM subscription WHERE dedup_key=? AND status!='deleted'"
        params: list = [dedup_key]
        if scope is not None:
            sql += " AND tenant=?"
            params.append(scope)
        r = self._db.execute(sql + " LIMIT 1", params).fetchone()
        return self._row(r) if r else None

    def set_status(self, sub_id: str, status: str) -> None:
        self._db.execute("UPDATE subscription SET status=? WHERE id=?", (status, sub_id))
        self._db.commit()

    def delete(self, sub_id: str) -> None:
        self._db.execute("DELETE FROM subscription WHERE id=?", (sub_id,))
        self._db.execute("DELETE FROM watch_state WHERE subscription_id=?", (sub_id,))
        self._db.commit()

    # ---- reads -----------------------------------------------------------
    def _row(self, r: _db.Row) -> Subscription:
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
        if scope is not None:  # isolation filter
            where.append("tenant=?")
            params.append(scope)
        sql = "SELECT * FROM subscription"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at"
        return [self._row(r) for r in self._db.execute(sql, params).fetchall()]

    def by_agent(self, agent: str, *, scope: str | None = None) -> list[Subscription]:
        if scope is not None:
            rows = self._db.execute(
                "SELECT * FROM subscription WHERE target_agent=? AND tenant=?", (agent, scope)
            ).fetchall()
        else:
            rows = self._db.execute("SELECT * FROM subscription WHERE target_agent=?", (agent,)).fetchall()
        return [self._row(r) for r in rows]

    def as_dicts(self, *, scope: str | None = None) -> list[dict]:
        return [asdict(s) for s in self.list(scope=scope)]

    # ---- native scheduler ------------------------------------------------
    def due(self, now: float) -> list[Subscription]:
        """Active NATIVE subscriptions whose next_fire has arrived — the scheduler's hot query.
        Only backend='native' rows participate; AP-backed flows are fired by Activepieces, not us."""
        rows = self._db.execute(
            "SELECT * FROM subscription WHERE backend='native' AND status='active' "
            "AND next_fire > 0 AND next_fire <= ? ORDER BY next_fire",
            (now,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def mark_fired(self, sub_id: str, *, last_fire: float, next_fire: float) -> None:
        """Record a fire and schedule the next one (next_fire=0 disables further firing)."""
        self._db.execute(
            "UPDATE subscription SET last_fire=?, next_fire=?, fire_count=fire_count+1 WHERE id=?",
            (last_fire, next_fire, sub_id),
        )
        self._db.commit()

    # ---- watch_state (stateful POLL delta, Phase 3) ----------------------
    def get_watch_state(self, sub_id: str) -> dict | None:
        """The delta state for a stateful poll, or None (→ Tier-0 'always report'). ``seen_keys`` is
        decoded to a list; ``baseline`` stays float|None."""
        r = self._db.execute("SELECT * FROM watch_state WHERE subscription_id=?", (sub_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["seen_keys"] = json.loads(d.get("seen_keys") or "[]")
        return d

    # ── HITL arming: the in-flight arming dialogue for one thread ──────────────────────────────
    def get_pending_arm(self, thread: str) -> dict | None:
        """The parked arming state for this thread, or None when there is none / it expired.

        Unlike the old in-memory version this does NOT pop: the caller decides the transition
        (answer a question, confirm, cancel), because a read that consumed the state made an
        innocent chat message destroy an in-flight arm."""
        r = self._db.execute("SELECT * FROM pending_arm WHERE thread=?", (thread,)).fetchone()
        if not r:
            return None
        d = dict(r)
        if float(d.get("expires_at") or 0) < time.time():
            self.clear_pending_arm(thread)
            return None
        try:
            d["payload"] = json.loads(d.get("payload") or "{}")
        except json.JSONDecodeError:
            d["payload"] = {}
        return d

    def set_pending_arm(self, thread: str, state: str, payload: dict, ttl_secs: float) -> None:
        self._db.execute(
            """INSERT INTO pending_arm (thread,state,payload,expires_at) VALUES (?,?,?,?)
               ON CONFLICT(thread) DO UPDATE SET
                 state=excluded.state, payload=excluded.payload, expires_at=excluded.expires_at""",
            (thread, state, json.dumps(payload or {}), time.time() + float(ttl_secs)),
        )
        self._db.commit()

    def clear_pending_arm(self, thread: str) -> None:
        self._db.execute("DELETE FROM pending_arm WHERE thread=?", (thread,))
        self._db.commit()

    def set_watch_state(self, ws: dict) -> None:
        """Upsert a watch_state row (arm-time seed and per-fire update both call this)."""
        self._db.execute(
            """INSERT INTO watch_state
                 (subscription_id,kind,seen_keys,baseline,reset_policy,value_path,threshold,updated_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(subscription_id) DO UPDATE SET
                 kind=excluded.kind, seen_keys=excluded.seen_keys, baseline=excluded.baseline,
                 reset_policy=excluded.reset_policy, value_path=excluded.value_path,
                 threshold=excluded.threshold, updated_at=excluded.updated_at""",
            (
                ws["subscription_id"],
                ws.get("kind") or "fuzzy",
                json.dumps(ws.get("seen_keys") or []),
                ws.get("baseline"),
                ws.get("reset_policy") or "ratchet",
                ws.get("value_path") or "",
                float(ws.get("threshold") or 0),
                float(ws.get("updated_at") or 0),
            ),
        )
        self._db.commit()
