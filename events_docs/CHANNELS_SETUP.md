# Channels & Box setup (Stage 2) — what to procure + how

Stage 2 wires Telegram/Discord/Slack **channels** + the **Box** resume watcher. **Telegram runs via
Activepieces** (AP does the receiving + sending); **Slack, Discord, and Box now default to *direct*
backends** (CUGA talks to the vendor API directly — no AP; Discord via a Gateway WebSocket, Slack via
the Events API, Box via a token poll). Each AP path stays behind `EVENTS_<CH>_BACKEND=ap`. CUGA sees
a normalized `/invoke` envelope either way. This is the combined checklist; for a focused per-integration
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
- **Box** (integration) — **AP is the default** (integrations run on AP: OAuth token lifecycle + the
  `new_file` trigger). Create a Box **OAuth 2.0 app** → `.env` `EVENTS_OAUTH_BOX_CLIENT_ID/_SECRET` +
  `EVENTS_PUBLIC_URL`; redirect URI = `<EVENTS_PUBLIC_URL>/api/events/connect/box/callback`; each user
  logs in via `GET /api/events/connect/box`. The concierge arms Box PUSH on AP (`create_push_flow`).
  Full walkthrough: **[setup/BOX.md](setup/BOX.md)**.
  - **Direct poll (opt-in):** set `EVENTS_BOX_BACKEND=direct` + `BOX_DEV_TOKEN` (dev console, ~60 min,
    no OAuth app) and drive `POST /api/events/box/poll` yourself — a quick, AP-free test path. Kept
    behind the flag, symmetric with Slack's parked AP path.

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
the whole round-trip. **Telegram (AP) + Slack (direct) round-trips are live-verified; Discord (direct
Gateway) gateway-connect is verified, full round-trip pending a live message.** The direct backends
(Slack Events API, Discord Gateway) are the defaults; each AP path is parked behind
`EVENTS_<CH>_BACKEND=ap`.

## Verified channel send actions (from live AP piece metadata, 2026-07-03)
One declarative row per channel in `flows.CHANNELS` (piece `telegram-bot@0.6.4` · `discord@0.5.3` ·
`slack@0.17.2`):

| Channel | Send action | Target arg | Text arg | Notes |
|---|---|---|---|---|
| **telegram** | `send_text_message` | `chat_id` | `message` | **fully round-trip verified live** |
| **discord** (`direct`, default) | REST create-message | `channel_id` | `content` | **Gateway WebSocket** (instant, no public URL); native author id; gateway connects verified |
| **discord** (`ap` backend) | `sendMessageWithBot` | `channel_id` | `message` | AP polling (~5 min) behind `EVENTS_DISCORD_BACKEND=ap` |
| **slack** (`ap` backend) | `send_channel_message` | `channel` | `text` | requires `sendAsBot=true`; **parked** — AP trigger emits `[]`, so `direct` is the default |
| **slack** (`direct`, default) | `chat.postMessage` | `channel` | `text` | CUGA-native (`slack_direct`), bot token; **inbound round-trip verified** (Events API → reply) |

## What's code vs config (the "AP owns it" boundary)
- **Config (CUGA):** `flows.CHANNELS` / `flows.SOURCE_TRIGGER` — one declarative row per connector
  (which AP piece + trigger + message/sender fields + send action). Adding a channel = one row.
- **Execution (AP):** the trigger reads the message, the send action replies, connections hold the
  tokens. CUGA has **no** Telegram/Discord/Slack/Box API client.
- **Identity:** the sender's native id rides in the `/invoke` `thread_id` (`gw:<channel>:<native>`);
  CUGA resolves it → user (decision 0007). Account-linking via `/start <token>`.
