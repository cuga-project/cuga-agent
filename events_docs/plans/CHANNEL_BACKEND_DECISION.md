# Decision: channel backends (AP-default, two documented exceptions)

Status: **accepted 2026-07-20**. Supersedes the ad-hoc per-channel choices. This is the authoritative
page for "which channel uses Activepieces vs. direct, and why." If you're confused about combinations,
start here. Related: [`../todos_actions/gmail.md`](../todos_actions/gmail.md),
[`../todos_actions/branching.md`](../todos_actions/branching.md).

## The problem this settles

"Direct" was being used for two *different* things, and channels sat on different sides of each, which
made the combination space feel infinite. We collapse it to **one rule with two named exceptions.**

## Two axes (the source of the confusion)

1. **Trigger backend** — how CUGA *hears* an event.
   - `ap`: an Activepieces flow watches the app (webhook = instant, or poll = timer).
   - `direct`: CUGA receives the event on its own bot connection (Slack Events API, Discord Gateway).
2. **Delivery backend** — how CUGA *sends* the reply.
   - `ap`: CUGA appends an AP send step.
   - `direct`: CUGA sends via the app's bot token/gateway.

A channel can be `direct` on one axis and `ap` on the other. Telegram is the classic case: `direct`
*trigger* (bot stream) but `ap` *delivery*. That split is exactly what made this hard to reason about.

## The rule

> **Web → native (never Activepieces). Everything else → Activepieces by default, for BOTH axes.
> `direct` is now an opt-in *exception*, allowed only where AP's piece genuinely can't do the job.**

### The two exceptions (and precisely why)

**Slack — `direct` (bot token + Events API).** Documented in `slack_direct.py`. Two independent
blockers on the AP path:
1. **OAuth2 wall.** `@activepieces/piece-slack` needs a full OAuth2 connection (client id/secret +
   redirect). That setup dance was the wall that blocked the AP path.
2. **Buggy trigger.** The AP slack piece's app-event trigger *silently ate the payload* — the message
   arrived but its content didn't come through.

The direct path (bot `xoxb-…` token → Slack **Events API** → `/api/events/slack/events`) needs **no
OAuth2** and is **already instant** (Events API is a webhook, not a poll). We keep it. The AP path
stays behind `EVENTS_SLACK_BACKEND=ap` to revisit if AP fixes the piece.

**Discord — `direct` (Gateway).** AP's discord trigger is **poll-only**. Best case ~1 minute (see poll
facts below); never instant. The Gateway is real-time. We keep direct. AP path behind
`EVENTS_DISCORD_BACKEND=ap`.

### Everything else is AP
- **Telegram** — already `ap` delivery; make the *trigger* AP too (its AP piece uses a webhook →
  instant). Fully AP, no exception needed.
- **Webhook** — AP `catch_webhook` (instant).
- **Gmail / GitHub / Box** — AP already (Gmail/GitHub webhook = instant; Box poll).

## Poll-interval facts (why "5 min", can it drop)

- There are **two kinds of AP triggers**: **webhook** (app pushes to AP → *instant*, no interval) and
  **polling** (AP pulls on a timer). Only polling triggers are subject to an interval.
- The "5 min" is Activepieces' default poll interval — env `AP_TRIGGER_DEFAULT_POLL_INTERVAL` (minutes)
  on the self-hosted AP.
- **Reducible to ~1 minute** (`AP_TRIGGER_DEFAULT_POLL_INTERVAL=1`). **Not** to real-time — polling has
  a ~1-min floor (cron-driven, per-minute granularity).
- Real-time requires a **webhook-type** trigger, which no interval tuning gives a polling piece. This is
  why Discord (poll-only piece) can never be instant via AP, while Gmail/GitHub/Telegram (webhook) are.

## What this means for ACTIONS (the payoff)

An **action = a step in an AP flow**. So a trigger can carry a Gmail action **iff arming it produces an
AP flow** — i.e. iff its trigger backend is `ap`. Under this decision:

| Trigger source | Trigger backend | Trigger → Gmail action? |
|---|---|---|
| gmail | ap (webhook) | ✅ works |
| github | ap (webhook) | ✅ works (verified live) |
| box (AP mode) | ap (poll) | ✅ works |
| **telegram** | **ap** (after flipping trigger to AP) | ✅ **NEW — unlocked by this decision** |
| webhook | ap | ✅ works |
| cron / poll (schedule) | ap | ✅ works |
| **slack** | **direct** (exception) | ❌ needs the direct→action executor (Option A) |
| **discord** | **direct** (exception) | ❌ needs the direct→action executor (Option A) |

**Web is not a trigger source** — it's where you *arm* an automation and/or *chat*. Arming *from* web an
automation whose trigger is any `ap` source above works today; the confirmation/answer returns to web.

### Consequence: the two exceptions are exactly the two channels that still can't carry an action
Slack and Discord are `direct` for good reasons (Slack: AP bugs; Discord: poll latency) — but `direct`
is precisely what prevents them from carrying an AP action step. So to get **slack→gmail /
discord→gmail actions** we need ONE general piece of machinery (not per-channel):

> **Option A — action-executor flow.** Arm one reusable AP flow per action-app (`catch_webhook ▸
> gmail/send_email`). When a *direct* trigger fires, CUGA runs the agent then POSTs the rendered params
> to that executor. AP keeps the credentials; direct-trigger branching runs in CUGA (Python). Additive;
> doesn't touch the AP-ROUTER path. Build once → slack/discord/box-direct → gmail all light up.

Until then, direct-trigger + action **declines loudly** (implemented 2026-07-20) — never a silent drop.

## Action items this decision implies
- [x] **Option A (direct→action executor) — BUILT 2026-07-20.** slack/discord/telegram triggers can now
      drive an AP-only action (gmail) via a reusable `exec-<app>-<action>` webhook flow CUGA fires after
      the agent answers. AP keeps the creds. See [`../todos_actions/direct_actions.md`](../todos_actions/direct_actions.md)
      for what's built, what's dormant, and live-verification status.
- [ ] Keep **Slack + Discord** direct; document as the two exceptions (this file). *(Telegram rides the
      executor for actions without needing an AP-trigger flip — the flip below is only for real-time
      trigger latency, not for the action capability.)*
- [ ] Flip **telegram trigger** to AP (piece-telegram-bot webhook) — optional; actions already work.
- [ ] Make **AP the default** for the trigger axis; `direct` an opt-in flag (invert today's default).
- [ ] (Later) revisit the AP slack trigger bug + OAuth2 flow if we want zero exceptions.
