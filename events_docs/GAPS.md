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

## Closed (2026-07-13) — the trigger registry

The layer used to support exactly ONE trigger per integration. `ap_engine.create_push_flow` accepted
an `event` argument and **ignored it** for every known app, so `gmail/new_attachment` silently armed
the new-email trigger and a second trigger on one app could not exist. Trigger knowledge was
scattered across five files, and an unknown source fell back to a nonexistent `new_item` trigger —
arming a flow that could never publish.

[`events/triggers.py`](../src/cuga/backend/events/triggers.py) is now the single source of truth: one
row per `(app, event)` carrying its piece trigger, payload map, required slots, classifier phrases, a
synthetic fire payload and its provider delivery header. `flows`, `ap_engine`, `classify`, the
concierge's validation gate, `envelope.EVENT_KINDS`, the docs and the tests all *derive* from it, and
an unknown trigger now raises instead of arming something broken. **33 triggers across 7 integrations
are armable; all 14 GitHub triggers are verified live (armed + fired against a real repo).**

Fixed in the same pass, each of which silently corrupted behaviour:
- **Flow-name collision** — push flows were named `push-{source}-{agent}`, and AP flow creation
  deletes any same-named flow. Arming a *second* trigger on one app therefore **destroyed the first
  one's flow**. Names (and the dedup key) now include the event.
- **AP sign-in storm** → false "CONNECT NEEDED". The engine re-authenticated on *every* operation, so
  a burst of arms produced hundreds of sign-ins; AP slowed, the connection check threw, and a bare
  `except` reported an already-connected account as *"connect your credentials"*. The JWT is now
  cached under a lock, `_connections` **raises instead of returning `[]`**, and the gate reports an
  AP outage as an AP outage.
- **Two Activepieces piece bugs**, found by reading `piece-github@0.8.5`'s bundled source: its
  `new_release` trigger only accepts `action: "created"` while its own sample data ships
  `"published"`; its `new_commit` trigger keeps only commits with `distinct: true`, which its sample
  omits. A payload copied faithfully from either sample is **silently discarded** — no run, no error.
  Both are pinned by tests.

## Known gaps (deferred, with the plan)

- **Single-agent world caveats** (shipped 2026-07-15, [plans/SUPERVISOR_REFACTOR.md](plans/SUPERVISOR_REFACTOR.md)):
  supervisor delegates run on a FIXED upstream thread per sub-agent (shared memory across users —
  wrong for per-user credentialed work); every wake-up costs one supervisor inference (~3–10s,
  14/14 accuracy on the gated bench); `test-suite-now`/`test-matrix`/`test-fire` still assert
  fleet-era per-agent semantics and need rework. Full honest list: ROADMAP.md §"Not yet fully vetted".

- **NL→flow rigor** — largely closed: a typed **FlowSpec** (`events/flowspec.py`) with a
  deterministic **pre-router** (a high-confidence utterance arms without the LLM; a missing
  required slot becomes a question the next message answers — *ask-till-legit*), the registry
  **validation gate** before anything is built, and a **47-case labelled benchmark** in CI gated
  on zero-wrong-at-high (see [nl_to_flow.html](nl_to_flow.html) + TESTING.md). Still missing: the
  same benchmark scored against the *LLM* seam (structured FlowSpec output) + a concierge model
  bake-off. Branching/ROUTER flows are designed, not built.
- **Webhook-OUT and email delivery sink** — remaining P3 sinks.
- **Gmail's triggers cannot be fired by machine — by design.** They are POLLING triggers, and
  Activepieces will not run a polling trigger out of band. `POST /subscriptions/{id}/run` fires
  schedule and *webhook* triggers (all 14 GitHub ones), but Gmail needs a real email. Arm-verified
  only; this is a property of Activepieces, not a defect to fix.
- **Slack/Discord watchers need their transport enabled.** They arm as CUGA-owned direct
  subscriptions (`ap_flow_id = NULL`, no AP flow), but the Slack app must be *subscribed* to each
  event type, and Discord member events need the privileged Server Members intent
  (`EVENTS_DISCORD_MEMBERS_INTENT=1` **after** enabling it in the dev portal — requesting it
  unapproved closes the whole gateway with 4014).
- **Some agents fabricate when they have no data source.** `support_digest` (no ticket source) invents
  a digest ~5 runs in 7; `mailbot` has no Gmail *tool* (Gmail is an integration, so it correctly says
  it can't reach the inbox on demand). Surfaced as XFAIL/XPASS in the suite.
- **The concierge trusts thread memory over the store** — after a subscription is deleted, a stale
  thread may still answer "already set up." Use a fresh `thread_id`.

## Security posture

**Fixed 2026-07-13 — the OAuth `state` was forgeable.** The connect callback is unauthenticated and
derived the principal from a **plain-base64, unsigned** `state`, so a crafted callback could bind a
freshly-authorized credential into *someone else's scope* (connection hijack), and its `ret` field was
an open redirect. `state` is now **HMAC-signed and expiring** (15 min); a state that fails
verification is a hard reject — never a silent fallback to a header principal — and `ret` accepts only
same-origin targets. Pinned by `test_oauth_state_signature_roundtrip_tamper_and_expiry`.

**Credentials now resolve through a seam** ([`events/secret_seam.py`](../src/cuga/backend/events/secret_seam.py)):
every secret the layer reads (`AP_PASSWORD`, `GATEWAY_TOKEN`, bot tokens, `BOX_DEV_TOKEN`, OAuth
client secrets) goes through CUGA's own secret resolver, so a `.env` value may be either plaintext
(dev, unchanged) **or** a reference — `vault://events/ap_password`, `aws://…`, `db://…`. Only values
containing `://` are dereferenced, so this is fully backward-compatible. **`AP_PASSWORD` is the one to
vault first** — it guards an internet-tunnelled AP admin console. The integration tokens themselves
never need vaulting: AP holds and encrypts them, and the agent never sees one.

Still open:
- **`GATEWAY_TOKEN`, `SLACK_SIGNING_SECRET`, `EVENTS_WEBHOOK_KEY` each protect nothing when unset** —
  `/invoke`, the Slack receiver, and the generic webhook accept *anything* (a wrong key too). The code
  warns loudly at boot but still serves. Fine on localhost; **set them before exposing the server on a
  public URL.** Failing closed instead of open is the right end state.
- **`EVENTS_STATE_KEY`** is derived from `GATEWAY_TOKEN`/`AP_JWT_SECRET` when unset, which is fine for
  one box. A **multi-replica** deployment must set it explicitly, or a consent started on one replica
  will not verify on another.

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
