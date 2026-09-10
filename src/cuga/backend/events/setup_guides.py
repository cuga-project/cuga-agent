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
        "label": "Discord", "kind": "channel", "wiring": "direct Gateway (instant)",
        "ownership": ["tenant"], "ownership_default": "tenant",
        "creds": [{"key": "DISCORD_BOT_TOKEN", "label": "Bot Token", "required": True,
                   "where": "Discord Developer Portal → your app → Bot → Token"}],
        "steps": [
            "Create an app + bot at discord.com/developers → copy the Bot Token → DISCORD_BOT_TOKEN.",
            "Bot → Privileged Gateway Intents → enable MESSAGE CONTENT INTENT (required).",
            "Invite the bot to your server with Read/Send Messages permissions.",
            "Restart the server — the bot connects on boot. No Connect call, no arm, no public URL.",
            "Post a message in a channel the bot can see — it replies instantly, in-thread.",
            "The old AP polling path (~5 min) is still available behind EVENTS_DISCORD_BACKEND=ap.",
        ],
    },
    "github": {
        "label": "GitHub", "kind": "integration", "wiring": "AP (PUSH: PR / issue)", "connect": "oauth",
        "ownership": ["per_user", "tenant"], "ownership_default": "per_user",
        "creds": [
            {"key": "EVENTS_OAUTH_GITHUB_CLIENT_ID", "label": "OAuth App client id", "required": True,
             "where": "GitHub → Settings → Developer settings → OAuth Apps → New OAuth App"},
            {"key": "EVENTS_OAUTH_GITHUB_CLIENT_SECRET", "label": "OAuth App client secret",
             "required": True, "where": "same page → Generate a new client secret"},
        ],
        "steps": [
            "Create an OAuth App: GitHub → Settings → Developer settings → OAuth Apps → New OAuth App.",
            "Authorization callback URL must be EXACTLY "
            "<EVENTS_PUBLIC_URL>/api/events/connect/github/callback",
            "Put the client id + secret in .env as EVENTS_OAUTH_GITHUB_CLIENT_ID / "
            "_CLIENT_SECRET [TENANT], then `make reload`.",
            "Each user connects their own GitHub: open "
            "<EVENTS_PUBLIC_URL>/api/events/connect/github (or Integrations → GitHub → Connect) and "
            "approve. Requested scopes: repo + admin:repo_hook.",
            "Ask the concierge: 'when a PR opens on <owner/repo>, summarize it and message me' "
            "(name the repo — the PR trigger needs it).",
        ],
        "note": "GitHub is OAuth, NOT a pasted PAT. @activepieces/piece-github accepts only "
                "OAUTH2/CUSTOM_AUTH; a PAT stored as SECRET_TEXT is kept by AP and is unusable by the "
                "piece, which then fails with '401 Bad credentials' that reads like a scope problem. "
                "admin:repo_hook is what lets the PR trigger create the repository webhook.",
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
        "label": "Box", "kind": "integration", "connect": "oauth",
        "wiring": "AP (OAuth) PUSH new_file — DEFAULT · direct token poll behind EVENTS_BOX_BACKEND=direct",
        "ownership": ["per_user"], "ownership_default": "per_user",
        "creds": [
            {"key": "EVENTS_OAUTH_BOX_CLIENT_ID", "label": "OAuth Client ID", "required": True,
             "where": "Box Developer Console → your app → Configuration"},
            {"key": "EVENTS_OAUTH_BOX_CLIENT_SECRET", "label": "OAuth Client Secret", "required": True,
             "where": "same app"},
            {"key": "BOX_DEV_TOKEN", "label": "Developer Token (direct path, optional)", "required": False,
             "where": "Box Developer Console → your app → Generate Developer Token (~60 min, no OAuth app)"},
        ],
        "steps": [
            "AP (default): create a Box OAuth 2.0 app; set EVENTS_OAUTH_BOX_CLIENT_ID/_SECRET; redirect "
            "URI = <EVENTS_PUBLIC_URL>/api/events/connect/box/callback; each user logs in via "
            "GET /api/events/connect/box. AP watches the folder (new_file) and fires /invoke.",
            "The concierge arms Box PUSH on AP (create_push_flow) — this is the default when you say "
            "'when a resume lands in my Box…'.",
            "DIRECT (opt-in): set EVENTS_BOX_BACKEND=direct + BOX_DEV_TOKEN, then poll a folder via "
            "POST /api/events/box/poll — no OAuth app, but you drive/schedule the poll yourself.",
        ],
        "note": "Integrations default to AP (OAuth token lifecycle + the piece trigger). The direct "
                "token poll stays available behind EVENTS_BOX_BACKEND=direct for a quick, AP-free test.",
    },
    "google_calendar": {
        "label": "Google Calendar", "kind": "integration", "connect": "oauth",
        "wiring": "AP (google-calendar piece) · PUSH new_event + polling",
        "ownership": ["per_user"], "ownership_default": "per_user",
        "creds": [
            {"key": "EVENTS_OAUTH_GOOGLE_CALENDAR_CLIENT_ID", "label": "OAuth Client ID",
             "required": True, "where": "Google Cloud Console → Credentials → OAuth client "
             "(you can REUSE your Gmail client — just add the calendar callback URI + scope)"},
            {"key": "EVENTS_OAUTH_GOOGLE_CALENDAR_CLIENT_SECRET", "label": "OAuth Client Secret",
             "required": True, "where": "same OAuth client"},
        ],
        "steps": [
            "Google Cloud → OAuth client: add redirect "
            "<EVENTS_PUBLIC_URL>/api/events/connect/google_calendar/callback and enable the "
            "Google Calendar API + the .../auth/calendar scope. (Reusing the Gmail client is fine.)",
            "Put client id/secret in .env as EVENTS_OAUTH_GOOGLE_CALENDAR_CLIENT_ID/_SECRET, "
            "then `make reload`.",
            "Connect from CUGA: open <EVENTS_PUBLIC_URL>/api/events/connect/google_calendar and "
            "approve. AP does the token exchange + refresh; the agent never sees the credential.",
            "Arm: 'when a new event is added to my google calendar, brief me' or 'when a meeting ends, "
            "email me the follow-ups'. Name a calendar id (or 'primary') if you have several.",
        ],
        "note": "OAuth requires the CONSENT flow (a pasted token won't work). calendar_id is a "
                "required dropdown resolved against the connection. See setup/GOOGLE_CALENDAR.md.",
    },
    "pinterest": {
        "label": "Pinterest", "kind": "integration", "connect": "oauth",
        "wiring": "AP (pinterest piece) · polling (new pin / board / follower)",
        "ownership": ["per_user"], "ownership_default": "per_user",
        "creds": [
            {"key": "EVENTS_OAUTH_PINTEREST_CLIENT_ID", "label": "App ID", "required": True,
             "where": "Pinterest Developer → your app → App ID"},
            {"key": "EVENTS_OAUTH_PINTEREST_CLIENT_SECRET", "label": "App secret secret",
             "required": True, "where": "same app"},
        ],
        "steps": [
            "Pinterest Developer (developers.pinterest.com) → create an app; add redirect "
            "<EVENTS_PUBLIC_URL>/api/events/connect/pinterest/callback; request boards:read, "
            "pins:read, user_accounts:read.",
            "Put the App ID/secret in .env as EVENTS_OAUTH_PINTEREST_CLIENT_ID/_SECRET, `make reload`.",
            "Connect from CUGA: open <EVENTS_PUBLIC_URL>/api/events/connect/pinterest and approve.",
            "Arm: 'when there's a new pin on my pinterest board, share it' (name the board id) or "
            "'when I get a new pinterest follower, thank them'.",
        ],
        "note": "OAuth consent flow. new_pin needs a board_id (required dropdown). "
                "See setup/PINTEREST.md.",
    },
    "youtube": {
        "label": "YouTube", "kind": "integration", "connect": "none",
        "wiring": "AP (youtube piece) · polling a public channel feed",
        "ownership": ["tenant"], "ownership_default": "tenant",
        "creds": [],
        "steps": [
            "No connection needed — the new-video trigger watches a PUBLIC channel feed.",
            "Arm: 'when my youtube channel posts a new video, share it' and name the channel (id, URL, "
            "or @handle).",
        ],
        "note": "The new-video trigger is RSS-backed and needs no OAuth — just the channel identifier. "
                "See setup/YOUTUBE.md.",
    },
    "rss": {
        "label": "RSS / Atom", "kind": "integration", "connect": "none",
        "wiring": "AP (rss piece) · polling any public feed URL",
        "ownership": ["tenant"], "ownership_default": "tenant",
        "creds": [],
        "steps": [
            "No connection needed — the new_item trigger watches any PUBLIC feed URL.",
            "Arm: 'when a new item appears in https://blog.example.com/rss, summarize it' — name the "
            "feed URL (required).",
        ],
        "note": "The new_item trigger is a public feed poll; no OAuth. See setup/RSS.md.",
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
