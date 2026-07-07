# The public URL (`EVENTS_PUBLIC_URL`) — one page

Everything about the public HTTPS URL: what it is, when it's generated, how it's wired, and the
*exact* step to update your integrations when it changes. If you only read one thing about tunnels,
read this.

## TL;DR
- **Find it:** `make public-url` (also printed at the end of `make up` / `make reload`).
- **You don't edit `.env` for it** — the live tunnel URL is auto-wired into the server on every
  `make up` / `make reload`.
- **When it changes, update only two external consoles:** **Slack** (Request URL) and **Gmail**
  (OAuth redirect + re-consent). `make public-url` prints the exact strings.
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

## After a restart / tunnel change — the checklist
| # | What | How |
|---|---|---|
| 1 | `EVENTS_PUBLIC_URL` re-points | **automatic** (`make up` / `make reload`) |
| 2 | See the URL + what to update | **`make public-url`** |
| 3 | Re-arm inbound channels | `make channels` (re-sets Telegram's webhook; re-prints Slack URL) |
| 4 | **Slack** (direct): Event Subscriptions **Request URL** | `<url>/api/events/slack/events` at api.slack.com/apps → your app (it re-verifies) |
| 5 | **Gmail** (OAuth): redirect URI **+ re-consent** | `<url>/api/events/connect/gmail/callback` in Google Cloud Console (char-exact, no trailing slash) → re-Connect in the Studio |
| — | **Box** (direct poll), **Discord** (Gateway) | nothing — outbound-only, tunnel-immune |

So the only **external consoles** you ever touch are **Slack** and **Gmail**.

## `reload` vs `restart`
- **`make reload`** — bounces only the CUGA server (pick up `.env`/code). Tunnels and the URL stay
  put, so steps 3–5 above do **not** apply. This is the everyday inner loop.
- **`make restart`** — full stop/start; **new tunnel URLs** → run the checklist. Use only when you
  changed AP or need fresh tunnels.

## Permanent fix (optional)
Quick tunnels are random by design. For a URL that never changes, pin a **named Cloudflare tunnel**
(needs a domain on Cloudflare) or an **ngrok static domain** (no domain needed), then set that URL as
`EVENTS_PUBLIC_URL` in `.env` (non-`trycloudflare` → pinned). Then you configure Slack/Gmail once and
never touch them again.

See also: [OPERATIONS.md](OPERATIONS.md) (day-to-day + every sharp edge) · [SETUP.md](SETUP.md) (the
bring-up runbook).
