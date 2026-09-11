"""OAuth USER tokens — store, expiry, and refresh. The piece Activepieces used to own.

``oauth.py`` gets you as far as an authorization code and exchanges it for tokens; its own
docstring then said "AP then stores/refreshes". Going AP-free removes that, and nothing replaced
it: the tokens were returned and dropped on the floor. This module is the replacement.

WHY IT MATTERS MORE THAN IT LOOKS. Box and GitHub deliberately avoid needing this — client-
credentials and GitHub-App installation tokens are re-minted from static config, so there is no
refresh token to keep. **Gmail, Google Calendar and Outlook cannot do that.** Their access tokens
last an hour and the only way back is a refresh token, so a durable, encrypted, refresh-aware
store is the hard prerequisite for every one of them.

THREE THINGS THIS GETS RIGHT, because each has bitten someone:

  * **Refresh BEFORE expiry, not after a 401.** Reacting to a 401 means every token's death is a
    user-visible failure first. We renew a couple of minutes early instead.
  * **A rotated refresh token must be persisted.** Google and Microsoft may hand back a NEW refresh
    token on every refresh; keeping the old one means the next refresh fails and the user has to
    reconnect. Providers that omit it (the common case) keep the existing one.
  * **Encrypted at rest, with the same marker scheme as ``oauth_app``** — so a store can hold both
    legacy plaintext and ciphertext, and rows re-encrypt as they are rewritten. No migration.

Deliberately NOT a general secrets store: those live behind ``secret_seam``/Vault. These are
per-user, per-provider grants that only exist after a human clicked Allow.
"""

from __future__ import annotations

import logging
import time

try:
    from . import db as _db
    from .oauth import _dec_secret, _enc_secret, _env, provider
except ImportError:  # flat load (tests put the events dir on sys.path)
    import db as _db  # type: ignore
    from oauth import _dec_secret, _enc_secret, _env, provider  # type: ignore

log = logging.getLogger("cuga.events.oauth")

# Renew this many seconds before the provider's stated expiry. Long enough that a slow agent run
# started with a valid token does not finish with a dead one.
REFRESH_SKEW = 120.0


class TokenStore:
    """Per (tenant, app, user) OAuth grant, with the refresh token needed to keep it alive."""

    def __init__(self, db_path: str = ":memory:"):
        self._db = _db.connect(db_path)
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS oauth_token (
                 tenant TEXT NOT NULL, app TEXT NOT NULL, user_id TEXT NOT NULL DEFAULT '',
                 access_token TEXT NOT NULL DEFAULT '', refresh_token TEXT NOT NULL DEFAULT '',
                 expires_at REAL NOT NULL DEFAULT 0, scopes TEXT NOT NULL DEFAULT '',
                 updated_at REAL NOT NULL DEFAULT 0,
                 PRIMARY KEY (tenant, app, user_id))"""
        )
        self._db.commit()

    # ── persistence ────────────────────────────────────────────────────────────────────────────
    def save(self, tenant: str, app: str, user_id: str, tokens: dict) -> None:
        """Persist a token response. ``expires_in`` (seconds) becomes an absolute deadline, because
        a relative TTL is meaningless once it has been sitting in a database."""
        app, user_id = app.lower(), user_id or ""
        access = str(tokens.get("access_token") or "")
        refresh = str(tokens.get("refresh_token") or "")
        try:
            ttl = float(tokens.get("expires_in") or 3600)
        except (TypeError, ValueError):
            ttl = 3600.0
        # A refresh that omits refresh_token keeps the existing one — dropping it would silently
        # end the grant at the NEXT refresh, long after the change that caused it.
        if not refresh:
            refresh = self._raw_refresh(tenant, app, user_id)
        now = time.time()
        self._db.execute(
            """INSERT INTO oauth_token
                 (tenant,app,user_id,access_token,refresh_token,expires_at,scopes,updated_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(tenant,app,user_id) DO UPDATE SET
                 access_token=excluded.access_token, refresh_token=excluded.refresh_token,
                 expires_at=excluded.expires_at, scopes=excluded.scopes,
                 updated_at=excluded.updated_at""",
            (
                tenant,
                app,
                user_id,
                _enc_secret(access),
                _enc_secret(refresh),
                now + ttl,
                str(tokens.get("scope") or ""),
                now,
            ),
        )
        self._db.commit()

    def _row(self, tenant: str, app: str, user_id: str):
        return self._db.execute(
            "SELECT access_token,refresh_token,expires_at,scopes FROM oauth_token "
            "WHERE tenant=? AND app=? AND user_id=?",
            (tenant, app.lower(), user_id or ""),
        ).fetchone()

    def _raw_refresh(self, tenant: str, app: str, user_id: str) -> str:
        r = self._row(tenant, app, user_id)
        return _dec_secret(r[1]) if r else ""

    def get(self, tenant: str, app: str, user_id: str = "") -> dict:
        """The stored grant, decrypted, or {} when there is none."""
        r = self._row(tenant, app, user_id)
        if not r:
            return {}
        return {
            "access_token": _dec_secret(r[0]),
            "refresh_token": _dec_secret(r[1]),
            "expires_at": float(r[2] or 0),
            "scopes": r[3] or "",
        }

    def forget(self, tenant: str, app: str, user_id: str = "") -> None:
        self._db.execute(
            "DELETE FROM oauth_token WHERE tenant=? AND app=? AND user_id=?",
            (tenant, app.lower(), user_id or ""),
        )
        self._db.commit()

    def connected(self, tenant: str, app: str, user_id: str = "") -> bool:
        """Is there a usable grant — i.e. a live token, or a refresh token to get one?"""
        g = self.get(tenant, app, user_id)
        if not g:
            return False
        return bool(g["refresh_token"]) or g["expires_at"] > time.time()

    # ── the accessor everything else should use ────────────────────────────────────────────────
    async def access_token(self, tenant: str, app: str, user_id: str = "", force: bool = False) -> str:
        """A VALID access token, refreshing first if it is close to expiry. "" when unavailable.

        This is the only method callers should need: asking for a token and getting a live one is
        the whole point, and it means no call site has to remember to check an expiry.
        """
        g = self.get(tenant, app, user_id)
        if not g:
            return ""
        if not force and g["access_token"] and time.time() < g["expires_at"] - REFRESH_SKEW:
            return g["access_token"]
        if not g["refresh_token"]:
            # Expired with no way back: the user must reconnect. Say so once, clearly.
            log.warning(
                "oauth: %s token for user=%r expired and there is no refresh token — reconnect needed",
                app,
                user_id or "(default)",
            )
            return ""
        fresh = await self.refresh(tenant, app, user_id)
        return fresh.get("access_token", "")

    async def refresh(self, tenant: str, app: str, user_id: str = "") -> dict:
        """Exchange the refresh token for a new grant and persist it. {} on failure."""
        import httpx

        g = self.get(tenant, app, user_id)
        if not g or not g["refresh_token"]:
            return {}
        p = provider(app)
        if not p or not p.get("token"):
            log.warning("oauth: no token endpoint known for %r — cannot refresh", app)
            return {}
        data = {
            "grant_type": "refresh_token",
            "refresh_token": g["refresh_token"],
            "client_id": _env(app, "CLIENT_ID"),
            "client_secret": _env(app, "CLIENT_SECRET"),
        }
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.post(p["token"], data=data, headers={"Accept": "application/json"})
        except Exception as e:  # noqa: BLE001 — a refresh failure must not take a poll loop down
            log.warning("oauth: %s refresh failed (%s)", app, e)
            return {}
        if r.status_code != 200:
            # A 400 here usually means the grant was revoked or the refresh token already rotated.
            # Never log the body: it echoes the client_secret on some providers.
            log.warning(
                "oauth: %s refresh returned HTTP %s — the user may need to reconnect",
                app,
                r.status_code,
            )
            return {}
        tokens = r.json() or {}
        if not tokens.get("access_token"):
            log.warning("oauth: %s refresh response carried no access_token", app)
            return {}
        self.save(tenant, app, user_id, tokens)
        log.info("oauth: refreshed the %s token for user=%r", app, user_id or "(default)")
        return self.get(tenant, app, user_id)

    # ── background renewal ─────────────────────────────────────────────────────────────────────
    def due(self, within: float = 600.0) -> list[tuple[str, str, str]]:
        """(tenant, app, user_id) for grants expiring within ``within`` seconds that CAN be renewed.

        Used by the renewal pass so a token does not have to wait for the next request to notice
        it is dying — which matters for a poller that runs every 15 minutes.
        """
        cutoff = time.time() + within
        rows = self._db.execute(
            "SELECT tenant,app,user_id,refresh_token,expires_at FROM oauth_token WHERE expires_at < ?",
            (cutoff,),
        ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows if _dec_secret(r[3])]

    async def renew_due(self, within: float = 600.0) -> int:
        """Refresh everything close to expiry. Returns how many succeeded. Never raises."""
        n = 0
        for tenant, app, user_id in self.due(within):
            try:
                if (await self.refresh(tenant, app, user_id)).get("access_token"):
                    n += 1
            except Exception as e:  # noqa: BLE001
                log.warning("oauth: renewal of %s/%s failed (%s)", app, user_id or "(default)", e)
        return n
