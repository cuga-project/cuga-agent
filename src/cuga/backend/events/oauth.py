"""OAuth connect — **CUGA hosts the connect UX; Activepieces holds the token.**

Self-host (BofA-style) model: the platform registers an OAuth app per provider (client id/secret)
once; CUGA hosts the redirect + callback so a chatting user logs in with THEIR OWN account; the
resulting token is stored as an AP connection (``ea::<tenant>::<user>::<app>``) which AP refreshes.

Two kinds of integration auth:
  - **oauth**  (gmail/box/slack/**github**/outlook) — redirect-based consent. CUGA builds the
    authorize URL and the user consents; the code→token exchange is then done by AP
    (``UpsertOAuth2Request`` wants the CODE, not tokens), so ``exchange_code`` below is currently
    unused. GitHub belongs HERE, not under "token": ``PROVIDERS`` carries its authorize URL and
    ``setup/GITHUB.md`` is explicit that a pasted PAT is *rejected* — AP accepts it into the
    connection store and it is then unusable for the piece triggers.
  - **token**  (telegram bot) — no redirect; the user pastes a secret → AP SECRET_TEXT
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

try:
    from . import db as _db
except ImportError:  # flat load (tests put the events dir on sys.path)
    import db as _db

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

# provider registry — endpoints + default scopes + the AP piece the connection is for.
PROVIDERS: dict[str, dict] = {
    "gmail": {
        "kind": "oauth",
        "piece": "@activepieces/piece-gmail",
        "auth": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "scopes": ["https://www.googleapis.com/auth/gmail.modify"],
        "extra_auth": {"access_type": "offline", "prompt": "consent"},
    },
    "box": {
        "kind": "oauth",
        "piece": "@activepieces/piece-box",
        "auth": "https://account.box.com/api/oauth2/authorize",
        "token": "https://api.box.com/oauth2/token",
        "scopes": [],
    },
    "slack": {
        "kind": "oauth",
        "piece": "@activepieces/piece-slack",
        "auth": "https://slack.com/oauth/v2/authorize",
        "token": "https://slack.com/api/oauth.v2.access",
        "scopes": ["chat:write", "channels:read"],
    },
    "outlook": {
        "kind": "oauth",
        "piece": "@activepieces/piece-microsoft-outlook",
        "auth": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "scopes": ["Mail.ReadWrite", "offline_access"],
    },
    # GitHub is an OAuth app, NOT a pasted PAT. `@activepieces/piece-github` accepts only OAUTH2 or
    # CUSTOM_AUTH — verify with `GET $AP_BASE_URL/api/v1/pieces/@activepieces/piece-github`. A PAT
    # stored as SECRET_TEXT is accepted by AP's connection store and is then silently unusable: the
    # piece runs with no credential and GitHub answers "401 Bad credentials", which is
    # indistinguishable from an under-scoped token. That cost real hours. Don't put it back.
    # `admin:repo_hook` is what lets the PR/issue trigger create the repository webhook.
    "github": {
        "kind": "oauth",
        "piece": "@activepieces/piece-github",
        "auth": "https://github.com/login/oauth/authorize",
        "token": "https://github.com/login/oauth/access_token",
        "scopes": ["repo", "admin:repo_hook"],
    },
    # Google Calendar — same Google OAuth endpoints as gmail (you can reuse the SAME Google Cloud
    # OAuth client: set EVENTS_OAUTH_GOOGLE_CALENDAR_CLIENT_ID/_SECRET to the gmail values and add
    # the calendar callback URI to that client). Calendar scope covers the read triggers.
    "google_calendar": {
        "kind": "oauth",
        "piece": "@activepieces/piece-google-calendar",
        "auth": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "scopes": ["https://www.googleapis.com/auth/calendar"],
        "extra_auth": {"access_type": "offline", "prompt": "consent"},
    },
    # Pinterest — its own OAuth app. Read scopes cover the pin/board/follower triggers. Pinterest's
    # v5 token endpoint REQUIRES HTTP Basic auth (client creds in the Authorization header); the
    # default BODY method makes AP's exchange fail with INVALID_CLAIM.
    "pinterest": {
        "kind": "oauth",
        "piece": "@activepieces/piece-pinterest",
        "auth": "https://www.pinterest.com/oauth/",
        "token": "https://api.pinterest.com/v5/oauth/token",
        "authorization_method": "HEADER",
        "scopes": ["boards:read", "pins:read", "user_accounts:read"],
    },
    # token apps — no redirect; the user pastes a secret (handled by ensure_secret_connection).
    # Only pieces whose `auth` really lists SECRET_TEXT belong here.
    "telegram": {"kind": "token", "piece": "@activepieces/piece-telegram-bot"},
    "discord": {"kind": "token", "piece": "@activepieces/piece-discord"},  # Bot Token (SECRET_TEXT)
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
    try:
        from .secret_seam import secret as _secret
    except ImportError:  # flat load
        from secret_seam import secret as _secret
    return _secret(f"EVENTS_OAUTH_{app.upper()}_{key}")


def _fernet():
    """Fernet on ``CUGA_SECRET_KEY``, or None when it is unset. Never raises — an unreadable key
    must degrade to plaintext, not take the store down.

    Built HERE rather than imported from ``cuga.backend.storage.secrets_store``. The events package
    imports exactly one thing from CUGA core (``resolve_secret``, via the seam) and
    ``test_core_decoupled`` enforces it, so that the layer can run beside a different CUGA build.
    Same key, same algorithm, same ciphertext — interoperable with core's store, just not coupled
    to it. The key itself comes through the seam, so ``CUGA_SECRET_KEY=vault://…`` works.
    """
    try:
        from cryptography.fernet import Fernet
    except Exception:  # noqa: BLE001 — cryptography absent → plaintext, as before
        return None
    try:
        from .secret_seam import secret as _secret
    except ImportError:  # flat load (tests put the events dir on sys.path)
        from secret_seam import secret as _secret
    key = (_secret("CUGA_SECRET_KEY") or "").strip()
    if not key:
        return None
    try:
        return Fernet(key.encode())
    except Exception:  # noqa: BLE001 — a malformed key must not break setup
        return None


_ENC = "fernet:"  # marker prefix, so a store can hold both old plaintext and new ciphertext


def _enc_secret(v: str) -> str:
    f = _fernet()
    if not f or not v or v.startswith(_ENC):
        return v
    try:
        return _ENC + f.encrypt(v.encode()).decode()
    except Exception:  # noqa: BLE001
        return v


def _dec_secret(v: str) -> str:
    if not v or not v.startswith(_ENC):
        return v  # legacy plaintext row, or no key configured — read it as-is
    f = _fernet()
    if not f:
        return ""  # encrypted but the key is gone: return nothing rather than ciphertext
    try:
        return f.decrypt(v[len(_ENC) :].encode()).decode()
    except Exception:  # noqa: BLE001
        return ""


class OAuthAppStore:
    """Admin-entered OAuth app credentials (client id/secret per provider), so setup is UI-only.

    ``client_secret`` is ENCRYPTED AT REST with CUGA's Fernet (``CUGA_SECRET_KEY``) when a key is
    configured — the value is stored as ``fernet:<ciphertext>``. Without a key it stays plaintext,
    which keeps dev and existing rows working; the marker prefix is what lets both live in one
    column, so this needs no migration and rows re-encrypt as they are rewritten.

    A deployment that accepts admin-entered OAuth secrets should set ``CUGA_SECRET_KEY``. Note the
    client_id is deliberately NOT encrypted: it is not a secret, and leaving it readable keeps
    ``status()`` and debugging honest. Runs on SQLite or Postgres via db.connect."""

    def __init__(self, db_path: str = ":memory:"):
        self._db = _db.connect(db_path)
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS oauth_app (
                 tenant TEXT NOT NULL, app TEXT NOT NULL,
                 client_id TEXT NOT NULL DEFAULT '', client_secret TEXT NOT NULL DEFAULT '',
                 scopes TEXT NOT NULL DEFAULT '', PRIMARY KEY (tenant, app))"""
        )
        self._db.commit()

    def set(self, tenant: str, app: str, client_id: str, client_secret: str, scopes: str = "") -> None:
        self._db.execute(
            """INSERT INTO oauth_app (tenant,app,client_id,client_secret,scopes)
               VALUES (?,?,?,?,?) ON CONFLICT(tenant,app) DO UPDATE SET
                 client_id=excluded.client_id, client_secret=excluded.client_secret,
                 scopes=excluded.scopes""",
            (tenant, app.lower(), client_id, _enc_secret(client_secret), scopes),
        )
        self._db.commit()

    def get(self, tenant: str, app: str, key: str) -> str:
        col = {"CLIENT_ID": "client_id", "CLIENT_SECRET": "client_secret", "SCOPES": "scopes"}.get(
            key.upper()
        )
        if not col:
            return ""
        r = self._db.execute(
            f"SELECT {col} FROM oauth_app WHERE tenant=? AND app=?", (tenant, app.lower())
        ).fetchone()
        if not r:
            return ""
        return _dec_secret(r[0]) if col == "client_secret" else r[0]

    def status(self, tenant: str) -> list[dict]:
        """Per provider: is it configured? (never returns the secret)."""
        rows = {
            r[0]: r
            for r in self._db.execute(
                "SELECT app,client_id,client_secret FROM oauth_app WHERE tenant=?", (tenant,)
            ).fetchall()
        }
        out = []
        for app, p in PROVIDERS.items():
            if p["kind"] != "oauth":
                continue
            r = rows.get(app)
            out.append(
                {"app": app, "configured": bool(r and r[1] and r[2]), "client_id_set": bool(r and r[1])}
            )
        return out


def is_configured(app: str) -> bool:
    """Can we actually connect this app right now?"""
    p = provider(app)
    if not p:
        return False
    if p["kind"] == "token":
        return True  # a token can always be pasted
    return bool(_env(app, "CLIENT_ID") and _env(app, "CLIENT_SECRET"))


def public_base() -> str:
    return (
        os.environ.get("EVENTS_PUBLIC_URL")
        or os.environ.get("HOST_CALLBACK_URL", "http://localhost:8000").rsplit("/invoke", 1)[0]
    ).rstrip("/")


def redirect_uri(app: str) -> str:
    return f"{public_base()}/api/events/connect/{app}/callback"


_RANDOM_STATE_KEY: bytes | None = None
STATE_MAX_AGE_SECS = 15 * 60


def _state_key() -> bytes:
    """The HMAC key for OAuth ``state``. Prefers an explicit EVENTS_STATE_KEY; falls back to other
    server-held secrets so single-box deploys are signed without extra config; last resort is a
    per-process random key (state still verifies within one process — a `make reload` mid-consent
    just means clicking Connect again). Multi-replica deploys must set EVENTS_STATE_KEY."""
    for var in ("EVENTS_STATE_KEY", "GATEWAY_TOKEN", "AP_JWT_SECRET", "AP_PASSWORD"):
        v = (os.environ.get(var, "") or "").split(" #", 1)[0].strip()
        if v:
            return hashlib.sha256(f"events-state:{v}".encode()).digest()
    global _RANDOM_STATE_KEY
    if _RANDOM_STATE_KEY is None:
        _RANDOM_STATE_KEY = secrets.token_bytes(32)
    return _RANDOM_STATE_KEY


def encode_state(**kw) -> str:
    """HMAC-signed, expiring OAuth state: ``b64(payload).hexsig``.

    The callback is UNAUTHENTICATED and used to trust a plain-base64 state for the principal —
    a crafted state could bind a freshly-authorized credential into an arbitrary user's scope
    (login-CSRF / connection hijack) and use ``ret`` as an open redirect. Signing makes the state
    tamper-proof; the timestamp bounds the replay window."""
    kw["ts"] = int(time.time())
    payload = base64.urlsafe_b64encode(json.dumps(kw).encode()).decode()
    sig = hmac.new(_state_key(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def decode_state(state: str) -> dict:
    """Verify + decode a signed state. Returns {} on ANY failure (bad signature, expired,
    malformed) — callers must treat {} as a hard reject, not a fallback."""
    try:
        payload, sig = state.rsplit(".", 1)
        want = hmac.new(_state_key(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(want, sig):
            return {}
        d = json.loads(base64.urlsafe_b64decode(payload.encode()).decode())
        if int(time.time()) - int(d.get("ts", 0)) > STATE_MAX_AGE_SECS:
            return {}
        return d
    except Exception:  # noqa: BLE001
        return {}


def authorize_url(app: str, state: str) -> str | None:
    """Build the provider consent URL (or None if not an oauth app / not configured)."""
    from urllib.parse import urlencode

    p = provider(app)
    if not p or p["kind"] != "oauth" or not is_configured(app):
        return None
    scopes = os.environ.get(f"EVENTS_OAUTH_{app.upper()}_SCOPES", " ".join(p.get("scopes", [])))
    params = {
        "client_id": _env(app, "CLIENT_ID"),
        "redirect_uri": redirect_uri(app),
        "response_type": "code",
        "scope": scopes,
        "state": state,
        **p.get("extra_auth", {}),
    }
    return f"{p['auth']}?{urlencode(params)}"


async def exchange_code(app: str, code: str) -> dict:
    """Exchange an auth code for tokens (CUGA does the exchange; AP then stores/refreshes)."""
    import httpx

    p = provider(app)
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri(app),
        "client_id": _env(app, "CLIENT_ID"),
        "client_secret": _env(app, "CLIENT_SECRET"),
    }
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(p["token"], data=data, headers={"Accept": "application/json"})
        r.raise_for_status()
        return r.json()
