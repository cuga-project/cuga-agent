# Gotchas — the sharp edges (and how to get past them)

Hard-won lessons from wiring the event-driven layer. Grouped by area. If something "just doesn't
work," scan here first.

## Google / Gmail OAuth
- **Redirect URI must match EXACTLY.** Google compares character-for-character. It must be
  `<EVENTS_PUBLIC_URL>/api/events/connect/gmail/callback` — https, no trailing slash. A mismatch →
  `redirect_uri_mismatch`.
- **Enable the Gmail API.** *APIs & Services → Library → Gmail API → Enable.* Without it the token is
  issued but every call 403s.
- **Scope is `gmail.modify`** (what our `oauth.py` requests) — Google's "restricted" tier. In
  **Testing** mode you (as an added **test user**) can consent despite the "unverified app" warning
  (**Advanced → Go to \<app\> (unsafe)**). Publishing to Production triggers a weeks-long verification.
- **Testing-mode refresh tokens expire after 7 days** (Google rule for sensitive scopes). Fine for a
  demo — re-Connect weekly; publishing removes it.
- **`access_type=offline` + `prompt=consent`** are required to get a refresh token (we set both).

## Activepieces (AP)
- **The OAuth2 wall.** AP's `UpsertOAuth2Request` REQUIRES the authorization **code** and does the
  token exchange itself — it **refuses a pre-obtained access/dev/bot token**. So OAuth integrations
  (Box/Gmail/Slack-via-AP) MUST go through the consent flow; you can't paste a token. (This is *the*
  reason Slack + Box got direct backends.)
- **Do NOT set `AP_WORKER_TOKEN`.** AP 0.82's entrypoint mints it as a JWT signed with `AP_JWT_SECRET`
  when unset. A random string → the worker crash-loops on Socket.IO "Authentication error" and every
  channel/schedule publish hangs ~30s. `.ap.env` holds ONLY `AP_ENCRYPTION_KEY` + `AP_JWT_SECRET`.
- **`AP_POSTGRES_PORT=5432` is required** — omitting it → `SYSTEM_PROP_NOT_DEFINED`.
- **AP needs a public `AP_FRONTEND_URL`** for channel webhooks (Telegram/Slack setWebhook demand
  HTTPS). `scripts/ap_up.sh` sets it to the tunnel.
- **`activepieces:latest` is NOT all-in-one** — it needs external postgres + redis + full env.
- **The Slack piece bug (parked):** AP 0.82 + slack-piece 0.17.2 `new-message` trigger emits `[]`
  even for a clean event → no flow-run. Diagnosed as a payload-shape mismatch → we went direct.

## Tunnels / public URL
- **Cloudflare quick-tunnels are ephemeral** — the URL changes every `cloudflared` restart. When it
  does, **all** of these go stale: `EVENTS_PUBLIC_URL`, Slack Event-Subscriptions URL, Google/Box
  OAuth redirect URIs, AP's `AP_FRONTEND_URL`, any registered webhooks. Use a **named tunnel or a
  real domain** for anything you don't want to re-point. Direct **Discord (gateway)** and **Box poll**
  need **no** public URL (outbound only) — the most tunnel-resilient.

## Box
- **Dev tokens expire after ~60 min.** `preflight.py box` / a 401 from `users/me` = regenerate it in
  the Box console. (This is why `BOX_DEV_TOKEN` is tagged USER, not a durable cred.)
- **Free/personal Box can't save a redirect URI** (Custom App OAuth config) — the reason the AP Box
  OAuth path needs a paid/dev Box app. The **direct poll** (`EVENTS_BOX_BACKEND=direct`) sidesteps it.

## Slack
- **`SLACK_SIGNING_SECRET` recommended** — without it CUGA accepts events but flags them "unverified".
- **Reply-in-thread** needs `thread_ts` (captured from the event); a root message starts a thread.
- **Author identity** is `ev.user` (per-user); a Slack user `/link`s to a tenant user.

## Discord
- **MESSAGE CONTENT INTENT** must be ON (Developer Portal → Bot → Privileged Gateway Intents) or the
  Gateway closes with code **4014** (disallowed intents) / delivers empty content.
- Direct = a **Gateway WebSocket** (instant, no public URL); AP path (polling ~5 min) behind
  `EVENTS_DISCORD_BACKEND=ap`.

## Identity / tenancy
- **Bot token / OAuth app creds = TENANT** (one per org, in `.env`). **A user login/PAT = USER**
  (per-user, resolved from the incoming message + stored in AP as `ea::<tenant>::<user>::<app>`).
  A shared `.env` doesn't mean one user — the author of each message is who tells Alice from Bob.
- **`EVENTS_USER_ID=admin`** in dev collapses everything to one user; real per-user identity needs the
  channel author + account-linking (Telegram verified; Slack/Discord wired via `source.user`).

## Ports / processes
- Registry :8001 (or :8021 under the `/tmp` launcher — a known discrepancy), AP :8081, CUGA :8100,
  Studio at `http://localhost:8100/studio`. Other repos may hold :8000/:8001/:7860.
- The server caches env at startup — **edit `.env` → restart** to pick up new creds/tokens.
- Frontend is a **pre-built webpack bundle** in `src/cuga/frontend/dist`; rebuild after `.tsx` changes
  via `scripts/frontend_build.sh`.

## Testing
- Offline suite: `uv run pytest tests/events/` (no network/AP/LLM).
- Live e2e: `tests/events/live_integrations_e2e.py` (4 trigger modes + integrations), plus the
  per-connector `live_*_check.py`. Set `GATEWAY_TOKEN` + `EVENTS_SERVER_URL`.
