"""The **web channel's mailbox** — outbound delivery for a browser.

Slack, Discord and Telegram each give CUGA a socket it can push into at any moment, so a flow armed
there fires straight back into the conversation that armed it (``delivery.send_direct``). A browser
gives CUGA nothing: a cron armed at 09:00 fires at 09:05, when no request is in flight and possibly
no tab is open. ``send_direct`` had no ``web`` branch at all, so those answers were written to the
runs log and nowhere else — the flow fired, the dashboard knew, and the chat that armed it never
heard a word. That is the "it ran but I don't see it" gap.

This module closes it by giving ``web`` the one transport a browser can actually use: a **durable
table the UI drains by cursor**. Delivery ``put()``s on fire; the chat surface polls
``list(thread_id, since=…)`` and appends what it finds. Because it lives in the same store as the
subscription index (Postgres in the cloud, SQLite locally), a message survives an instance
replacement — it is still waiting when the tab comes back.

The key is the **thread_id**, which is what makes this a delivery address rather than a log: a fire
lands in the conversation that armed it, and only there. Channel-armed flows never reach this module
(their origin resolves to a real channel), so there are no duplicates.
"""

from __future__ import annotations

import time
import uuid

try:
    from . import db as _db
except ImportError:  # flat load (tests put the events dir on sys.path)
    import db as _db

# keep the mailbox from growing without bound — trim to the newest N rows on write
_CAP = 2000


class WebInbox:
    """Durable per-thread mailbox for web deliveries. ``":memory:"`` for tests / no-DB deployments."""

    def __init__(self, db_path: str = ":memory:"):
        self._db = _db.connect(db_path)
        self._migrate()

    def _migrate(self) -> None:
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS web_inbox (
                 id TEXT PRIMARY KEY,
                 ts REAL NOT NULL DEFAULT 0,
                 scope TEXT NOT NULL DEFAULT '',
                 thread_id TEXT NOT NULL DEFAULT '',
                 text TEXT NOT NULL DEFAULT '',
                 agent TEXT NOT NULL DEFAULT '',
                 subscription_id TEXT NOT NULL DEFAULT '',
                 flow_name TEXT NOT NULL DEFAULT '',
                 event_kind TEXT NOT NULL DEFAULT ''
               )"""
        )
        # the read is always "this thread, newer than my cursor" — index it that way
        self._db.execute("CREATE INDEX IF NOT EXISTS ix_web_inbox_thread ON web_inbox(thread_id, ts)")
        self._db.commit()

    def put(
        self,
        *,
        scope: str,
        thread_id: str,
        text: str,
        agent: str = "",
        subscription_id: str = "",
        flow_name: str = "",
        event_kind: str = "",
    ) -> str:
        """Deliver one message to a web thread. Returns the message id."""
        mid = uuid.uuid4().hex
        self._db.execute(
            """INSERT INTO web_inbox
                 (id, ts, scope, thread_id, text, agent, subscription_id, flow_name, event_kind)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                mid,
                time.time(),
                scope or "",
                thread_id or "",
                (text or "")[:16000],
                agent or "",
                subscription_id or "",
                flow_name or "",
                event_kind or "",
            ),
        )
        self._db.execute(
            "DELETE FROM web_inbox WHERE id NOT IN (SELECT id FROM web_inbox ORDER BY ts DESC LIMIT ?)",
            (_CAP,),
        )
        self._db.commit()
        return mid

    def list(
        self, *, thread_id: str, since: float = 0.0, scope: str | None = None, limit: int = 50
    ) -> list[dict]:
        """Messages for one thread newer than ``since`` (epoch seconds), **oldest first** — the
        order a chat log appends them in. ``since`` is exclusive, so a client can pass back the
        ``ts`` of the last message it rendered and never see it twice."""
        where = ["thread_id=?", "ts>?"]
        params: list = [thread_id or "", float(since or 0.0)]
        if scope is not None:
            where.append("scope=?")
            params.append(scope)
        params.append(max(1, min(200, limit)))
        rows = self._db.execute(
            "SELECT * FROM web_inbox WHERE " + " AND ".join(where) + " ORDER BY ts ASC LIMIT ?", params
        ).fetchall()
        return [dict(r) for r in rows]


# ── module-level singleton ────────────────────────────────────────────────────────────────────────
# ``delivery.send_direct`` is a free function reached from the /invoke path, the native scheduler and
# the direct watchers — none of which carry the app's closure. One process-wide store keeps the
# delivery seam a plain function call, exactly like slack_direct's module-level client.
_STORE: WebInbox | None = None


def init(db_path: str = ":memory:") -> WebInbox:
    """Create (or replace) the process-wide mailbox. Called once at app build."""
    global _STORE
    _STORE = WebInbox(db_path)
    return _STORE


def store() -> WebInbox | None:
    return _STORE


def put(**kw) -> str:
    """Deliver via the process-wide mailbox. Returns "" when no mailbox is mounted."""
    return _STORE.put(**kw) if _STORE is not None else ""


def list_since(**kw) -> list[dict]:
    return _STORE.list(**kw) if _STORE is not None else []
