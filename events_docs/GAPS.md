# Gaps & sharp edges

Known limitations, deliberate deferrals, and the operational edges you *will* hit. The formal design
rationale lives in the [ADRs](decisions/); this is the honest "what's not done / what bites" list.

## Deliberate design decisions (not gaps)

- **Activepieces owns every credential.** The agent never sees a token. This is the security boundary,
  not a limitation. ([ADR-0001](decisions/0001-ap-as-the-event-engine.md),
  [ADR-0006](decisions/0006-auth-connection-model.md))
- **The concierge routes; it never creates agents.** New agents are a builder action, not something an
  utterance can conjure. ([ADR-0005](decisions/0005-runtime-router-over-prebuilt-agents.md))
- **Slack/Discord/Box are direct backends** (no AP) by choice — instant, no public URL needed for
  Discord, and sidestepping AP's OAuth wall for Box. ([ADR-0008](decisions/0008-direct-backends-for-channels.md))

## Known gaps (deferred, with the plan)

- **NL→flow rigor** *(the strategic gap)* — no typed FlowSpec, no validation gate before arming, no
  labeled benchmark in CI. Branching/ROUTER flows are designed, not built. See [PHASES.md](PHASES.md)
  and [ROADMAP.md](ROADMAP.md).
- **Webhook-OUT and email delivery sink** — remaining P3 sinks.
- **Gmail/Box polling triggers can't be fired on demand.** `POST /subscriptions/{id}/run` works for
  schedule and GitHub (webhook) triggers, but Activepieces will not run an app-polling trigger out of
  band. Only a real inbound event proves those (`live_gmail_e2e.py`, `live_box_e2e.py`).
- **Some agents fabricate when they have no data source.** `support_digest` (no ticket source) invents
  a digest ~5 runs in 7; `mailbot` has no Gmail *tool* (Gmail is an integration, so it correctly says
  it can't reach the inbox on demand). Surfaced as XFAIL/XPASS in the suite.
- **The concierge trusts thread memory over the store** — after a subscription is deleted, a stale
  thread may still answer "already set up." Use a fresh `thread_id`.

## Security posture

- **`GATEWAY_TOKEN`, `SLACK_SIGNING_SECRET`, `EVENTS_WEBHOOK_KEY` each protect nothing when unset** —
  `/invoke`, the Slack receiver, and the generic webhook accept *anything* (a wrong key too). Fine on
  localhost; set them before exposing the server on a public URL.
- **`AP_PASSWORD` is plaintext in `.env`** and guards an internet-tunnelled AP admin console. Vault the
  `AP_PASSWORD`/`AP_ENCRYPTION_KEY`, not the integration tokens (AP already encrypts those).

## Operational sharp edges (biggest first)

### The ephemeral tunnel — the #1 pain
Activepieces' public URL is a **cloudflared quick tunnel**, which is ephemeral and dies after a while.
When it dies, AP can't call back its own payload server and **every flow fails with `INTERNAL_ERROR`**
— which looks exactly like a code regression. Diagnose with `make tunnels`; fix with `make ap` (fresh
tunnel, connections survive). There are two tunnels: CUGA's (ngrok, ideally a stable reserved domain
via `EVENTS_NGROK_DOMAIN`) and AP's (cloudflared). Pinning CUGA's URL is strongly recommended so you
never re-point Slack/Gmail callbacks.

### The server caches `.env` at startup
Edit `.env` → `make reload` (bounces CUGA only, keeps AP + tunnels). `reload` ≠ `restart` (restart
gives new tunnel URLs).

### Short-lived tokens expire
`BOX_DEV_TOKEN` lasts ~60 min; a Gmail refresh token from a "Testing"-mode OAuth app expires after
7 days. When Box 401s or Gmail stops, refresh the token, not the code.

### `make nuke` / `make fresh` wipe AP volumes
That loses **all** integration *connections* (they must be reconnected). To reset just your armed
flows, use `make reset-flows` — it wipes only `events.db`, keeps AP connections/pieces/tunnel.

### A fresh AP must install its pieces
`make up` force-installs the needed pieces after boot. If a Connect 404s with
`piece_metadata_not_found`, run `make ap-pieces` (idempotent), then restart. `make doctor` shows status.

## Recently fixed (so nobody re-diagnoses them)

- **GitHub "connect your credentials" / `401 Bad credentials`** — `piece-github` accepts only OAUTH2,
  never a pasted PAT; GitHub is now an OAuth connector, and `connect/github/token` refuses a PAT with
  a clear 400. `ensure_secret_connection`/`ensure_oauth_connection` now update on rotation instead of
  no-op'ing.
- **Box watcher only saw the filename** — the server-side download step now hands the agent file
  content (text inlined, binary as base64) plus a job description.
- **Dangling subscriptions** — endpoints now check the AP flow actually exists (`ap_flow != null`),
  not just that an `ap_flow_id` is stored.
