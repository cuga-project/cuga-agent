# The public URL (`EVENTS_PUBLIC_URL`)

Companion to [SETUP.md](SETUP.md). The one page on how CUGA and Activepieces are reachable from the
internet — needed for Slack/Gmail/GitHub webhooks and OAuth callbacks.

## Two tunnels

- **CUGA's** (ngrok) → `EVENTS_PUBLIC_URL`. This is where Slack events, OAuth callbacks, and the
  generic webhook land. **Pin it** with a reserved domain (`EVENTS_NGROK_DOMAIN=<your>.ngrok-free.app`)
  so you never re-point Slack/Gmail consoles after a restart. Strongly recommended.
- **Activepieces'** (cloudflared quick tunnel) → AP's own `AP_FRONTEND_URL`. Used internally by AP and
  as the inbound target for its webhook triggers (e.g. GitHub).

## The `reload` vs `restart` rule

- `make reload` — bounces **CUGA only**, keeps AP + both tunnels. URLs unchanged. Use after a `.env`
  or code edit (the server caches `.env` at startup).
- `make restart` — new tunnel URLs; you must re-point any hardcoded consoles. Avoid unless needed.

## After a restart — the checklist

`make public-url` prints the exact strings to paste. If you did **not** pin the ngrok domain, update:
Slack Event Subscriptions Request URL, and any OAuth redirect URIs (Gmail/GitHub).

## The #1 gotcha — AP's tunnel is ephemeral

The cloudflared quick tunnel dies after a while. When it does, **every AP flow fails with
`INTERNAL_ERROR`** on AP's payload callback — it looks like a code regression but isn't. Diagnose with
`make tunnels`, fix with `make ap` (fresh tunnel; connections survive). Full detail in
[GAPS.md](GAPS.md).
