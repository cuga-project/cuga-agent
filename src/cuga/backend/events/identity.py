"""Identity map — resolve a channel-native id to a CUGA user (decision 0007).

A message from Telegram/Slack/Discord arrives with a **native id** (telegram user id, slack user
id, …) under a **shared bot**. This maps `(tenant, channel, native_id) → user_id` so isolation +
per-user connections become real on channels.

Linking is initiated from the **authenticated profile** (so the binding is trustworthy): the
profile issues a short-lived **link token**; the user sends it to the bot (Telegram deep-link
`?start=<token>`, Discord code); the inbound flow posts it to `/invoke`, which redeems it → binds.

SQLite or Postgres via ``db.connect`` → flat-loadable + offline-testable.
"""

from __future__ import annotations

import secrets

try:
    from . import db as _db
except ImportError:  # flat load (tests put the events dir on sys.path)
    import db as _db
import time

_TOKEN_TTL = 15 * 60  # link tokens are valid 15 minutes


class IdentityMap:
    def __init__(self, db_path: str = ":memory:"):
        self._db = _db.connect(db_path)
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS identity (
                 tenant TEXT NOT NULL, channel TEXT NOT NULL, native_id TEXT NOT NULL,
                 user_id TEXT NOT NULL, created_at REAL NOT NULL DEFAULT 0,
                 PRIMARY KEY (tenant, channel, native_id)
               )"""
        )
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS link_token (
                 token TEXT PRIMARY KEY, tenant TEXT NOT NULL, user_id TEXT NOT NULL,
                 channel TEXT NOT NULL, created_at REAL NOT NULL DEFAULT 0, used INTEGER NOT NULL DEFAULT 0
               )"""
        )
        self._db.commit()

    # ---- resolution -------------------------------------------------------
    def link(self, tenant: str, channel: str, native_id: str, user_id: str) -> None:
        self._db.execute(
            """INSERT INTO identity (tenant,channel,native_id,user_id,created_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(tenant,channel,native_id) DO UPDATE SET user_id=excluded.user_id""",
            (tenant, channel, str(native_id), user_id, time.time()),
        )
        self._db.commit()

    def resolve(self, tenant: str, channel: str, native_id: str) -> str | None:
        r = self._db.execute(
            "SELECT user_id FROM identity WHERE tenant=? AND channel=? AND native_id=?",
            (tenant, channel, str(native_id)),
        ).fetchone()
        return r["user_id"] if r else None

    def unlink(self, tenant: str, channel: str, native_id: str) -> None:
        self._db.execute(
            "DELETE FROM identity WHERE tenant=? AND channel=? AND native_id=?",
            (tenant, channel, str(native_id)),
        )
        self._db.commit()

    def links_for_user(self, tenant: str, user_id: str) -> list[dict]:
        rows = self._db.execute(
            "SELECT channel,native_id FROM identity WHERE tenant=? AND user_id=?", (tenant, user_id)
        ).fetchall()
        return [{"channel": r["channel"], "native_id": r["native_id"]} for r in rows]

    # ---- link tokens (issued from the authenticated profile) -------------
    def issue_token(self, tenant: str, user_id: str, channel: str) -> str:
        token = secrets.token_urlsafe(8)
        self._db.execute(
            "INSERT INTO link_token (token,tenant,user_id,channel,created_at,used) VALUES (?,?,?,?,?,0)",
            (token, tenant, user_id, channel, time.time()),
        )
        self._db.commit()
        return token

    def redeem_token(self, token: str, native_id: str) -> str | None:
        """Bind ``native_id`` to the token's user (called when the bot receives the token).
        Returns the user_id on success, else None (unknown/expired/used)."""
        r = self._db.execute("SELECT * FROM link_token WHERE token=?", (token,)).fetchone()
        if not r or r["used"] or (time.time() - r["created_at"]) > _TOKEN_TTL:
            return None
        self.link(r["tenant"], r["channel"], native_id, r["user_id"])
        self._db.execute("UPDATE link_token SET used=1 WHERE token=?", (token,))
        self._db.commit()
        return r["user_id"]
