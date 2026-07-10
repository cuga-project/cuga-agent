# The public URL (`EVENTS_PUBLIC_URL`) — one page

Everything about the public HTTPS URL: what it is, when it's generated, how it's wired, and the
*exact* step to update your integrations when it changes. If you only read one thing about tunnels,
read this.

## TL;DR
- **Do this once:** set `EVENTS_NGROK_DOMAIN` to a free reserved ngrok domain → your public URL is
  **stable forever**, and you configure Slack/Gmail **one time**. This is the recommended setup; see
  [§ Stable URL](#permanent-fix--a-stable-url-strongly-recommended). Everything below is the mechanics.
- **Find it anytime:** `make public-url` (also printed at the end of `make up` / `make reload`).
- **You never edit `EVENTS_PUBLIC_URL` by hand** — with ngrok it's pinned to your domain; with a quick
  tunnel it's auto-wired from the live tunnel each start.
- **Only two external consoles ever matter:** **Slack** (Request URL) and **Gmail** (OAuth redirect).
  With a stable URL that's a one-time step; with a quick tunnel you re-point them whenever it changes.
- **Avoid churn:** use `make reload` (server-only; URL unchanged), not `make restart`, for
  `.env`/code changes.

## The two tunnels
Local ports aren't reachable from the internet, so `cloudflared` exposes them over HTTPS. There are
**two** URLs, for two jobs:

| Tunnel | Exposes | Feeds | Used by |
|---|---|---|---|
| **CUGA tunnel** | the CUGA server (`:8100`) | **`EVENTS_PUBLIC_URL`** | Slack Events **Request URL**, OAuth callbacks (Gmail/Box) |
| **AP tunnel** | Activepieces (`:8081`) | `AP_FRONTEND_URL` (inside AP) | Telegram / Slack-AP **webhooks** that AP registers |

`make public-url` is about the **CUGA** one (`EVENTS_PUBLIC_URL`). The AP one is managed for you by
`scripts/ap_up.sh` and re-applied to Telegram by `make channels`.

## When / where it's generated
[`scripts/events_up.sh`](../scripts/events_up.sh) runs `cloudflared tunnel --url http://localhost:8100`,
which gets a **random** `https://<words>.trycloudflare.com` at that moment (logged to
`/tmp/events_up/cuga_tunnel.log`). The script then **detects that URL and exports it to the CUGA
server as `EVENTS_PUBLIC_URL`** before launch — so the server always matches the live tunnel, with no
stale-`.env` lag.

## `.env`'s `EVENTS_PUBLIC_URL` — fallback or pin
- A **`trycloudflare.com`** value is treated as a stale fallback → the **live tunnel wins**.
- A **non-`trycloudflare`** value (e.g. `https://cuga.yourdomain.com`) is treated as a **pin** and
  respected as-is. This is how you'd wire a stable/named tunnel later — no code change.

## After a restart — the checklist
**With a stable ngrok URL (`EVENTS_NGROK_DOMAIN` set): the CUGA URL does NOT change, so steps 4–5 are
one-time** — after a restart you only re-arm channels (`make channels`) because the *AP* tunnel (free
tier stays on cloudflared) may have moved. With a quick tunnel, the whole table applies every restart.

| # | What | How |
|---|---|---|
| 1 | `EVENTS_PUBLIC_URL` | ngrok: **fixed**. quick tunnel: **auto-wired** on `make up`/`reload` |
| 2 | See the URL + what to update | **`make public-url`** |
| 3 | Re-arm inbound channels | `make channels` (re-sets Telegram's webhook; re-prints Slack URL) |
| 4 | **Slack** Event Subscriptions **Request URL** | `<url>/api/events/slack/events` — ngrok: **once**; quick tunnel: every change |
| 5 | **Gmail** redirect URI **+ re-consent** | `<url>/api/events/connect/gmail/callback` — ngrok: **once**; quick tunnel: every change |
| — | **Box** (direct poll), **Discord** (Gateway) | nothing — outbound-only, tunnel-immune |

So the only **external consoles** you ever touch are **Slack** and **Gmail** — once, if you use ngrok.

## `reload` vs `restart`
- **`make reload`** — bounces only the CUGA server (pick up `.env`/code). Tunnels and the URL stay
  put, so steps 3–5 above do **not** apply. This is the everyday inner loop.
- **`make restart`** — full stop/start; **new tunnel URLs** → run the checklist. Use only when you
  changed AP or need fresh tunnels.

## Permanent fix — a stable URL (STRONGLY recommended)
Quick tunnels are random *and can flap mid-session* (the URL changes even without a restart), which
silently breaks Slack/OAuth. Pin a stable URL and the whole problem disappears — configure Slack/Gmail
**once**, forever.

### ngrok static domain (no domain to buy) — [full step-by-step: setup/NGROK.md](setup/NGROK.md)
1. Create a free ngrok account and **verify your email** (agent sessions are blocked until you do):
   https://dashboard.ngrok.com/user/settings
2. `brew install ngrok` and set your token once: `ngrok config add-authtoken <token>`.
3. Reserve your free static domain: https://dashboard.ngrok.com/domains → e.g. `you-cuga.ngrok-free.app`.
4. In `.env`: `EVENTS_NGROK_DOMAIN=you-cuga.ngrok-free.app` → `make restart`.
   `events_up.sh` now serves `:8100` on that domain and pins `EVENTS_PUBLIC_URL` to it.
5. Set the Slack Request URL / Gmail redirect to `https://you-cuga.ngrok-free.app/…` **once**.

Free tier = 1 domain → this covers **CUGA** (Slack + OAuth). The **AP** tunnel (Telegram webhook) stays
on a cloudflared quick-tunnel, so after an AP restart just re-run `make channels` to re-arm Telegram —
or reserve a 2nd ngrok domain / use a paid plan and point AP at it via `AP_TUNNEL_URL`.

### Cloudflare named tunnel (needs a domain on Cloudflare)
Gives `cuga.yourdomain.com` **and** `ap.yourdomain.com`, both stable, free tunnel. `cloudflared login →
tunnel create → route dns → run`, then set `EVENTS_PUBLIC_URL=https://cuga.yourdomain.com` (pinned) and
pass `AP_TUNNEL_URL=https://ap.yourdomain.com` to `ap_up.sh`.

See also: [OPERATIONS.md](OPERATIONS.md) (day-to-day + every sharp edge) · [SETUP.md](SETUP.md) (the
bring-up runbook).
