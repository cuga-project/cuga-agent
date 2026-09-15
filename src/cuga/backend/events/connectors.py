"""Connector descriptors + live status — the source of truth the Studio UI renders.

The UI is **dumb**: it does not know which channels/integrations exist or how to tell if one is
connected. It asks these endpoints and paints the result. So all of that knowledge lives here,
server-side, next to the code that actually uses it (Principle: UI in sync with functionality).

Two views of one ``connector`` idea (the events docs (ARCHITECTURE.md)):
  - **Channel**   — converse-with (web/telegram/discord/slack); a human on the other end.
  - **Integration** — watch/act-on (gmail/box/github/outlook); an app on the other end.

Status is derived from **real state**, never hardcoded "connected":
  - channels  → presence of the bot token in env (the thing that actually enables inbound/outbound).
  - integrations → an AP **connection** whose externalId names the app (AP owns creds).
So what the UI shows is exactly what the backend can do right now.

``live`` = wired end-to-end and verified. Channels deliver two-way today: web (built-in),
Slack + Discord (DIRECT backends — Events API / Gateway), and Telegram, which is direct long-poll
by default with the AP webhook available behind ``EVENTS_TELEGRAM_BACKEND=ap``.
Integrations watch/act through AP (OAuth/token connection + a piece trigger). ``outlook`` is the
one connector still genuinely planned.
"""

from __future__ import annotations

import os

# --- descriptors ---------------------------------------------------------------
# ``env`` = the env var whose presence means "this channel can send/receive".
# ``live`` = wired end-to-end and verified. ``backend`` names how a channel talks to the outside
# world: direct (CUGA owns the socket) vs AP (Activepieces piece). See delivery.channel_backend.
CHANNELS = [
    {
        "name": "web",
        "label": "Web chat",
        "env": None,
        "live": True,
        "backend": "direct",
        "note": "the built-in /chat + /api/concierge surface — always on",
    },
    {
        "name": "telegram",
        "label": "Telegram",
        "env": "TELEGRAM_BOT_TOKEN",
        "live": True,
        # Resolved per-request from EVENTS_TELEGRAM_BACKEND — see _telegram_backend() below. These
        # were hardcoded to the AP webhook, which contradicted every other reader of that variable
        # (app.py, capability.py, telegram_direct.py and delivery.py all default to direct).
        "backend": None,
        "note": None,
    },
    {
        "name": "discord",
        "label": "Discord",
        "env": "DISCORD_BOT_TOKEN",
        "live": True,
        "backend": "direct",
        "note": "two-way via the direct Gateway WebSocket bot (instant)",
    },
    {
        "name": "slack",
        "label": "Slack",
        "env": "SLACK_BOT_TOKEN",
        "live": True,
        "backend": "direct",
        "note": "two-way via the direct Events API (signed) + chat.postMessage",
    },
]

# ``app`` = the AP piece / substring we match a connection's externalId against.
# ``auth`` = how a connection is created: ``oauth`` (authorized in AP's connect UI) vs
# ``token`` (a PAT/secret that can be created via the API).
INTEGRATIONS = [
    {
        "name": "gmail",
        "label": "Gmail",
        "app": "gmail",
        "auth": "oauth",
        "live": True,
        "note": "AP OAuth connection + new-email trigger (source) / send-email (sink)",
    },
    {
        "name": "box",
        "label": "Box",
        "app": "box",
        "auth": "oauth",
        "live": True,
        "note": "AP OAuth (new-file resume watcher); direct-poll opt-in via EVENTS_BOX_BACKEND=direct",
    },
    {
        "name": "github",
        "label": "GitHub",
        "app": "github",
        "auth": "oauth",
        "live": True,
        "note": "AP OAuth connection (repo + admin:repo_hook) + new-PR trigger (pr_reviewer). "
        "NOT a pasted PAT: piece-github accepts only OAUTH2/CUSTOM_AUTH",
    },
    {
        "name": "google_calendar",
        "label": "Google Calendar",
        "app": "google_calendar",
        "auth": "oauth",
        "live": True,
        "note": "AP OAuth (google-calendar piece): new_event · new_or_updated_event · event_ends",
    },
    {
        "name": "pinterest",
        "label": "Pinterest",
        "app": "pinterest",
        "auth": "oauth",
        "live": True,
        "note": "AP OAuth (pinterest piece): new_pin · new_board · new_follower",
    },
    {
        "name": "youtube",
        "label": "YouTube",
        "app": "youtube",
        "auth": "none",
        "live": True,
        "note": "AP youtube piece — new_video from a PUBLIC channel feed (no OAuth, just the handle)",
    },
    {
        "name": "rss",
        "label": "RSS / Atom",
        "app": "rss",
        "auth": "none",
        "live": True,
        "note": "AP rss piece — new_item from any PUBLIC feed URL (no OAuth)",
    },
    {
        "name": "outlook",
        "label": "Outlook",
        "app": "microsoft-outlook",
        "auth": "oauth",
        "live": False,
        "note": "planned — M365 / Graph",
    },
]


def _telegram_backend() -> tuple[str, str]:
    """(backend, note) for Telegram, read from the environment rather than assumed.

    Telegram is the one channel with two transports, and the report used to claim the AP webhook
    unconditionally. On a deployment running the default (direct long-poll) with AP unreachable,
    that told an operator their working channel was down — and pointed them at setting up
    Activepieces to fix a problem they did not have.
    """
    if os.environ.get("EVENTS_TELEGRAM_BACKEND", "direct").split(" #", 1)[0].strip() == "ap":
        return "ap", "two-way via the AP Telegram piece (webhook)"
    return "direct", "two-way via direct long-poll (getUpdates/sendMessage) — no AP, no public URL"


def channels_status() -> list[dict]:
    """Each channel + whether its token is present (→ it can actually deliver)."""
    out = []
    for c in CHANNELS:
        if c["env"] is None:
            status = "connected"
        else:
            status = "connected" if os.environ.get(c["env"]) else "not_configured"
        out.append(
            {
                "name": c["name"],
                "label": c["label"],
                "kind": "channel",
                "direction": "converse",
                "status": status,
                "configured_via": c["env"] or "built-in",
                "live": c["live"],
                # A None backend/note means "resolve it now" — only Telegram, which has two
                # transports and must report the one actually configured.
                "backend": c.get("backend") or _telegram_backend()[0],
                "note": c["note"] or _telegram_backend()[1],
            }
        )
    return out


def _connected_apps(connections: list[dict]) -> set[str]:
    """The set of integration ``app`` keys that have at least one AP connection."""
    ids = " ".join((x.get("externalId") or "") for x in (connections or [])).lower()
    return {i["app"] for i in INTEGRATIONS if i["app"].split("-")[0] in ids}


def integrations_status(
    connections: list[dict] | None, *, ap_configured: bool, ap_connect_url: str | None = None
) -> list[dict]:
    """Each integration + connection status, derived from live AP connections (AP owns creds).

    ``connections`` is the caller-scoped ``engine.list_connections(...)`` result (may be None if
    AP is down/off). ``ap_connect_url`` lets the UI deep-link to AP's connect screen.
    """
    connected = _connected_apps(connections) if connections is not None else set()
    out = []
    for i in INTEGRATIONS:
        if i.get("auth") == "none":
            # a public-feed piece (youtube/rss) needs NO connection — it's ready as soon as AP is up
            status = "ready" if ap_configured else "ap_not_configured"
        elif not ap_configured:
            status = "ap_not_configured"
        elif connections is None:
            status = "unknown"
        elif i["app"] in connected:
            status = "connected"
        else:
            status = "not_connected"
        # `auth` decides whether the Studio opens an OAuth consent window or prompts for a pasted
        # token, so it MUST agree with oauth.PROVIDERS — the registry the connect endpoints obey.
        # It didn't: this table still said github was "token" after the provider became OAuth, so the
        # UI prompted for a PAT that the endpoint then rejected with a 400. Derive it, don't copy it.
        out.append(
            {
                "name": i["name"],
                "label": i["label"],
                "kind": "integration",
                "direction": "watch/act",
                "auth": _auth_kind(i["app"], i["auth"]),
                "status": status,
                # 'ready' (public-feed pieces) counts as good-to-go — nothing to connect
                "connected": status in ("connected", "ready"),
                "needs_connection": i.get("auth") != "none",
                "live": i["live"],
                "note": i["note"],
                "connect_url": ap_connect_url,
            }
        )
    return out


def _auth_kind(app: str, fallback: str) -> str:
    """How this app is connected, according to the provider registry (oauth | token)."""
    try:
        from . import oauth
    except ImportError:  # flat load (offline tests put the events dir on sys.path)
        import oauth
    return oauth.connect_kind(app) or fallback
