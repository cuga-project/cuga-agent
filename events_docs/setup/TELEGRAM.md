# Telegram setup (direct backend — the default, no AP, no tunnel)

Telegram talks to CUGA **directly** over the Bot API using **long-polling** (`getUpdates`) — an
**outbound** HTTPS call the bot makes in a loop, so it needs **no Activepieces, no public URL, and no
tunnel** (the same "go direct" move as Discord's Gateway). The poller starts on server start and
sends replies with `sendMessage`; there's nothing to arm.

```
Telegram DM ─▶ getUpdates loop (outbound) ─▶ /invoke (concierge) ─▶ sendMessage ─▶ reply
```

The bot token is the only credential. A **private chat** is inherently addressed to the bot, so DMs
always reach it; in groups you can gate on an @mention (see below).

**Trigger:** `new_channel_message` — every incoming Telegram message. That's the one event this
backend surfaces (it drives chat, and any `new_channel_message` watcher armed on `telegram`).

> The old **AP webhook** path (AP registers a Telegram `setWebhook` and runs the flow) is still
> available behind `EVENTS_TELEGRAM_BACKEND=ap` — it needs AP + a public HTTPS URL. Prefer the direct
> default: instant, zero infra. Discord/Slack have the same `EVENTS_{DISCORD,SLACK}_BACKEND` pair,
> also `direct` by default.

## What you'll need
- The Telegram app (phone or desktop).
- Just the bot token — **no Activepieces, no public URL** for the direct backend.

## Steps

1. **Create the bot** — in Telegram, message **@BotFather** → `/newbot` → pick a name + a username
   (must end in `bot`, e.g. `time4fun_bot`). BotFather returns a token like `8737…:AAE…`.

2. **Add to `.env`**:
   ```
   TELEGRAM_BOT_TOKEN=8737…:AAE…
   EVENTS_TELEGRAM_BOT_USERNAME=time4fun_bot     # the @handle WITHOUT the @ — enables the group
                                                 # @mention gate + account-linking deep link
   ```
   Then **restart the server**. The direct backend just needs the token — the poller starts on boot
   and clears any stale webhook automatically. `EVENTS_TELEGRAM_BOT_USERNAME` is optional (CUGA
   fills it from `getMe` if unset), but a wrong handle breaks the `t.me/<bot>?start=<token>`
   account-linking deep link.

3. **Only answer when @mentioned (optional)** — by default every message with text reaches the
   concierge. To make a **group** message reach chat only when it @mentions the bot (a private chat
   always passes), set:
   ```
   EVENTS_TELEGRAM_CHAT=mention      # then: make reload
   ```

4. **(Optional) link your account** — so the agent knows *who* you are (per-user creds, memory):
   ```bash
   curl -s -X POST localhost:8100/api/events/link/telegram \
        -H "content-type: application/json" -H "x-user-id: admin" -d '{}'
   # → {"token":"…","how":"open https://t.me/<bot>?start=<token>"}
   ```
   Open that link (or send `/start <token>` to the bot) → binds your Telegram id → your profile.
   Unlinked messages fall back to header identity.

## Verify
```bash
.venv/bin/python tests/events/preflight.py telegram        # bot reachable (getMe)
```
Then DM the bot any question → an **instant** reply with a metadata footer. The server log shows
`telegram long-poll started as @<bot>` on startup.

## Troubleshooting
- **No reply** — `TELEGRAM_BOT_TOKEN` missing/wrong, or the server isn't running (the poller posts
  back via CUGA's local `/invoke`). `preflight.py telegram` confirms the token with `getMe`.
- **Group messages ignored** — that's `EVENTS_TELEGRAM_CHAT=mention` doing its job; @mention the bot,
  or unset it. Private chats (DMs) always pass regardless.
- **Wrong "who am I"** — link your account (step 4); unlinked messages fall back to header identity.

## Optional: AP backend (`EVENTS_TELEGRAM_BACKEND=ap`)

The legacy path where **Activepieces** registers a Telegram webhook (`setWebhook`) and runs a
`telegram webhook → /invoke → send` flow. Choose it only if you specifically want Telegram armed as
an AP flow alongside the SaaS integrations. It needs **AP running + a public HTTPS URL** (webhooks
require it). Send fields are verified against `telegram-bot@0.6.4` (`send_text_message`, `chat_id`,
`message`).

1. Set `EVENTS_TELEGRAM_BACKEND=ap`, and bring up AP + a tunnel so `setWebhook` has an HTTPS URL —
   see [../SETUP.md](../SETUP.md) for the AP + tunnel bring-up.
2. **Arm the inbound flow** — `make channels` connects + arms every channel you have a token for
   (idempotent). Or by hand:
   ```bash
   curl -s -X POST localhost:8100/api/events/admin/channels/telegram/arm \
        -H "content-type: application/json" -H "x-user-id: admin" -d '{}'
   ```
   The bot **connection** in AP (`ea::<tenant>::<user>::telegram`) is auto-created on server startup
   from `TELEGRAM_BOT_TOKEN`. If you added the token *after* boot, restart first, or create it live:
   ```bash
   curl -s -X POST localhost:8100/api/events/connect/telegram/token \
        -H "content-type: application/json" -H "x-user-id: admin" \
        -d "{\"token\":\"$TELEGRAM_BOT_TOKEN\"}"
   ```

### AP-backend troubleshooting
- **`setWebhook` fails "HTTPS URL must be provided"** — AP's `AP_FRONTEND_URL` is `http://…` or
  `localhost`. Use the tunnel URL. Webhooks require public HTTPS. (The direct backend has no such
  requirement — it's outbound.)
- **Arm fails `ConnectionNotFound: connection (ea::…::telegram) not found`** — the bot connection
  doesn't exist in AP yet. It auto-creates on startup from `TELEGRAM_BOT_TOKEN`; the usual cause is
  the token was added *after* boot: **restart the server**, or create it live with
  `POST /api/events/connect/telegram/token` (above), then re-arm.
