# Operations — running it day-to-day + every sharp edge you'll hit

The practical guide to running the event-driven layer: the mental model that ends most confusion,
the recurring pains (in rough order of how often they bite), and per-connector setup notes distilled
from wiring every channel and integration.

For architecture see [README.md](README.md); for the clean quick-start see [SETUP.md](SETUP.md); for
per-connector step-by-steps see [setup/](setup/README.md). Security posture lives in
[KNOWN_GAPS.md](KNOWN_GAPS.md) — not repeated here.

> **Everyday commands** — the root [`Makefile`](../Makefile) wraps the day-to-day loop; `make` lists
> all targets. The ones you'll use most: `make up` / `make stop` (start/stop AP + CUGA, keeps data),
> `make channels` (connect + arm the inbound chat channels — **re-run after any tunnel-URL change**),
> `make nuke` (stop **and** wipe AP volumes + `events.db` — the full reset), `make status`,
> `make logs`, `make env-check` / `make doctor`, `make test`. Details in [SETUP.md](SETUP.md#tldr--the-make-shortcuts).

---

## 1. The mental model (TENANT vs USER creds)

Every credential is either **TENANT** (one for the whole org/deployment) or **USER** (per person).
Getting this split right ends most of the confusion.

- **TENANT** — one value for the whole deployment, in `.env`: infra, the LLM, Activepieces admin, the
  **channel bot tokens** (one bot per org), and **OAuth *app* client id/secret** (one app registration
  per org). Example: `SLACK_BOT_TOKEN`, `DISCORD_BOT_TOKEN`, `EVENTS_OAUTH_GMAIL_CLIENT_ID/_SECRET`.
- **USER** — a personal identity, per person: a **PAT** or **dev token** (may sit in `.env` for a
  single operator), or an **OAuth consent** (Gmail — never a static token; done via the Studio wizard,
  stored in AP as `ea::<tenant>::<user>::<app>`). Example: `GITHUB_TOKEN`, `BOX_DEV_TOKEN`.

**A shared bot token does NOT collapse everyone into one user.** One org-wide bot is normal — the
**author of each incoming message** is who tells Alice from Bob. Identity comes from the message
(`ev.user` on Slack, `source.user`, the Telegram author + `/link`), not from the token.

`.env` is split into tagged TENANT / USER sections. The Studio reads it and shows *configured ✓* or a
*set-up →* wizard, plus the live **● Connected / ○ Not connected** status.

> Note: `EVENTS_USER_ID=admin` in dev collapses everything to one user. Real per-user identity needs
> the channel author + account-linking (Telegram verified; Slack/Discord wired via `source.user`).

---

## 2. The recurring pains (biggest first)

### The ephemeral tunnel URL (the #1 pain)
> Full one-pager: **[PUBLIC_URL.md](PUBLIC_URL.md)** — what it is, when it's generated, the auto-wire,
> and the per-connector update checklist. The essentials are below.

Cloudflare **quick-tunnels are ephemeral** — the URL changes on every `cloudflared` restart. When it
does, **all** of these go stale at once:
- `EVENTS_PUBLIC_URL`
- the Slack Event-Subscriptions Request URL
- every OAuth redirect URI (Google/Gmail, Box)
- AP's `AP_FRONTEND_URL`
- any registered webhooks

Fix: use a **named tunnel or a real domain** for anything you don't want to re-point. The most
tunnel-resilient connectors are **direct Discord (Gateway)** and **Box poll** — both are outbound-only
and need **no** public URL.

**Checklist — after a restart / tunnel change:**
| # | Action | How |
|---|---|---|
| 1 | `EVENTS_PUBLIC_URL` → the new tunnel | **automatic** — `make up` / `make reload` detect the live CUGA tunnel and feed it to the server, so it's never stale. (`.env`'s value is just a fallback; a *non-tunnel* URL there is treated as a pinned/stable override and respected.) |
| 2 | Find the URL + what to update | **`make public-url`** — prints the URL and the exact Slack/Gmail strings (also printed at the end of `make up`) |
| 3 | Re-arm inbound channels | `make channels` — re-sets Telegram's webhook, re-prints the Slack Request URL |
| 4 | **Slack** (direct): Event Subscriptions **Request URL** | `<url>/api/events/slack/events` at api.slack.com/apps → your app (Slack re-verifies) |
| 5 | **Gmail** (OAuth): redirect URI **+ re-consent** | `<url>/api/events/connect/gmail/callback` in Google Cloud Console (char-exact, no trailing slash) → re-Connect in the Studio |
| — | **Box** (direct poll), **Discord** (Gateway), **Telegram** | nothing (Box/Discord are outbound-only; Telegram is handled by step 3) |

So after a restart the runtime self-heals its own URL; the only **external consoles** you ever touch
are **Slack** and **Gmail** — and `make public-url` hands you the exact strings. Prefer `make reload`
(server-only, tunnels + URL unchanged) over `make restart` so you rarely hit this at all.

### The server caches `.env` at startup
Creds/tokens are read once at boot. **Edit `.env` → `make reload`** to pick them up (bounces the
CUGA server only — keeps AP + tunnels, so URLs don't change). Reach for `make restart` **only** when
you changed AP or need fresh tunnels; it re-triggers the whole tunnel checklist above. Silent "why is
my new token not working" is almost always a missed reload.

### DO NOT set `AP_WORKER_TOKEN`
AP 0.82's entrypoint **mints** `AP_WORKER_TOKEN` as a JWT signed with `AP_JWT_SECRET` when it is left
unset. Set it to a random string and the worker **crash-loops** on a Socket.IO "Authentication error",
and every channel/schedule publish hangs ~30s. `.ap.env` should hold **only** `AP_ENCRYPTION_KEY` +
`AP_JWT_SECRET`. (Also required: `AP_POSTGRES_PORT=5432`, else `SYSTEM_PROP_NOT_DEFINED`.)

### Short-lived tokens expire on you
- **Box dev tokens expire ~60 min.** A 401 from `users/me` (or `preflight.py box`) = regenerate it in
  the Box console. This is why `BOX_DEV_TOKEN` is USER-tagged, not a durable cred.
- **Gmail Testing-mode refresh tokens expire after 7 days** (Google's rule for sensitive scopes).
  Fine for a demo — just re-Connect weekly. Publishing the app to Production removes the limit (but
  triggers a weeks-long verification).

### Run AP CE on Postgres, not sqlite
Run Activepieces Community Edition against **Postgres**, not sqlite. Note `activepieces:latest` is
**not** all-in-one — it needs external postgres + redis + full env. `scripts/ap_up.sh` sets
`AP_FRONTEND_URL` to the tunnel (Telegram/Slack `setWebhook` demand HTTPS).

### Ports
- **AP → 8081**
- **CUGA → 8100** (Studio at `http://localhost:8100/studio`)
- **registry → 8021**

The server caches env at startup (see above). The frontend is a **pre-built webpack bundle** in
`src/cuga/frontend/dist` — rebuild after `.tsx` changes via `scripts/frontend_build.sh`.

---

## 3. Per-connector setup notes & pain points

Clean step-by-steps live in [setup/](setup/README.md); this section is the warts-and-all summary and
the pain points for each.

### Channels

#### Web — trivial
In-process; nothing to set up.

#### Telegram — Activepieces (webhook, instant) — [setup/TELEGRAM.md](setup/TELEGRAM.md)
- **Setup:** @BotFather → `TELEGRAM_BOT_TOKEN`; set `EVENTS_TELEGRAM_BOT_USERNAME`; run `ap_up.sh`
  (AP + tunnel); arm the inbound flow.
- **Pain:** `setWebhook` refuses non-HTTPS → AP's `AP_FRONTEND_URL` must be the tunnel. The bot
  username must match exactly — a wrong handle breaks the `/start` account-link deep-link.

#### Slack — DIRECT (Events API), the default — [setup/SLACK.md](setup/SLACK.md)
- **Setup:** api.slack.com/apps → bot scopes (`chat:write`, `channels:history`, `channels:read`) →
  `SLACK_BOT_TOKEN`; set the Event Subscriptions Request URL to
  `<EVENTS_PUBLIC_URL>/api/events/slack/events`; subscribe to `message.channels`; invite the bot.
  `SLACK_SIGNING_SECRET` is recommended — without it CUGA accepts events but flags them "unverified".
- **Why it went direct:** AP's slack `new-message` trigger emitted `[]` even for a clean event (AP
  0.82 + slack-piece 0.17.2 payload-shape bug → no flow-run), **and** AP's OAuth2 refuses a
  pre-obtained bot token. Slack's own API takes the `xoxb` token directly → instant, no AP. The AP
  path is parked behind `EVENTS_SLACK_BACKEND=ap`.
- **Also:** reply-in-thread uses `thread_ts` (captured from the event; a root message starts a thread)
  with per-thread memory; author identity is `ev.user` (per-user), which `/link`s to a tenant user.

#### Discord — DIRECT (Gateway WebSocket), the default — [setup/DISCORD.md](setup/DISCORD.md)
- **Setup:** dev portal → bot → `DISCORD_BOT_TOKEN`; **enable MESSAGE CONTENT INTENT**
  (Bot → Privileged Gateway Intents); invite the bot.
- **Pain:** without MESSAGE CONTENT INTENT the Gateway closes with **code 4014** (disallowed intents)
  / delivers empty content. The Gateway is instant and needs **no public URL** (outbound WS). AP's
  path polls (~5 min) behind `EVENTS_DISCORD_BACKEND=ap`.

### Integrations

#### GitHub — token (PAT) — [setup/GITHUB.md](setup/GITHUB.md)
- **Setup:** GitHub → Developer settings → PAT → `GITHUB_TOKEN` [USER] in `.env` → **auto-connected
  on startup** ("set in .env == connected"). Driving agent: `pr_reviewer`.
- **Pain:** a **fine-grained** PAT (`github_pat_…`) can't create repos / may be read-scoped — fine for
  reading PRs (which is `pr_reviewer`'s job) but not for creating them. The PR trigger needs you to
  **name the repo** (`when a PR opens on owner/repo…`) — the concierge can't guess it.

#### Box — AP OAuth by default; direct-poll opt-in — [setup/BOX.md](setup/BOX.md)
- **Setup (direct opt-in):** `EVENTS_BOX_BACKEND=direct` + `BOX_DEV_TOKEN` [USER]; poll via
  `POST /api/events/box/poll`. Driving agent: `resume_judge`.
- **Pain:** dev tokens **expire ~60 min** (regenerate constantly). A free/personal Box **can't save a
  redirect URI** (Custom App OAuth config), so the AP-OAuth path needs a **paid/dev Box app** — the
  direct poll sidesteps that. Open gap: the watcher passes the file *name*, not its *content*.

#### Gmail — AP OAuth (consent + refresh) — the painful one — [setup/GMAIL.md](setup/GMAIL.md)
- **Setup:** Google Cloud OAuth client → `EVENTS_OAUTH_GMAIL_CLIENT_ID/_SECRET` [TENANT] → per-user
  consent via the Studio wizard. Scope is `gmail.modify` (Google's "restricted" tier). Set
  `access_type=offline` + `prompt=consent` to get a refresh token (both are set). Driving agents:
  `mailbot`, `resume_judge` (email delivery). **Enable the Gmail API** (APIs & Services → Library →
  Gmail API → Enable) or every call 403s.
- **The saga, in the order you'll hit the errors:**
  1. `redirect_uri_mismatch` — the redirect URI wasn't registered on the client. Google compares
     **character-for-character**: register EXACTLY `<EVENTS_PUBLIC_URL>/api/events/connect/gmail/callback`
     (https, **no trailing slash**) — on the **same client** whose id is in `.env` (the `client_id`
     changed twice as new clients were made).
  2. **"No Authorized redirect URIs field"** — the client was a **Desktop** type. Only a **Web
     application** client has redirect URIs. Recreate it as **Web application**.
  3. `deleted_client` — the client referenced by `.env` had been deleted. Point `.env` at a live
     client and restart.
  4. `access_denied` (403, "app being tested") — the consenting account wasn't a **Test user**. Add
     it under OAuth consent screen → Test users.
  5. Consent screen shows **"unverified app"** → Advanced → Go to \<app\> (unsafe) → Allow (expected
     in Testing mode). Reminder: Testing-mode refresh tokens **expire after 7 days** — re-Connect
     weekly (see §2).
- **Lesson:** Gmail = a **Web-application** OAuth client + the exact redirect URI + your email as a
  **test user** + Gmail API enabled. Nothing is a static token — the login is a consent.

#### Webhook — DIRECT (no AP) — [setup/WEBHOOK.md](setup/WEBHOOK.md)
`POST /api/events/hook/<name>` → renders the payload → `incident_triage` (or any agent) → optional
channel delivery. No AP, no OAuth. Use for monitoring alerts / CI failures / form leads.

---

## Testing

- **Offline suite:** `make test` (= `pytest tests/events/`, no network / AP / LLM); `make test-all`
  runs all offline tests (`tests/events` + `tests/unit`). The full product suite (`pytest tests`,
  with browser/pgvector/e2e) needs a complete dev env and isn't the events gate.
- **Live e2e:** `tests/events/live_integrations_e2e.py` (4 trigger modes + integrations), plus the
  per-connector `live_*_check.py`. Set `GATEWAY_TOKEN` + `EVENTS_SERVER_URL`. Check creds first with
  `make doctor`.

See [TESTING.md](TESTING.md) for the full testing guide.
