# Telegram setup (Activepieces backend — instant webhook)

Telegram runs through **Activepieces**: AP registers a Telegram webhook (`setWebhook`) so messages
arrive instantly, calls CUGA's `/invoke`, and sends the reply via the Telegram piece.

```
Telegram DM ─▶ AP telegram webhook ─▶ /invoke (concierge) ─▶ AP telegram send ─▶ reply
```

The bot token is the only credential — it covers chat **and** the one Telegram watcher trigger,
`new_channel_message` (*"when someone sends the bot a link, summarize it"*, optional **pattern**
slot). Nothing extra to grant per trigger.

Telegram is the **most battle-tested** channel (first one wired live). Its send fields are verified
against `telegram-bot@0.6.4` (`send_text_message`, `chat_id`, `message`).

## What you'll need
- The Telegram app (phone or desktop).
- Activepieces running + reachable, with a public HTTPS URL (webhooks need it). See
  [../SETUP.md](../SETUP.md) for the AP + tunnel bring-up (`scripts/ap_up.sh`).

## Steps

1. **Create the bot** — in Telegram, message **@BotFather** → `/newbot` → pick a name + a username
   (must end in `bot`, e.g. `time4fun_bot`). BotFather returns a token like `8737…:AAE…`.

2. **Add to `.env`**:
   ```
   TELEGRAM_BOT_TOKEN=8737…:AAE…
   EVENTS_TELEGRAM_BOT_USERNAME=time4fun_bot     # the @handle WITHOUT the @ — used by account-linking
   ```
   Restart the server. (Double-check the username matches the bot — a wrong handle breaks the
   `t.me/<bot>?start=<token>` deep-link used for account linking.)

3. **Bring up AP + tunnel** — `scripts/ap_up.sh` starts AP (`:8081`) behind a cloudflared tunnel and
   sets it as AP's `AP_FRONTEND_URL` (required so Telegram's `setWebhook` gets an HTTPS URL).

4. **Arm the inbound flow** (admin) — builds the AP `telegram webhook → /invoke → send` flow.
   **Easy path:** `make channels` connects + arms every channel you have a token for (idempotent —
   re-run it after any tunnel-URL change). Or do it by hand:
   ```bash
   curl -s -X POST localhost:7860/api/events/admin/channels/telegram/arm \
        -H "content-type: application/json" -H "x-user-id: admin" -d '{}'
   # → {"ok":true,"ap_flow_id":"…"}
   ```
   The bot **connection** in AP (`ea::<tenant>::<user>::telegram`) that this flow needs is
   auto-created on server startup from `TELEGRAM_BOT_TOKEN` (same "set in .env == connected" path as
   GitHub). If you added the token *after* the server booted, restart it first — otherwise the arm
   fails with `ConnectionNotFound` (see Troubleshooting). To create it without a restart:
   ```bash
   curl -s -X POST localhost:7860/api/events/connect/telegram/token \
        -H "content-type: application/json" -H "x-user-id: admin" \
        -d "{\"token\":\"$TELEGRAM_BOT_TOKEN\"}"
   # → {"ok":true,"connection":"ea::…::telegram"}   then re-run the arm above
   ```

5. **(Optional) link your account** — so the agent knows *who* you are (per-user creds, memory):
   ```bash
   curl -s -X POST localhost:7860/api/events/link/telegram \
        -H "content-type: application/json" -H "x-user-id: admin" -d '{}'
   # → {"token":"…","how":"open https://t.me/<bot>?start=<token>"}
   ```
   Open that link (or send `/start <token>` to the bot) → binds your Telegram id → your profile.

## Verify
```bash
.venv/bin/python tests/events/preflight.py telegram        # bot reachable (getMe)
```
Then DM the bot any question → instant reply with a metadata footer. Grep one `trace_id` across the
CUGA + AP logs to watch the whole round-trip.

## Troubleshooting
- **`setWebhook` fails "HTTPS URL must be provided"** — AP's `AP_FRONTEND_URL` is `http://…` or
  `localhost`. Use the tunnel URL (`ap_up.sh` sets it). Webhooks require public HTTPS.
- **No reply** — flow not armed, or AP can't reach CUGA's `/invoke`. `HOST_CALLBACK_URL` must resolve
  from inside the AP container (podman: `http://host.containers.internal:7860/invoke`).
- **Wrong "who am I"** — link your account (step 5); unlinked messages fall back to header identity.
- **Arm fails `ConnectionNotFound: connection (ea::…::telegram) not found`** — the bot connection
  doesn't exist in AP yet. It auto-creates on startup from `TELEGRAM_BOT_TOKEN`, so the usual cause
  is the token was added *after* boot: **restart the server**, or create it live with
  `POST /api/events/connect/telegram/token` (step 4), then re-arm. The flow shows **DISABLED** in AP
  until the connection exists; once it does, arming flips it to **ENABLED**.
