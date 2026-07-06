# Channels & Box setup (Stage 2) — what to procure + how

Stage 2 wires Telegram/Discord/Slack **channels** + the **Box** resume watcher. **Telegram and
Discord run via Activepieces** (AP does the receiving + sending); **Slack and Box now default to a
*direct* backend** (CUGA talks to the vendor API with a token — no AP). CUGA sees a normalized
`/invoke` envelope either way. This is the combined checklist; for a focused per-integration
walkthrough see **[setup/](setup/)** ([Slack](setup/SLACK.md) · [Discord](setup/DISCORD.md) ·
[Telegram](setup/TELEGRAM.md) · [Box](setup/BOX.md)).

## 1) Bots (get the tokens → `.env`)
- **Telegram** — message **@BotFather** → `/newbot`. → `TELEGRAM_BOT_TOKEN`,
  `EVENTS_TELEGRAM_BOT_USERNAME` (the @handle, for the `/start` deep-link).
- **Discord** — developers portal → *Bot* → **Reset Token** → `DISCORD_BOT_TOKEN`; enable
  **Message Content Intent**; invite the bot to a test server (OAuth2 URL, scope `bot` + Send
  Messages).
- **Slack** — api.slack.com/apps → *From scratch*; bot scopes `chat:write, channels:history,
  channels:read, app_mentions:read` → **Install to Workspace** → `SLACK_BOT_TOKEN` (`xoxb-…`).
- **GitHub** (the frictionless PUSH source — recommended over Box for the watcher demo) —
  **github.com/settings/tokens** → *Generate new token (classic)* → scopes `repo` + `read:org` →
  paste in CUGA **Integrations → GitHub → Connect** (token, no OAuth/redirect). Proves the same
  integration mechanics (New-PR/New-Issue → /invoke → router → deliver) with zero account-tier
  friction. `.env`: nothing needed (token pasted in the UI).
- **Box** (integration) — **direct backend is the default and free-account-friendly.** Grab a Box
  **Developer Token** (Box dev console → your app → *Generate Developer Token*, ~60 min, no OAuth app
  / redirect URI) → `.env` `BOX_DEV_TOKEN`. The watcher polls a folder via
  `POST /api/events/box/poll`. Full walkthrough + verify: **[setup/BOX.md](setup/BOX.md)**. No
  Business account, no OAuth consent needed.
  - **AP path (optional, secondary):** the AP `new_file` **webhook** trigger needs a paid Box app
    that can save a Redirect URI + `manage_webhook`, plus the OAuth consent flow (AP refuses a
    pre-obtained token — it does the code-exchange itself). Only bother with this if you specifically
    want AP to own the Box connection. Set `EVENTS_OAUTH_BOX_CLIENT_ID/_SECRET` + `EVENTS_PUBLIC_URL`,
    redirect URI = `<EVENTS_PUBLIC_URL>/api/events/connect/box/callback`, then Studio → Integrations →
    Connect Box → approve → `ensure_oauth_connection` creates the AP connection.
    (The free-account block on saving OAuth app config is exactly why direct is the default.)

## 2) Tunnel + AP (the real-inbound blocker)
Telegram/Slack **push events to AP over the internet**, so AP needs a public URL (Discord uses a
websocket gateway, usually no public needed). **Use `scripts/ap_up.sh`** — it starts a cloudflared
tunnel and sets it as AP's `AP_FRONTEND_URL` for you (AP=**8081**):
```bash
scripts/ap_up.sh          # cloudflared tunnel + AP (pg+redis, persistent volume) + admin sign-up
```
- The script sets AP's **frontend/public URL** to the tunnel automatically; confirm the
  **Telegram, Discord, Slack, HTTP, Schedule, Box** pieces are installed.
- ⚠️ **Do NOT set `AP_WORKER_TOKEN`.** AP 0.82's docker-entrypoint mints it as a JWT signed with
  `AP_JWT_SECRET` when unset; a raw random string makes the worker crash-loop on Socket.IO
  "Authentication error" and every channel/schedule flow publish hangs ~30s. `.ap.env` should hold
  **only** `AP_ENCRYPTION_KEY` + `AP_JWT_SECRET` (`ap_up.sh` is authoritative + gates on worker health).
- **Slack** ships a **direct backend** (default) that bypasses AP — see the box below. Its Event
  Subscriptions Request URL points at **CUGA** (`<EVENTS_PUBLIC_URL>/api/events/slack/events`), not
  the AP webhook. Only set the AP webhook URL if you flip `EVENTS_SLACK_BACKEND=ap`.

> ### Slack: two backends — `direct` (default) vs `ap` (behind a flag)
> AP 0.82 + slack-piece 0.17.2's `new-message` APP_WEBHOOK trigger emits `[]` even for a clean event
> (events reach AP, EXECUTE_WEBHOOK jobs run, but no flow-run fires), and AP's OAuth2 schema refuses a
> pre-obtained bot token. So Slack now defaults to a CUGA-native path:
>
> | | Inbound | Outbound | Auth |
> |---|---|---|---|
> | **`direct`** (default) | Slack Events API → `POST /api/events/slack/events` (CUGA) | `slack_direct.send_message` → `chat.postMessage` | `SLACK_BOT_TOKEN` (+ `SLACK_SIGNING_SECRET`) |
> | **`ap`** (`EVENTS_SLACK_BACKEND=ap`) | AP slack `new-message` APP_WEBHOOK | AP `send_channel_message` | AP OAuth2 (code-exchange) |
>
> **Setup (direct):** Slack app → *Event Subscriptions* → Request URL = `<EVENTS_PUBLIC_URL>/api/events/slack/events`,
> subscribe bot event `message.channels`; bot scopes `chat:write, channels:history, channels:read`;
> invite the bot to the channel. Arming returns the `events_url` to paste (no AP flow):
> `POST /api/events/admin/channels/slack/arm`.
>
> **Direct-channel delivery** (a *scheduled* flow delivering to Slack): the AP schedule is just the
> clock — it POSTs to `/invoke` with `deliver:true` + a channel `source`; CUGA sends the answer itself
> via `delivery.send_direct` (no AP send-step). The `direct|ap` split lives in `delivery.channel_backend`
> (override per channel with `EVENTS_<CH>_BACKEND`).
- `.env`: `AP_BASE_URL` (CUGA→AP, e.g. `http://localhost:8081`), `HOST_CALLBACK_URL` must let AP
  reach CUGA's `/invoke` on **8100**. Under podman use the host alias:
  `http://host.containers.internal:8100/invoke` (Docker: `http://host.docker.internal:8100/invoke`).

## 3) Confirm back (no secret values)
✅ 3 bot tokens + `EVENTS_TELEGRAM_BOT_USERNAME` in `.env` · ✅ Discord bot in a server +
Message-Content-Intent on · ✅ Box app created · ✅ AP tunnel URL set as AP's `AP_FRONTEND_URL`
(via `ap_up.sh`) · ✅ `HOST_CALLBACK_URL` reaches CUGA on :8100. Paste me the **AP tunnel URL** +
a **Discord**/**Slack** test channel.

## 4) Run the live arming + round-trip
```bash
# seeded server + registry (as before), then:
GATEWAY_TOKEN=<from .env> EVENTS_SERVER_URL=http://localhost:8100 \
  .venv/bin/python tests/events/live_stage2_channels.py     # arms inbound flows + Box watcher
```
Then **you** send a real message to the bot; AP → `/invoke` → the concierge resolves you (via the
channel link) → answers → AP sends it back. Grep one `trace_id` across the CUGA + AP logs to watch
the whole round-trip. **Telegram (AP), Discord (AP), and Slack (direct) round-trips are all
live-verified**; Slack's direct path (Events API → `/api/events/slack/events` → `chat.postMessage`)
is the default (AP Slack parked behind `EVENTS_SLACK_BACKEND=ap`).

## Verified channel send actions (from live AP piece metadata, 2026-07-03)
One declarative row per channel in `flows.CHANNELS` (piece `telegram-bot@0.6.4` · `discord@0.5.3` ·
`slack@0.17.2`):

| Channel | Send action | Target arg | Text arg | Notes |
|---|---|---|---|---|
| **telegram** | `send_text_message` | `chat_id` | `message` | **fully round-trip verified live** |
| **discord** | `sendMessageWithBot` | `channel_id` | `message` | **live round-trip verified** (AP polling, ~5 min) |
| **slack** (`ap` backend) | `send_channel_message` | `channel` | `text` | requires `sendAsBot=true`; **parked** — AP trigger emits `[]`, so `direct` is the default |
| **slack** (`direct`, default) | `chat.postMessage` | `channel` | `text` | CUGA-native (`slack_direct`), bot token; **inbound round-trip verified** (Events API → reply) |

## What's code vs config (the "AP owns it" boundary)
- **Config (CUGA):** `flows.CHANNELS` / `flows.SOURCE_TRIGGER` — one declarative row per connector
  (which AP piece + trigger + message/sender fields + send action). Adding a channel = one row.
- **Execution (AP):** the trigger reads the message, the send action replies, connections hold the
  tokens. CUGA has **no** Telegram/Discord/Slack/Box API client.
- **Identity:** the sender's native id rides in the `/invoke` `thread_id` (`gw:<channel>:<native>`);
  CUGA resolves it → user (decision 0007). Account-linking via `/start <token>`.
