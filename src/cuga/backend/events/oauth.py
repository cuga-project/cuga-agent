"""OAuth connect — **CUGA hosts the connect UX; Activepieces holds the token.**

Self-host (BofA-style) model: the platform registers an OAuth app per provider (client id/secret)
once; CUGA hosts the redirect + callback so a chatting user logs in with THEIR OWN account; the
resulting token is stored as an AP connection (``ea::<tenant>::<user>::<app>``) which AP refreshes.

Two kinds of integration auth:
  - **oauth**  (gmail/box/slack/outlook) — redirect-based consent. CUGA builds the authorize URL,
    the user consents, CUGA exchanges the code, then creates an AP OAUTH2 connection.
  - **token**  (github PAT, telegram bot) — no redirect; the user pastes a secret → AP SECRET_TEXT
    connection (``APEngine.ensure_secret_connection``, already built).

BOUNDARY (clarified): **CUGA hosts the connect UX for EVERY connector — the user never hops to
AP's UI.** Token apps paste a secret; OAuth apps (Box/Gmail/Slack) run the redirect here; then CUGA
**passes the credential to AP** (``ensure_secret_connection`` / ``ensure_oauth_connection``) and AP
holds + refreshes it and does the trigger/delivery. So "AP owns integrations" = AP EXECUTES + HOLDS
tokens; CUGA owns the connect UX. The only realignment TODO: the ``PROVIDERS`` table's auth/token
URLs duplicate what AP's pieces already know — source them from **AP piece metadata**
(``/api/v1/pieces/<piece>``) so no provider specifics are hardcoded in CUGA (table → fallback).
The connect flow stays CUGA-hosted regardless.

Config (per provider, self-host):
    EVENTS_OAUTH_<APP>_CLIENT_ID / EVENTS_OAUTH_<APP>_CLIENT_SECRET   (e.g. EVENTS_OAUTH_GMAIL_CLIENT_ID)
    EVENTS_OAUTH_<APP>_SCOPES   (optional, space-separated; else the default below)
    EVENTS_PUBLIC_URL           (the externally-reachable base; the redirect_uri is
                                 <EVENTS_PUBLIC_URL>/api/events/connect/<app>/callback)
Nothing is configured by default → /connect/<app> returns a clear "not configured" message,
so the layer degrades gracefully.
"""

from __future__ import annotations

import base64
import json
import os

# provider registry — endpoints + default scopes + the AP piece the connection is for.
PROVIDERS: dict[str, dict] = {
    "gmail": {"kind": "oauth", "piece": "@activepieces/piece-gmail",
              "auth": "https://accounts.google.com/o/oauth2/v2/auth",
              "token": "https://oauth2.googleapis.com/token",
              "scopes": ["https://www.googleapis.com/auth/gmail.modify"],
              "extra_auth": {"access_type": "offline", "prompt": "consent"}},
    "box": {"kind": "oauth", "piece": "@activepieces/piece-box",
            "auth": "https://account.box.com/api/oauth2/authorize",
            "token": "https://api.box.com/oauth2/token", "scopes": []},
    "slack": {"kind": "oauth", "piece": "@activepieces/piece-slack",
              "auth": "https://slack.com/oauth/v2/authorize",
              "token": "https://slack.com/api/oauth.v2.access",
              "scopes": ["chat:write", "channels:read"]},
    "outlook": {"kind": "oauth", "piece": "@activepieces/piece-microsoft-outlook",
                "auth": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
                "token": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
                "scopes": ["Mail.ReadWrite", "offline_access"]},
    # token apps — no redirect; user pastes a secret (handled by ensure_secret_connection)
    "github": {"kind": "token", "piece": "@activepieces/piece-github"},
    "telegram": {"kind": "token", "piece": "@activepieces/piece-telegram-bot"},
    "discord": {"kind": "token", "piece": "@activepieces/piece-discord"},   # Bot Token (SECRET_TEXT)
}


def provider(app: str) -> dict | None:
    return PROVIDERS.get((app or "").lower())


def connect_kind(app: str) -> str | None:
    p = provider(app)
    return p["kind"] if p else None


# Optional resolver so OAuth app creds can come from an admin-managed store (the Admin → OAuth
# apps UI) instead of only .env. The app layer sets it; default = env only.
_cred_resolver = None


def set_cred_resolver(fn) -> None:
    """fn(app, key) -> value | None. Checked BEFORE env, so the Admin UI overrides .env."""
    global _cred_resolver
    _cred_resolver = fn


def _env(app: str, key: str) -> str:
    if _cred_resolver is not None:
        try:
            v = _cred_resolver(app, key)
            if v:
                return v
        except Exception:  # noqa: BLE001
            pass
    return os.environ.get(f"EVENTS_OAUTH_{app.upper()}_{key}", "")


class OAuthAppStore:
    """Admin-entered OAuth app credentials (client id/secret per provider), so setup is UI-only.
    NOTE: sqlite plaintext at rest (parity with .env). Production TODO: back with CUGA's Fernet
    secrets. Stdlib sqlite → flat-loadable + offline-testable."""

    def __init__(self, db_path: str = ":memory:"):
        import sqlite3
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS oauth_app (
                 tenant TEXT NOT NULL, app TEXT NOT NULL,
                 client_id TEXT NOT NULL DEFAULT '', client_secret TEXT NOT NULL DEFAULT '',
                 scopes TEXT NOT NULL DEFAULT '', PRIMARY KEY (tenant, app))""")
        self._db.commit()

    def set(self, tenant: str, app: str, client_id: str, client_secret: str,
            scopes: str = "") -> None:
        self._db.execute(
            """INSERT INTO oauth_app (tenant,app,client_id,client_secret,scopes)
               VALUES (?,?,?,?,?) ON CONFLICT(tenant,app) DO UPDATE SET
                 client_id=excluded.client_id, client_secret=excluded.client_secret,
                 scopes=excluded.scopes""",
            (tenant, app.lower(), client_id, client_secret, scopes))
        self._db.commit()

    def get(self, tenant: str, app: str, key: str) -> str:
        col = {"CLIENT_ID": "client_id", "CLIENT_SECRET": "client_secret",
               "SCOPES": "scopes"}.get(key.upper())
        if not col:
            return ""
        r = self._db.execute(f"SELECT {col} FROM oauth_app WHERE tenant=? AND app=?",
                             (tenant, app.lower())).fetchone()
        return r[0] if r else ""

    def status(self, tenant: str) -> list[dict]:
        """Per provider: is it configured? (never returns the secret)."""
        rows = {r[0]: r for r in self._db.execute(
            "SELECT app,client_id,client_secret FROM oauth_app WHERE tenant=?", (tenant,)).fetchall()}
        out = []
        for app, p in PROVIDERS.items():
            if p["kind"] != "oauth":
                continue
            r = rows.get(app)
            out.append({"app": app, "configured": bool(r and r[1] and r[2]),
                        "client_id_set": bool(r and r[1])})
        return out


def is_configured(app: str) -> bool:
    """Can we actually connect this app right now?"""
    p = provider(app)
    if not p:
        return False
    if p["kind"] == "token":
        return True                       # a token can always be pasted
    return bool(_env(app, "CLIENT_ID") and _env(app, "CLIENT_SECRET"))


def public_base() -> str:
    return (os.environ.get("EVENTS_PUBLIC_URL")
            or os.environ.get("HOST_CALLBACK_URL", "http://localhost:8000").rsplit("/invoke", 1)[0]
            ).rstrip("/")


def redirect_uri(app: str) -> str:
    return f"{public_base()}/api/events/connect/{app}/callback"


def encode_state(**kw) -> str:
    return base64.urlsafe_b64encode(json.dumps(kw).encode()).decode()


def decode_state(state: str) -> dict:
    try:
        return json.loads(base64.urlsafe_b64decode(state.encode()).decode())
    except Exception:  # noqa: BLE001
        return {}


def authorize_url(app: str, state: str) -> str | None:
    """Build the provider consent URL (or None if not an oauth app / not configured)."""
    from urllib.parse import urlencode
    p = provider(app)
    if not p or p["kind"] != "oauth" or not is_configured(app):
        return None
    scopes = os.environ.get(f"EVENTS_OAUTH_{app.upper()}_SCOPES", " ".join(p.get("scopes", [])))
    params = {"client_id": _env(app, "CLIENT_ID"), "redirect_uri": redirect_uri(app),
              "response_type": "code", "scope": scopes, "state": state,
              **p.get("extra_auth", {})}
    return f"{p['auth']}?{urlencode(params)}"


async def exchange_code(app: str, code: str) -> dict:
    """Exchange an auth code for tokens (CUGA does the exchange; AP then stores/refreshes)."""
    import httpx
    p = provider(app)
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri(app),
            "client_id": _env(app, "CLIENT_ID"), "client_secret": _env(app, "CLIENT_SECRET")}
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(p["token"], data=data,
                         headers={"Accept": "application/json"})
        r.raise_for_status()
        return r.json()
