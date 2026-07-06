"""Per-connector setup guides — the "how do I connect this?" content the Studio's Integrations /
Channels tabs render. Data-only (no logic) so the UI stays dumb.

Each guide: what credentials it needs (env keys), how ownership works (per-user vs per-tenant),
the concrete step list, and how it's wired (direct vs AP). ``ownership`` options:
  • ``tenant``   — one shared credential for everyone in the tenant (a shared bot / shared app).
  • ``per_user`` — each user connects their OWN account (per-user OAuth login).
"""
from __future__ import annotations

# fmt: off
GUIDES: dict[str, dict] = {
    "slack": {
        "label": "Slack", "kind": "channel", "wiring": "direct (default) · AP behind EVENTS_SLACK_BACKEND=ap",
        "ownership": ["tenant"], "ownership_default": "tenant",
        "creds": [
            {"key": "SLACK_BOT_TOKEN", "label": "Bot Token (xoxb-…)", "required": True,
             "where": "Slack app → OAuth & Permissions → Bot User OAuth Token"},
            {"key": "SLACK_SIGNING_SECRET", "label": "Signing Secret", "required": False,
             "where": "Slack app → Basic Information → App Credentials (recommended — verifies requests)"},
        ],
        "steps": [
            "Create a Slack app at api.slack.com/apps (from scratch).",
            "OAuth & Permissions → Bot Token Scopes: chat:write, channels:history, channels:read.",
            "Install to Workspace → copy the Bot User OAuth Token → SLACK_BOT_TOKEN in .env.",
            "Event Subscriptions → Request URL = <EVENTS_PUBLIC_URL>/api/events/slack/events → wait for 'Verified'.",
            "Subscribe to bot event: message.channels (+ message.groups for private).",
            "Invite the bot to your channel, then post a message — it replies instantly.",
        ],
    },
    "telegram": {
        "label": "Telegram", "kind": "channel", "wiring": "AP (webhook, instant)",
        "ownership": ["tenant"], "ownership_default": "tenant",
        "creds": [{"key": "TELEGRAM_BOT_TOKEN", "label": "Bot Token", "required": True,
                   "where": "@BotFather → /newbot"}],
        "steps": [
            "Create a bot with @BotFather → copy the token → TELEGRAM_BOT_TOKEN in .env.",
            "Connect: POST /api/events/connect/telegram/token (or the Studio 'Connect').",
            "Arm: POST /api/events/admin/channels/telegram/arm (registers the webhook).",
            "Message the bot — it replies instantly.",
        ],
    },
    "discord": {
        "label": "Discord", "kind": "channel", "wiring": "AP (polling, ~5 min)",
        "ownership": ["tenant"], "ownership_default": "tenant",
        "creds": [{"key": "DISCORD_BOT_TOKEN", "label": "Bot Token", "required": True,
                   "where": "Discord Developer Portal → your app → Bot → Token"}],
        "steps": [
            "Create an app + bot at discord.com/developers → copy the Bot Token → DISCORD_BOT_TOKEN.",
            "Bot → Privileged Gateway Intents → enable MESSAGE CONTENT INTENT (required).",
            "Invite the bot to your server with Read/Send Messages permissions.",
            "Connect: POST /api/events/connect/discord/token. Arm with the channel id to watch.",
            "Post a message — replies arrive on the next poll (~up to 5 min).",
        ],
    },
    "github": {
        "label": "GitHub", "kind": "integration", "wiring": "AP (PUSH: PR / issue)",
        "ownership": ["per_user", "tenant"], "ownership_default": "per_user",
        "creds": [{"key": "GITHUB_TOKEN", "label": "Personal Access Token", "required": True,
                   "where": "GitHub → Settings → Developer settings → PAT (repo scope)"}],
        "steps": [
            "Create a PAT (repo scope) → connect: POST /api/events/connect/github/token.",
            "Ask the concierge: 'when a PR opens on my repo, summarize it and message me'.",
        ],
    },
    "gmail": {
        "label": "Gmail", "kind": "integration", "wiring": "AP (OAuth) · PUSH + email delivery",
        "ownership": ["per_user"], "ownership_default": "per_user",
        "creds": [
            {"key": "EVENTS_OAUTH_GMAIL_CLIENT_ID", "label": "OAuth Client ID", "required": True,
             "where": "Google Cloud Console → Credentials → OAuth client"},
            {"key": "EVENTS_OAUTH_GMAIL_CLIENT_SECRET", "label": "OAuth Client Secret", "required": True,
             "where": "same OAuth client"},
        ],
        "steps": [
            "Google Cloud → create an OAuth client; set redirect <EVENTS_PUBLIC_URL>/api/events/connect/gmail/callback.",
            "Put client id/secret in .env (or the Admin OAuth-apps panel).",
            "Each user logs in: GET /api/events/connect/gmail (consent). AP does the token exchange + refresh.",
        ],
        "note": "OAuth requires the CONSENT flow — a pasted token won't work (AP does the code exchange).",
    },
    "box": {
        "label": "Box", "kind": "integration", "connect": "token",
        "wiring": "direct poll (default, token) · AP (OAuth) PUSH new_file behind the AP path",
        "ownership": ["per_user"], "ownership_default": "per_user",
        "creds": [
            {"key": "BOX_DEV_TOKEN", "label": "Developer Token (direct path)", "required": False,
             "where": "Box Developer Console → your app → Generate Developer Token (~60 min, no OAuth app)"},
            {"key": "EVENTS_OAUTH_BOX_CLIENT_ID", "label": "OAuth Client ID (AP path)", "required": False,
             "where": "Box Developer Console → your app → Configuration"},
            {"key": "EVENTS_OAUTH_BOX_CLIENT_SECRET", "label": "OAuth Client Secret (AP path)", "required": False,
             "where": "same app"},
        ],
        "steps": [
            "DIRECT (recommended): grab a Developer Token (no OAuth app / redirect URI needed), set "
            "BOX_DEV_TOKEN, then poll a folder via POST /api/events/box/poll {folder_id, deliver_to}.",
            "The watcher fires resume_judge per new file and can deliver to a DIRECT channel (Slack) — "
            "fully AP-free. Verify with tests/events/live_box_direct_check.py.",
            "AP path (optional): a paid/dev Box app that can save a Redirect URI "
            "(<EVENTS_PUBLIC_URL>/api/events/connect/box/callback) + manage_webhook; each user logs in "
            "via GET /api/events/connect/box.",
        ],
        "note": "Direct poll takes the token directly (fast, no consent). The AP push path needs the "
                "OAuth code-exchange (AP refuses a pre-obtained dev token) + a paid Box app.",
    },
    "webhook": {
        "label": "Generic Webhook", "kind": "integration", "wiring": "AP (in) / direct (out)",
        "ownership": ["tenant"], "ownership_default": "tenant", "creds": [],
        "steps": ["No credentials — inbound is a generated URL any system can POST to; outbound "
                  "delivers to any URL you name in the request."],
    },
}
# fmt: on


def guide(app: str) -> dict | None:
    return GUIDES.get(app)


def as_list() -> list[dict]:
    return [{"app": k, **v} for k, v in GUIDES.items()]
