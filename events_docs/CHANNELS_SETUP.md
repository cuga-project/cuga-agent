# Channels & Box setup (Stage 2) — what to procure + how

Stage 2 wires Telegram/Discord/Slack **channels** + the **Box** resume watcher — **all via
Activepieces** (no channel/integration API code in CUGA; AP does the receiving + sending). CUGA
only sees a normalized `/invoke` envelope and returns an answer. This is the setup checklist.

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
- **Box** (integration) — ⚠️ **needs a Box Business/Enterprise account.** On a **free/personal**
  Box account the Developer Console **blocks saving OAuth app config** ("some changes on
  configuration page cannot be saved") — redirect URIs on Custom Apps are a paid-tier feature. So
  Box is on the backlog; use **GitHub** above for the PUSH/watcher demo, or ask me to **simulate**
  the Box new-file event (real `resume_judge` + router + delivery, no live Box). When you have a
  business Box account: **connect it IN CUGA** (Studio → Integrations → Connect Box) — CUGA hosts
  the OAuth and **passes the token to AP**; app type must be **OAuth 2.0 (User Authentication)**,
  redirect URI = CUGA's callback, `.env` `EVENTS_OAUTH_BOX_CLIENT_ID/_SECRET` + `EVENTS_PUBLIC_URL`.
  - Box Developer Console → **Create New App → Custom App → OAuth 2.0 (User Authentication /
    3-legged)** — this is the **non-enterprise fix**. Do **NOT** pick *Server Authentication (JWT)*
    or *Client Credentials* — those are **enterprise only** (need Box Admin-Console approval),
    which is what fails on a free account. (A Box **Developer Token** is a 60-min test token that
    expires — not for the watcher.)
  - Copy **Client ID + Secret** → `.env`: `EVENTS_OAUTH_BOX_CLIENT_ID` / `EVENTS_OAUTH_BOX_CLIENT_SECRET`.
  - Set the app's **Redirect URI** to **CUGA's** callback: `http://localhost:8100/api/events/connect/box/callback`.
  - Then in the Studio you click **Connect Box** → approve your Box → CUGA exchanges the code and
    creates the AP Box connection (`ensure_oauth_connection`). The New-File trigger watches a
    folder **you own** (fine on a free account).

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
- **Slack** only: app → *Event Subscriptions* → Request URL = the AP webhook URL → subscribe to
  `message.channels` + `app_mention`.
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
the whole round-trip. **Only the Telegram round-trip is fully live-verified**; Discord/Slack are
wired from AP piece metadata but not yet proven end-to-end.

## Verified channel send actions (from live AP piece metadata, 2026-07-03)
One declarative row per channel in `flows.CHANNELS` (piece `telegram-bot@0.6.4` · `discord@0.5.3` ·
`slack@0.17.2`):

| Channel | Send action | Target arg | Text arg | Notes |
|---|---|---|---|---|
| **telegram** | `send_text_message` | `chat_id` | `message` | **fully round-trip verified live** |
| **discord** | `sendMessageWithBot` | `channel_id` | `message` | wired from AP metadata; **not yet live round-trip-tested** |
| **slack** | `send_channel_message` | `channel` | `text` | requires constant `sendAsBot=true`; **not yet live round-trip-tested** |

## What's code vs config (the "AP owns it" boundary)
- **Config (CUGA):** `flows.CHANNELS` / `flows.SOURCE_TRIGGER` — one declarative row per connector
  (which AP piece + trigger + message/sender fields + send action). Adding a channel = one row.
- **Execution (AP):** the trigger reads the message, the send action replies, connections hold the
  tokens. CUGA has **no** Telegram/Discord/Slack/Box API client.
- **Identity:** the sender's native id rides in the `/invoke` `thread_id` (`gw:<channel>:<native>`);
  CUGA resolves it → user (decision 0007). Account-linking via `/start <token>`.
