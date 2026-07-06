# 0008 — Direct backends for chat channels (amends 0001)

## Context
[0001](0001-ap-as-the-event-engine.md) made Activepieces the *one* event engine — every trigger is
an AP flow. That is the right call for **integrations** (Gmail/Box/GitHub), where AP's real value is
holding & refreshing OAuth tokens, hosting the piece triggers, and being the clock.

But when we wired the **chat channels** (Slack, Discord) through AP, three things broke:

1. **AP's OAuth2 connector demands the authorization *code*** and performs the token exchange
   itself. It refuses a pre-obtained bot token (`xoxb…`, a Discord bot token) — which is exactly
   what a chat bot *is*. There is no supported "I already have the token" path.
2. **AP's Slack `new-message` trigger emitted empty payloads** (AP 0.82 + piece 0.17.2) — the
   message never reached `/invoke`.
3. **Latency.** AP's channel triggers *poll* (~1–5 min). For a conversation that is unusable.

## Decision
Split the backends by connector kind — encoded in `delivery.channel_backend()`:

- **Channels use DIRECT backends by default** — CUGA owns the socket:
  - **Slack** → the Events API (`slack_direct.py`): Slack POSTs signed events to
    `/api/events/slack/events`; we reply with `chat.postMessage`. Instant.
  - **Discord** → the Gateway WebSocket (`discord_direct.py`): an outbound WS bot, instant, and it
    needs **no public URL** at all.
  - **Telegram** stays on **AP** (its webhook piece works and is instant).
  - **Web** is in-process.
- **Integrations stay on AP** (unchanged from 0001) — OAuth/token connection + piece trigger + clock.
- **The AP path for Slack/Discord is kept behind a flag** (`EVENTS_SLACK_BACKEND=ap`,
  `EVENTS_DISCORD_BACKEND=ap`) so the decision is reversible, not deleted.
- **Box gets a direct-poll opt-in** (`EVENTS_BOX_BACKEND=direct`, `box_direct.py`) for quick tests
  without a paid Box app, but **defaults to AP** — it's an integration, not a channel.

Direct channels also enable **direct-channel delivery**: a standing flow (cron/poll) whose sink is a
direct channel carries `deliver:true` + `source={type:channel,…}`, and `/invoke` sends the answer
itself via the channel adapter — no AP send-step.

## Consequences
- Channels are instant and don't fight AP's OAuth wall; the "one engine" invariant now reads **"AP is
  the one engine for integration triggers and the clock,"** which 0001 should be read alongside.
- Two code paths to maintain (direct adapters + AP flows), justified by the latency + auth mismatch.
- A shared bot token is TENANT-scoped; per-user identity still comes from the message author
  (`source.user`) — see [0007](0007-identity-profiles-permissions.md).
- Direct backends are outbound/inbound HTTP+WS from CUGA, so they inherit CUGA's own auth posture,
  not AP's. The Slack receiver verifies the signing secret; see [KNOWN_GAPS.md](../KNOWN_GAPS.md).
