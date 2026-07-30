"""The **NOW-run log** — a thin persistent record of immediate (NOW) agent answers.

Standing flows (cron/poll/push) already have a run history in Activepieces; the Studio's Runs tab
reads those from AP. But a NOW answer — a human chatting in the Studio Concierge, or DMing a bot on
a channel — never becomes an AP flow, so it has no AP run. This store captures those, so the unified
Runs log can show *every* execution: NOW answers here + AP flow-runs from the engine.

stdlib ``sqlite3`` + JSON columns, same conventions as ``subscriptions.py`` (migrate-on-open,
single shared connection). Dependency-free → unit-testable in isolation.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid

# keep the table from growing without bound — trim to the newest N rows on write
_CAP = 2000


class NowRunStore:
    """SQLite-backed log of NOW invocations. Pass ``":memory:"`` for tests / no-DB deployments."""

    def __init__(self, db_path: str = ":memory:"):
        # check_same_thread=False: shared by an async server whose handlers may run on a threadpool.
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self) -> None:
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS now_run (
                 id TEXT PRIMARY KEY,
                 ts REAL NOT NULL DEFAULT 0,
                 scope TEXT NOT NULL DEFAULT '',
                 agent TEXT NOT NULL DEFAULT '',
                 channel TEXT NOT NULL DEFAULT '',
                 prompt TEXT NOT NULL DEFAULT '',
                 answer TEXT NOT NULL DEFAULT '',
                 status TEXT NOT NULL DEFAULT 'ok',
                 ms INTEGER NOT NULL DEFAULT 0,
                 mcp TEXT NOT NULL DEFAULT '[]',
                 tools TEXT NOT NULL DEFAULT '[]',
                 trace_id TEXT NOT NULL DEFAULT ''
               )""")
        # migrate-on-open: mode/backend/subscription_id let a run carry its TRIGGER TYPE (cron/poll/
        # push/now · native/ap) so the Runs API can filter and the UI can group — not just "NOW".
        cols = {r[1] for r in self._db.execute("PRAGMA table_info(now_run)").fetchall()}
        for _c, _d in (("mode", "TEXT NOT NULL DEFAULT 'NOW'"),
                       ("backend", "TEXT NOT NULL DEFAULT 'direct'"),
                       ("subscription_id", "TEXT NOT NULL DEFAULT ''"),
                       ("event_kind", "TEXT NOT NULL DEFAULT ''")):
            if _c not in cols:
                self._db.execute(f"ALTER TABLE now_run ADD COLUMN {_c} {_d}")
        self._db.execute("CREATE INDEX IF NOT EXISTS ix_now_run_ts ON now_run(ts)")
        self._db.commit()

    def add(self, *, scope: str, agent: str, channel: str, prompt: str, answer: str,
            status: str = "ok", ms: int = 0, mcp=None, tools=None, trace_id: str = "",
            mode: str = "NOW", backend: str = "direct", subscription_id: str = "",
            event_kind: str = "") -> str:
        """Append one NOW run. Answers can be long — cap what we persist so the log stays light."""
        rid = uuid.uuid4().hex
        self._db.execute(
            """INSERT INTO now_run
                 (id, ts, scope, agent, channel, prompt, answer, status, ms, mcp, tools, trace_id,
                  mode, backend, subscription_id, event_kind)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (rid, time.time(), scope or "", agent or "", channel or "",
             (prompt or "")[:2000], (answer or "")[:8000], status or "ok", int(ms or 0),
             json.dumps(mcp or []), json.dumps(tools or []), trace_id or "",
             mode or "NOW", backend or "direct", subscription_id or "", event_kind or ""))
        # trim: keep the newest _CAP rows overall (cheap, runs only when we exceed the cap)
        self._db.execute(
            "DELETE FROM now_run WHERE id NOT IN (SELECT id FROM now_run ORDER BY ts DESC LIMIT ?)",
            (_CAP,))
        self._db.commit()
        return rid

    def list(self, *, scope: str | None = None, limit: int = 100,
             mode: str | None = None, backend: str | None = None,
             agent: str | None = None, status: str | None = None,
             subscription_id: str | None = None) -> list[dict]:
        where, params = [], []
        if scope is not None:
            where.append("scope=?"); params.append(scope)
        if mode:                                     # CRON | POLL | PUSH | NOW (case-insensitive)
            where.append("UPPER(mode)=?"); params.append(mode.upper())
        if backend:                                  # native | ap | direct
            where.append("backend=?"); params.append(backend.lower())
        if agent:
            where.append("agent=?"); params.append(agent)
        if status:
            where.append("status=?"); params.append(status)
        if subscription_id:
            where.append("subscription_id=?"); params.append(subscription_id)
        sql = "SELECT * FROM now_run"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        return [self._row(r) for r in self._db.execute(sql, params).fetchall()]

    def get(self, run_id: str) -> dict | None:
        r = self._db.execute("SELECT * FROM now_run WHERE id=?", (run_id,)).fetchone()
        return self._row(r) if r else None

    @staticmethod
    def _row(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["mcp"] = json.loads(d.get("mcp") or "[]")
        d["tools"] = json.loads(d.get("tools") or "[]")
        return d
