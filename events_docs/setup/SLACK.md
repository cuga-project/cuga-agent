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
   curl -s -X POST localhost:7860/api/events/admin/channels/slack/arm \
        -H "content-type: application/json" -H "x-user-id: admin" -d '{}'
   # → {"ok":true,"backend":"direct","events_url":"https://<tunnel>/api/events/slack/events", …}
   ```

6. **Point Slack at it** — **Event Subscriptions** → toggle **On** → **Request URL** = the
   `events_url` above. Slack sends a `url_verification` challenge; CUGA echoes it → **✅ Verified**
   appears immediately. Under **Subscribe to bot events** add **`message.channels`**
   (+ `message.groups` for private). **Save Changes** (reinstall if prompted).

7. **Invite the bot** to the channel: `/invite @your-bot-name`.

## Slack WATCHERS — the other 7 triggers (optional)

Steps 1–7 give you the **chat bot** (a human talks, an agent answers). Slack also exposes 7 more
triggers CUGA can arm as **watchers** — *"when a message gets a `:bug:` reaction, triage it"*, *"when a
new teammate joins, send an onboarding brief"*. These arm as **CUGA-owned direct subscriptions**: no
Activepieces flow, no AP Slack connection — CUGA already receives the Slack Events API.

They cannot fire until the Slack app is **subscribed to that event type**. Add whichever you want
under **Event Subscriptions → Subscribe to bot events**, then **reinstall the app** and add the paired
OAuth scope under **OAuth & Permissions → Bot Token Scopes**:

| Trigger (what you say) | Slack bot event | Scope also needed |
|---|---|---|
| `new_reaction` — *"when a message gets a `:bug:` reaction…"* | `reaction_added` | `reactions:read` |
| `reaction_removed` — *"when a `:white_check_mark:` is removed…"* | `reaction_removed` | `reactions:read` |
| `new_slack_mention` — *"when the team is @mentioned…"* | `app_mention` | `app_mentions:read` |
| `channel_created` — *"when a new channel is created…"* | `channel_created` | `channels:read` |
| `new_slack_user` — *"when a new user joins the workspace…"* | `team_join` | `users:read` |
| `new_emoji` — *"when a new custom emoji is added…"* | `emoji_changed` | `emoji:read` |
| `saved_message` — *"when I save a message…"* | `star_added` | `stars:read` |
| `new_channel_message` — *"when a message is posted in #incidents…"* | `message.channels` | *(already have it)* |

## Only answer when @mentioned (optional)

By default **every** channel message the bot can see reaches the concierge. To make the bot answer
**only when @mentioned** (like Telegram's group privacy mode), set:

```bash
EVENTS_SLACK_CHAT=mention     # then: make reload
```

- **1:1 DMs (`im`) always reach the bot** — a Slack `im` is strictly you and the bot, so every
  message there is addressed to it. Multi-person conversations (channels, private groups, group
  DMs) are all mention-gated.
- **Replies to the bot pass without a mention** — when the bot posts (e.g. a trigger's answer) and
  a human replies in that thread, the reply reaches the concierge (same rule as Telegram's privacy
  mode, detected via the thread root's author).
- The bot's `<@U…>` mention is stripped before the text reaches the concierge.
- **Armed channel-message watchers are NOT gated** — *"watch #incidents"* still sees every message;
  the mode only controls chat. (The watcher and chat are two consumers of the same event stream.)
- The bot's user id is resolved automatically (auth.test); set `SLACK_BOT_USER_ID` to skip that call.

**You only need the rows you actually want.** An event you never subscribe to simply never arrives —
the watcher arms, sits idle, and nothing breaks. CUGA reports the requirement when you arm one:

```
ARMED direct watcher (slack/new_reaction [emoji=bug]) for incident_triage → web
  (the Slack app must be subscribed to this event type — see events_docs/setup/SLACK.md)
```

Then arm it in chat:

```
/push when a message gets a :bug: reaction in slack, triage it as an incident
```

## Verify

```bash
# wiring (token, membership, can-post, arm)
EVENTS_SERVER_URL=http://localhost:7860 .venv/bin/python tests/events/live_slack_check.py

# full round-trip: post any message in the channel → instant reply with a metadata footer, e.g.
#   "The capital of Japan is Tokyo.  — geobot · via cuga-knowledge, cuga-geo · 20.6s"
```

To simulate a message without Slack (when the signing secret is unset):
```bash
curl -s -X POST localhost:7860/api/events/slack/events -H "content-type: application/json" \
  -d '{"type":"event_callback","event":{"type":"message","text":"what is the capital of Japan?","channel":"<CHANNEL_ID>","user":"U1"}}'
```

## Threads & identity (how context is scoped)
- **Per-thread memory + reply-in-thread.** A reply carries `thread_ts`; a root message uses its own
  `ts` to *start* a thread. The bot replies **in that thread**, and conversation memory is keyed
  `gw:slack:<channel>#<thread_ts>` — so **one Slack thread = one topic**, isolated from other threads.
- **Per-user identity.** Each event carries the author (`ev.user`), forwarded as `source.user`. Once
  a user runs `/link <token>` (from `POST /api/events/link/slack`), their Slack id maps to a tenant
  user, so their **per-user credentials + permissions** apply — even though the bot is shared.
  Unlinked users fall back to the default principal.

## Troubleshooting
- **No reply after posting** — the #1 cause: the **Request URL is stale** (pointing at an old tunnel).
  On a quick tunnel the URL changes every restart; set **`EVENTS_NGROK_DOMAIN`** for a stable URL you
  paste **once** (see [PUBLIC_URL.md](../PUBLIC_URL.md)). Diagnose: `make public-url` shows the current
  URL — it must match your Slack app's Request URL. The endpoint itself is easy to test:
  `curl -s -X POST <url>/api/events/slack/events -d '{"type":"url_verification","challenge":"x"}'`
  should echo `x`. If that works but posts get no reply, Slack isn't delivering → **Request URL wrong/
  unverified**, or **`message.channels` not subscribed**, or the **bot isn't invited** to the channel
  (`/invite @bot`), or a private channel needs `groups:history` + `message.groups`.
- **Request URL won't verify** — the endpoint must be publicly reachable (`curl <url>/api/events/status`
  → 200) and echo the challenge (above). With a quick tunnel, re-check `make public-url` first.
- **Bot replies to itself / loops** — shouldn't happen: `should_process` skips bot messages
  (`bot_id`/`subtype`). If it does, check the bot isn't posting as a user token.
- **Delivery to Slack from a *scheduled* flow** (not a reply) uses **direct-channel delivery** — CUGA
  sends it, no AP send-step. Nothing extra to configure.
