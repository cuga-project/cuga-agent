"""Connector descriptors + live status — the source of truth the Studio UI renders.

The UI is **dumb**: it does not know which channels/integrations exist or how to tell if one is
connected. It asks these endpoints and paints the result. So all of that knowledge lives here,
server-side, next to the code that actually uses it (Principle: UI in sync with functionality).

Two views of one ``connector`` idea (DESIGN §2):
  - **Channel**   — converse-with (web/telegram/discord/slack); a human on the other end.
  - **Integration** — watch/act-on (gmail/box/github/outlook); an app on the other end.

Status is derived from **real state**, never hardcoded "connected":
  - channels  → presence of the bot token in env (the thing that actually enables inbound/outbound).
  - integrations → an AP **connection** whose externalId names the app (AP owns creds, §10).
So what the UI shows is exactly what the backend can do right now. Phase-3 capabilities
(inbound routing, OAuth connect) are labelled ``planned`` honestly rather than faked.
"""

from __future__ import annotations

import os

# --- descriptors ---------------------------------------------------------------
# ``env`` = the env var whose presence means "this channel can send/receive".
# ``live`` = wired end-to-end today (Phase 1/2). ``planned`` marks Phase-3 legs so the UI
# can show them without pretending they work.
CHANNELS = [
    {"name": "web", "label": "Web chat", "env": None, "live": True,
     "note": "the built-in /chat + /api/concierge surface — always on"},
    {"name": "telegram", "label": "Telegram", "env": "TELEGRAM_BOT_TOKEN", "live": True,
     "note": "outbound delivery live; two-way inbound routing is Phase 3"},
    {"name": "discord", "label": "Discord", "env": "DISCORD_BOT_TOKEN", "live": True,
     "note": "outbound delivery live; two-way inbound routing is Phase 3"},
    {"name": "slack", "label": "Slack", "env": "SLACK_BOT_TOKEN", "live": False,
     "note": "planned — AP Slack piece"},
]

# ``app`` = the AP piece / substring we match a connection's externalId against.
# ``auth`` = how a connection is created: ``oauth`` (authorized in AP's connect UI) vs
# ``token`` (a PAT/secret that can be created via the API).
INTEGRATIONS = [
    {"name": "gmail", "label": "Gmail", "app": "gmail", "auth": "oauth", "live": False,
     "note": "connect in Activepieces; PUSH (new-email) triggers are Phase 3"},
    {"name": "box", "label": "Box", "app": "box", "auth": "oauth", "live": False,
     "note": "connect in Activepieces; New-File (resume watcher) trigger is Phase 3"},
    {"name": "github", "label": "GitHub", "app": "github", "auth": "token", "live": False,
     "note": "PAT connection via API; New-PR trigger is Phase 3"},
    {"name": "outlook", "label": "Outlook", "app": "microsoft-outlook", "auth": "oauth",
     "live": False, "note": "planned — M365 / Graph"},
]


def channels_status() -> list[dict]:
    """Each channel + whether its token is present (→ it can actually deliver)."""
    out = []
    for c in CHANNELS:
        if c["env"] is None:
            status = "connected"
        else:
            status = "connected" if os.environ.get(c["env"]) else "not_configured"
        out.append({"name": c["name"], "label": c["label"], "kind": "channel",
                    "direction": "converse", "status": status,
                    "configured_via": c["env"] or "built-in", "live": c["live"],
                    "note": c["note"]})
    return out


def _connected_apps(connections: list[dict]) -> set[str]:
    """The set of integration ``app`` keys that have at least one AP connection."""
    ids = " ".join((x.get("externalId") or "") for x in (connections or [])).lower()
    return {i["app"] for i in INTEGRATIONS if i["app"].split("-")[0] in ids}


def integrations_status(connections: list[dict] | None, *, ap_configured: bool,
                        ap_connect_url: str | None = None) -> list[dict]:
    """Each integration + connection status, derived from live AP connections (AP owns creds).

    ``connections`` is the caller-scoped ``engine.list_connections(...)`` result (may be None if
    AP is down/off). ``ap_connect_url`` lets the UI deep-link to AP's connect screen.
    """
    connected = _connected_apps(connections) if connections is not None else set()
    out = []
    for i in INTEGRATIONS:
        if not ap_configured:
            status = "ap_not_configured"
        elif connections is None:
            status = "unknown"
        elif i["app"] in connected:
            status = "connected"
        else:
            status = "not_connected"
        out.append({"name": i["name"], "label": i["label"], "kind": "integration",
                    "direction": "watch/act", "auth": i["auth"], "status": status,
                    "live": i["live"], "note": i["note"],
                    "connect_url": ap_connect_url})
    return out
