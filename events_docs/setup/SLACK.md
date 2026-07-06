# Slack setup (direct backend — the default)

Slack talks to CUGA **directly**: the Slack Events API POSTs to a CUGA endpoint and CUGA replies with
the bot token via `chat.postMessage`. No Activepieces, no OAuth consent — just a bot token.

```
Slack channel message ─▶ Slack Events API ─▶ POST /api/events/slack/events (CUGA)
                                                      │
                                             /invoke (concierge)
                                                      │
                                        chat.postMessage ◀── the reply (with metadata footer)
```

> The AP-based Slack path still exists behind `EVENTS_SLACK_BACKEND=ap`, but it's **parked** — under
> AP 0.82 + slack-piece 0.17.2 the `new-message` trigger emits `[]` (events arrive, no flow fires).
> The direct backend sidesteps that *and* AP's OAuth wall. Leave the default (`direct`).

## What you'll need
- A Slack workspace where you can create an app.
- A public HTTPS URL for CUGA (`EVENTS_PUBLIC_URL`) — see [README.md](README.md) prerequisites.

## Steps

1. **Create the app** — go to <https://api.slack.com/apps> → **Create New App** → **From scratch**.
   Name it, pick your workspace.

2. **Bot token scopes** — **OAuth & Permissions** → *Scopes* → *Bot Token Scopes*, add:
   - `chat:write` — post replies
   - `channels:history` — read messages in public channels
   - `channels:read` — resolve channel info
   - (optional) `groups:history` — private channels

3. **Install the app** — **Install to Workspace** → Allow. Copy the **Bot User OAuth Token**
   (`xoxb-…`).

4. **Add to `.env`**:
   ```
   SLACK_BOT_TOKEN=xoxb-…
   SLACK_SIGNING_SECRET=…      # Basic Information → App Credentials → Signing Secret (recommended)
   ```
   Then **restart the server**. `SLACK_SIGNING_SECRET` lets CUGA verify each request is really from
   Slack; without it, events are accepted but flagged "unverified".

5. **Get the events URL** — arm returns the exact URL to paste (nothing is created in AP):
   ```bash
   curl -s -X POST localhost:8100/api/events/admin/channels/slack/arm \
        -H "content-type: application/json" -H "x-user-id: admin" -d '{}'
   # → {"ok":true,"backend":"direct","events_url":"https://<tunnel>/api/events/slack/events", …}
   ```

6. **Point Slack at it** — **Event Subscriptions** → toggle **On** → **Request URL** = the
   `events_url` above. Slack sends a `url_verification` challenge; CUGA echoes it → **✅ Verified**
   appears immediately. Under **Subscribe to bot events** add **`message.channels`**
   (+ `message.groups` for private). **Save Changes** (reinstall if prompted).

7. **Invite the bot** to the channel: `/invite @your-bot-name`.

## Verify

```bash
# wiring (token, membership, can-post, arm)
EVENTS_SERVER_URL=http://localhost:8100 .venv/bin/python tests/events/live_slack_check.py

# full round-trip: post any message in the channel → instant reply with a metadata footer, e.g.
#   "The capital of Japan is Tokyo.  — geobot · via cuga-knowledge, cuga-geo · 20.6s"
```

To simulate a message without Slack (when the signing secret is unset):
```bash
curl -s -X POST localhost:8100/api/events/slack/events -H "content-type: application/json" \
  -d '{"type":"event_callback","event":{"type":"message","text":"what is the capital of Japan?","channel":"<CHANNEL_ID>","user":"U1"}}'
```

## Troubleshooting
- **Request URL won't verify** — the tunnel URL changed (quick-tunnels are ephemeral). Update
  `EVENTS_PUBLIC_URL`, restart, re-arm, re-paste. Confirm `curl <tunnel>/api/events/status` → 200.
- **No reply after posting** — bot not invited to the channel; or you posted in a private channel
  without `groups:history` + `message.groups`; or `SLACK_BOT_TOKEN` missing (arm returns 400).
- **Bot replies to itself / loops** — shouldn't happen: `should_process` skips bot messages
  (`bot_id`/`subtype`). If it does, check the bot isn't posting as a user token.
- **Delivery to Slack from a *scheduled* flow** (not a reply) uses **direct-channel delivery** — CUGA
  sends it, no AP send-step. Nothing extra to configure.
