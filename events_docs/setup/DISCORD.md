# Discord setup (Activepieces backend — polling ~5 min)

Discord runs through **Activepieces**. Its `new_message` trigger is a **POLLING** trigger that
watches **one channel** (Discord has no free push webhook for reads), so replies arrive within the
poll interval (~5 min), not instantly. Fields verified against `discord@0.5.3`
(`sendMessageWithBot`, `channel_id`, `message`).

```
Discord channel msg ─▶ AP polls the channel ─▶ /invoke (concierge) ─▶ AP discord send ─▶ reply
```

Replies go to the message's `channel_id`, so a message posted **in a thread** is answered **in that
thread** automatically.

## Why polling, not instant (vs Telegram)?
Telegram/Slack push events to a webhook; Discord's read side is a gateway/poll model, and AP's piece
polls. That's why Discord uses `DedupeStrategy.TIMEBASED` — each poll only processes messages newer
than the last poll baseline (this is stored; re-processing old messages only happens if you re-arm,
which resets the baseline).

## What you'll need
- A Discord server where you can add a bot.
- Activepieces running + reachable. (Discord polling does **not** need a public URL — no webhook.)

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

5. **Get the channel id** — in Discord, enable *Developer Mode* (User Settings → Advanced), then
   right-click the channel → **Copy Channel ID**.

6. **Arm the inbound flow** — Discord's polling trigger needs the channel id to watch:
   ```bash
   curl -s -X POST localhost:8100/api/events/admin/channels/discord/arm \
        -H "content-type: application/json" -H "x-user-id: admin" \
        -d '{"channel":"<CHANNEL_ID>"}'
   # → {"ok":true,"ap_flow_id":"…"}
   ```

## Verify
```bash
.venv/bin/python tests/events/preflight.py discord         # bot reachable (users/@me)
```
Post a message in the watched channel → a reply appears within the poll interval (~5 min) with a
metadata footer.

## Troubleshooting
- **preflight discord 403 (Cloudflare 1010)** — a bare user-agent is blocked; preflight already sends
  a browser UA. If you script your own call, add `User-Agent: Mozilla/5.0`.
- **No reply** — bot not in the server / channel; Message Content Intent off; wrong channel id in the
  arm call; or AP can't reach CUGA's `/invoke` (`HOST_CALLBACK_URL`).
- **Old messages re-answered** — you re-armed, which reset the poll baseline. Arm once and leave it.
- **~5 min latency (why it feels slow)** — the AP `new_message` trigger **polls** Discord on a
  schedule; there is no push. Telegram (webhook) and Slack (Events API) are instant because they
  *push*. **Instant Discord = a direct gateway backend** (a persistent Discord WebSocket bot,
  analogous to direct Slack) — not built yet; it would also give the author id for free (below).
- **Per-user identity** — the inbound flow forwards the message author as `source.user` via
  `{{trigger.author.id}}`, so a user who `/link`s their Discord id gets per-user creds/permissions.
  ⚠️ The exact author field name is unverified against the AP piece — confirm on the first live
  message (if wrong it falls back to the channel, i.e. today's behavior; a one-word descriptor fix).
