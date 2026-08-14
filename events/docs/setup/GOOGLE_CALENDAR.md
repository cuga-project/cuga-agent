# Google Calendar setup (Activepieces piece — polling/webhook)

Google Calendar is an **Activepieces** trigger: AP holds the OAuth connection and CUGA arms a flow
that calls back `/invoke` on each event. The agent holds **no** Google credential — AP owns it.

```
Calendar event ─▶ AP (google-calendar piece) ─▶ /invoke (agent) ─▶ [action / delivery]
```

## What you'll need
- A Google account with the calendar you want to watch.
- Activepieces reachable, with a **Google Calendar OAuth connection** (the browser consent creates it).

## Steps

1. **Connect Google Calendar in Activepieces** — AP → **Connections** → **Google Calendar** →
   authorize with the Google account. (Or set the platform Google OAuth app so the consent link
   works from CUGA.) A bare token will not satisfy AP's OAuth2 connection schema.

2. **Arm a watcher** — just say what you want; the concierge picks the trigger and asks for the
   calendar if it needs one:
   ```
   /push when a new event is added to my google calendar, brief me
   /push when a meeting ends on my calendar, email me the follow-ups
   ```

## The triggers

| Trigger (what you say) | AP trigger | Slot |
|---|---|---|
| `new_event` — *"when a new event is added to my calendar…"* | `new_event` (webhook) | `calendar` (id or `primary`) |
| `new_or_updated_event` — *"when a calendar event is updated…"* | `new_or_updated_event` (poll) | `calendar` |
| `event_ends` — *"when a meeting ends…"* | `event_ends` (poll) | `calendar` |

`calendar_id` is a required AP dropdown resolved against the connection; name a calendar id (or
`primary`) in your utterance and CUGA passes it through, otherwise the arm asks.

## Verify
Arm one, then create/end an event on the watched calendar. Polling triggers fire on the next tick;
the `new_event` webhook fires promptly. The agent's answer is delivered to the origin channel.

## Troubleshooting
- **"connect your Google Calendar"** — no AP connection exists yet; do step 1.
- **Flow won't publish** — the calendar dropdown couldn't resolve; confirm the connection has access
  to that calendar id.
