# Discord setup (direct Gateway backend — the default, instant)

Discord talks to CUGA **directly** over its **Gateway** (a persistent WebSocket bot) — **instant**
messages, and because the Gateway is an *outbound* connection it needs **no public URL** (unlike
Slack's Events API). The bot connects on server start; there's nothing to arm.

```
Discord message ─▶ Gateway (MESSAGE_CREATE) ─▶ /invoke (concierge) ─▶ REST create-message ─▶ reply
```

Replies go to the message's `channel_id`, so a message in a **thread** is answered **in that thread**;
memory is per channel/thread, and the message's **author** id gives per-user identity natively.

> The old **AP polling** path (`new_message` trigger, ~5 min latency) is still available behind
> `EVENTS_DISCORD_BACKEND=ap`. It watches one channel with `DedupeStrategy.TIMEBASED`. Prefer the
> direct Gateway (default) — instant, no public URL, native author id.

## What you'll need
- A Discord server where you can add a bot, with **MESSAGE CONTENT INTENT** enabled.
- Just the bot token — **no Activepieces, no public URL** for the direct backend.

## Steps

1. **Create the app + bot** — <https://discord.com/developers/applications> → **New Application** →
   **Bot** → **Add Bot**. Copy the **Bot Token**.

2. **Enable Message Content Intent** — in the **Bot** tab, turn on **MESSAGE CONTENT INTENT**
   (required to read message text).

3. **Invite the bot to your server** — **OAuth2 → URL Generator** → scopes `bot`; bot permissions
   *Read Messages/View Channels* + *Send Messages*. Open the generated URL → add to your server.

4. **Add to `.env`**:
   ```
   DISCORD_BOT_TOKEN=…
   ```
   Restart the server.

5. **Nothing to arm (direct backend).** The bot connects to the Gateway on server start. You can
   confirm the backend:
   ```bash
   curl -s -X POST localhost:7860/api/events/admin/channels/discord/arm \
        -H "content-type: application/json" -H "x-user-id: admin" -d '{}'
   # → {"ok":true,"channel":"discord","backend":"direct", ...}
   ```
   *(For the AP polling path — `EVENTS_DISCORD_BACKEND=ap` — arm with `{"channel":"<CHANNEL_ID>"}`
   instead; get the channel id via Developer Mode → right-click channel → Copy Channel ID.)*

## Verify
```bash
.venv/bin/python tests/events/preflight.py discord         # bot reachable (users/@me)
```
Then **post any message** in a channel the bot can see → an **instant** reply with a metadata footer.
The server log shows `discord gateway READY as <bot>` on startup.

## Troubleshooting
- **Gateway closes with code 4014 / no reply** — **MESSAGE CONTENT INTENT is off.** Enable it
  (Developer Portal → your app → Bot → Privileged Gateway Intents), then restart.
- **No reply** — bot not invited to the channel; `DISCORD_BOT_TOKEN` missing; or the server isn't
  running (the gateway posts back via CUGA's local `/invoke`).
- **preflight discord 403 (Cloudflare 1010)** — a bare user-agent is blocked; preflight sends a
  browser UA. If you script your own call, add `User-Agent: Mozilla/5.0`.
- **Per-user identity** — the Gateway message carries the full `author` object; CUGA forwards
  `author.id` as `source.user`, so a user who `/link`s their Discord id gets per-user creds/permissions.
- **Want the old polling behavior?** Set `EVENTS_DISCORD_BACKEND=ap` (watches one channel, ~5 min).

## Discord WATCHERS — the two triggers (optional)

Beyond the chat bot, CUGA can arm two **watchers**, both as CUGA-owned direct subscriptions on the
Gateway CUGA already holds (no Activepieces):

| Trigger (what you say) | Gateway event | Needs |
|---|---|---|
| `new_member` — *"when a new member joins the server, greet them"* | `GUILD_MEMBER_ADD` | **SERVER MEMBERS intent** (privileged — see below) |
| `new_channel_message` — *"when someone posts in #help on discord…"* | `MESSAGE_CREATE` | MESSAGE CONTENT intent (you already enabled it for chat) |

`GUILD_MEMBER_ADD` is a **privileged** Gateway intent, so it is **opt-in in two places** and requesting
it unapproved **closes the entire gateway with error 4014** (your chat bot stops working too):

1. **Discord Developer Portal** → your app → **Bot** → **Privileged Gateway Intents** → enable
   **SERVER MEMBERS INTENT** → Save.
2. **`.env`** → `EVENTS_DISCORD_MEMBERS_INTENT=1` → `make reload`.

Do them in that order. With the env flag set but the portal toggle off, the gateway will refuse to
connect. If Discord suddenly goes quiet after enabling this, that is the cause — unset the flag,
`make reload`, and the bot comes straight back.

```
/push when a new member joins the server on discord, greet them
```
