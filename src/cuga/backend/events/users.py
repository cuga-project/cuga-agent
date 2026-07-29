"""Local user store — tenant → users (id, email, roles), for self-host without an IdP.

The *identity anchor* (decision 0007). A user logs in (here, or via CUGA's OIDC) → a principal;
their profile then holds the channels they've linked + the integrations they've connected. For
SSO deployments this store is bypassed (the OIDC ``sub`` is the user); here it lets an admin add
users and lets us e2e real two-user isolation.

Passwords (optional) are PBKDF2-hashed (stdlib ``hashlib``). Roles gate agent access + admin.
Stdlib ``sqlite3`` only → flat-loadable + offline-testable.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass, field


def _hash(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120_000).hex()


@dataclass
class User:
    user_id: str
    email: str = ""
    roles: list = field(default_factory=list)     # e.g. ["user"], ["admin"], ["builder"]
    tenant: str = "default"

    def has_role(self, role: str) -> bool:
        return role in (self.roles or [])


class UserStore:
    def __init__(self, db_path: str = ":memory:"):
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS app_user (
                 tenant TEXT NOT NULL,
                 user_id TEXT NOT NULL,
                 email TEXT NOT NULL DEFAULT '',
                 roles TEXT NOT NULL DEFAULT '["user"]',
                 salt TEXT NOT NULL DEFAULT '',
                 pwhash TEXT NOT NULL DEFAULT '',
                 created_at REAL NOT NULL DEFAULT 0,
                 PRIMARY KEY (tenant, user_id)
               )""")
        self._db.commit()

    def add(self, user_id: str, *, email: str = "", roles: list | None = None,
            password: str | None = None, tenant: str = "default") -> User:
        salt = os.urandom(16)
        pwhash = _hash(password, salt) if password else ""
        self._db.execute(
            """INSERT INTO app_user (tenant,user_id,email,roles,salt,pwhash,created_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(tenant,user_id) DO UPDATE SET
                 email=excluded.email, roles=excluded.roles,
                 salt=CASE WHEN excluded.pwhash!='' THEN excluded.salt ELSE app_user.salt END,
                 pwhash=CASE WHEN excluded.pwhash!='' THEN excluded.pwhash ELSE app_user.pwhash END""",
            (tenant, user_id, email, json.dumps(roles or ["user"]),
             salt.hex() if password else "", pwhash, time.time()))
        self._db.commit()
        return User(user_id=user_id, email=email, roles=roles or ["user"], tenant=tenant)

    def _row(self, r: sqlite3.Row) -> User:
        return User(user_id=r["user_id"], email=r["email"], roles=json.loads(r["roles"]),
                    tenant=r["tenant"])

    def get(self, user_id: str, tenant: str = "default") -> User | None:
        r = self._db.execute("SELECT * FROM app_user WHERE tenant=? AND user_id=?",
                             (tenant, user_id)).fetchone()
        return self._row(r) if r else None

    def by_email(self, email: str, tenant: str = "default") -> User | None:
        r = self._db.execute("SELECT * FROM app_user WHERE tenant=? AND email=?",
                             (tenant, email)).fetchone()
        return self._row(r) if r else None

    def list(self, tenant: str = "default") -> list[User]:
        rows = self._db.execute("SELECT * FROM app_user WHERE tenant=? ORDER BY user_id",
                               (tenant,)).fetchall()
        return [self._row(r) for r in rows]

    def authenticate(self, email: str, password: str, tenant: str = "default") -> User | None:
        r = self._db.execute("SELECT * FROM app_user WHERE tenant=? AND email=?",
                             (tenant, email)).fetchone()
        if not r or not r["pwhash"]:
            return None
        if _hash(password, bytes.fromhex(r["salt"])) == r["pwhash"]:
            return self._row(r)
        return None
