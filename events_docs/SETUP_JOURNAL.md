# Setup journal — every channel & integration, and every pain point

The real, warts-and-all account of getting the channels + integrations working — what we did, what
broke, and the fix. For clean step-by-steps see [setup/](setup/); for the sharp edges see
[GOTCHAS.md](GOTCHAS.md). This ties them together in the order you'd actually hit them.

## The mental model that ended most confusion
- **TENANT** = one value for the whole deployment (in `.env`): infra, the LLM, Activepieces admin,
  **channel bot tokens** (one bot per org), and **OAuth *app* client id/secret** (one registration
  per org).
- **USER** = a personal login (per-user): a **PAT** or **dev token** (can sit in `.env` for a single
  operator) or an **OAuth consent** (Gmail — never a static token; done via the Studio wizard).
- A shared bot token does NOT mean one user — the **author of each message** is who tells users apart.
- `.env` is split into tagged TENANT / USER sections; the Studio reads it and shows *configured ✓* or
  a *set-up →* wizard, plus the live **● Connected / ○ Not connected** status.

---

## Channels

### Web — trivial
In-process; nothing to set up.

### Telegram — Activepieces (webhook, instant)
- **Did:** @BotFather → `TELEGRAM_BOT_TOKEN`; `EVENTS_TELEGRAM_BOT_USERNAME`; `ap_up.sh` (AP + tunnel);
  arm the inbound flow.
- **Pain:** `setWebhook` refuses non-HTTPS → AP's `AP_FRONTEND_URL` must be the tunnel. The bot
  username must match (a wrong handle breaks the `/start` account-link deep-link).

### Slack — DIRECT (Events API), the default
- **Did:** api.slack.com/apps → bot scopes (`chat:write, channels:history, channels:read`) →
  `SLACK_BOT_TOKEN`; Event Subscriptions Request URL = `<EVENTS_PUBLIC_URL>/api/events/slack/events`;
  subscribe `message.channels`; invite the bot.
- **Pain (why we went direct):** AP's slack `new-message` trigger emitted `[]` (AP 0.82 + piece
  0.17.2 payload-shape bug) AND AP's OAuth2 refuses a pre-obtained bot token. Slack's own API takes
  the `xoxb` token directly → instant, no AP. AP path parked behind `EVENTS_SLACK_BACKEND=ap`.
- **Also fixed:** reply-in-thread (`thread_ts`, per-thread memory) + per-user author id (`ev.user`).

### Discord — DIRECT (Gateway WebSocket), the default
- **Did:** dev portal → bot → `DISCORD_BOT_TOKEN`; **enable MESSAGE CONTENT INTENT**; invite the bot.
- **Pain:** without MESSAGE CONTENT INTENT the Gateway closes with **code 4014**. AP's path polls
  (~5 min); the Gateway is instant and needs **no public URL** (outbound WS). AP path behind
  `EVENTS_DISCORD_BACKEND=ap`.

---

## Integrations (all on Activepieces — AP holds/refreshes the token + runs the trigger)

### GitHub — token (PAT)
- **Did:** GitHub → Developer settings → PAT → `GITHUB_TOKEN` [USER] in `.env` → **auto-connected on
  startup** ("set in .env == connected"). Driving agent: `pr_reviewer`.
- **Pain:** a **fine-grained** PAT (`github_pat_…`) can't create repos / may be read-scoped — fine for
  reading PRs (which is `pr_reviewer`'s job) but not for creating them. The PR trigger needs you to
  **name the repo** (`when a PR opens on owner/repo…`) — the concierge can't guess it.

### Box — AP OAuth by default; **direct-poll opt-in** for a quick test
- **Did (direct opt-in):** `EVENTS_BOX_BACKEND=direct` + `BOX_DEV_TOKEN` [USER]; poll via
  `POST /api/events/box/poll`. Driving agent: `resume_judge`.
- **Pain:** **dev tokens expire ~60 min** (regenerate constantly). A free/personal Box **can't save a
  redirect URI**, so the AP-OAuth path needs a paid/dev Box app — the direct poll sidesteps that.
  Open gap: the watcher passes the file *name*, not its *content*.

### Gmail — AP OAuth (consent + refresh). **This was the painful one.**
- **Did:** Google Cloud OAuth client → `EVENTS_OAUTH_GMAIL_CLIENT_ID/_SECRET` [TENANT] → per-user
  consent via the Studio wizard. Driving agents: `mailbot`, `resume_judge` (email delivery).
- **The saga (in order):**
  1. `redirect_uri_mismatch` — the redirect URI wasn't registered on the client. Register EXACTLY
     `<EVENTS_PUBLIC_URL>/api/events/connect/gmail/callback` (https, no trailing slash) — and on the
     **same client** whose id is in `.env` (the `client_id` changed twice as new clients were made).
  2. **"No Authorized redirect URIs field"** — because the client was a **Desktop** type. Only a
     **Web application** client has redirect URIs. Recreate as Web application.
  3. `deleted_client` — the client referenced by `.env` had been deleted. Point `.env` at a live one
     and restart.
  4. `access_denied` (403, "app being tested") — the consenting account wasn't a **Test user**. Add
     it under *OAuth consent screen → Test users*.
  5. Consent screen shows "unverified app" → **Advanced → Go to \<app\> (unsafe) → Allow** (expected
     in Testing mode). **Testing-mode refresh tokens expire after 7 days** — re-Connect weekly.
- **Lesson:** Gmail = a **Web-application** OAuth client + the exact redirect URI + your email as a
  **test user** + Gmail API enabled. Nothing is a static token — the login is a consent.

---

## The generic webhook (direct, no AP) — a bonus
`POST /api/events/hook/<name>` → renders the payload → `incident_triage` (or any agent) → optional
channel delivery. Use for monitoring alerts / CI failures / form leads. See [setup/WEBHOOK.md].

---

## The single biggest recurring pain: the tunnel
The cloudflared **quick-tunnel URL is ephemeral**. Every restart invalidates `EVENTS_PUBLIC_URL`, the
Slack Events URL, the Google/Box OAuth redirect URIs, AP's `AP_FRONTEND_URL`, and any webhooks. A
**named tunnel or a real domain** ends this. Direct Discord + Box poll (outbound only) don't care.
