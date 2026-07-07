# Known gaps, design decisions & issues

The honest reviewer's list. Nothing here is a surprise — it's the set of conscious trade-offs, the
things deferred, and the sharp edges, each with *why*. If you're reviewing this PR, start here.

Grouped: **A. Design decisions** (deliberate, documented) · **B. Known gaps** (deferred, with the
plan) · **C. Security posture** (what's guarded, what isn't, and the assumption) · **D. Operational
sharp edges** (see also [OPERATIONS.md](OPERATIONS.md)).

---

## A. Design decisions (deliberate)

These are choices, not bugs. Each has an ADR.

1. **Opt-in, additive, non-breaking.** The whole layer is behind `EVENTS_ENABLED`; startup is wrapped
   so a failure never blocks vanilla CUGA. No core CUGA file changes behavior when the flag is off.
   → [ARCHITECTURE.md](ARCHITECTURE.md).
2. **Activepieces owns integration triggers + the clock; channels are direct.** The division and the
   reasons (AP's OAuth wall, the empty-payload Slack piece, poll latency) →
   [decisions/0008](decisions/0008-direct-backends-for-channels.md), amending
   [0001](decisions/0001-ap-as-the-event-engine.md).
3. **The concierge is a runtime *router*, not an agent factory.** It selects among builder-defined
   agents (answer-now / reuse-or-create-flow / decline); it never invents tools or agents.
   → [decisions/0005](decisions/0005-runtime-router-over-prebuilt-agents.md).
4. **Two isolation grains:** agent *definitions* are tenant-shared (`agent_scope`), run-state
   (threads/memory/connections) is per-user (`scope`). → [decisions/0002](decisions/0002-tenancy-and-isolation.md).
5. **Per-user identity through a shared bot** via the message author (`source.user`) + an
   account-linking handshake. → [decisions/0007](decisions/0007-identity-profiles-permissions.md).
6. **No silent ReAct fallback on the CUGA worker.** If a CUGA worker can't run, `/invoke` fails loud
   unless `EVENTS_CUGA_FALLBACK_REACT=1` — so a misconfigured worker is visible, not silently
   downgraded.
7. **AP project auto-degrade.** On a plan that caps projects, the engine falls back to the default
   project with scope-prefixed flow names (isolation becomes app-layer filtering, logged). This keeps
   Community-Edition AP working; strict per-tenant projects need Enterprise AP.

---

## B. Known gaps (deferred, with the plan)

| Gap | Impact | Plan |
|---|---|---|
| **Gmail PUSH fire leg** | The Gmail OAuth connection is live and the inbox-watcher flow arms, but a real *fire* needs an email sent to the connected inbox. `live_gmail_e2e.py` proves connection + arm, not the fire. | Manual: send to the connected account. The trigger is AP-native and identical to Box's proven push path. |
| **Box watcher passes file *name*, not *content*** | `resume_judge` judges on the filename + metadata, not the file body. | Add a Box download step (AP action or direct fetch) before `/invoke`. |
| **GitHub PR trigger needs the repo named** | The concierge can't guess `owner/repo`; the user must say it (*"when a PR opens on psf/requests…"*). | A repo-picker in the arm step; or default to the connected user's repos. |
| **Fine-grained PAT can't create repos** | `live_github_e2e.py` is read-only (fetches a real public PR) rather than creating one. | Use a classic PAT for write-path tests, or a dedicated test org. |
| **Telegram had no dedicated live e2e** | It was only exercised indirectly. | Added `live_telegram_check.py` (this PR); mirrors the Slack check. |
| **`instance_id` is effectively always `default`** | `agent_scope` collapses to `tenant/default`; instance-level isolation is nominal. | Add an `EVENTS_INSTANCE_ID` fallback in `principal.resolve` when instances are needed. |
| **Two flow-builder code paths** | `flows.py` (pure JSON, for dry-run/tests) and `ap_engine.py` (live REST) render the same flow shapes independently → drift risk. | Have `ap_engine` consume `flows.build_*` as the single source, or a shared-shape test. |
| **`connectors.py` still lists `outlook` as planned** | Intentional — it's the one genuinely-unbuilt connector, kept to show the shape. | Build when M365/Graph is prioritized. |

---

## C. Security posture

What a deployment must understand before exposing this beyond localhost.

- **The `/invoke` seam trusts a caller-asserted `scope`.** `/invoke` (and the connect/admin
  endpoints) become whatever `Principal` the caller names, guarded only by the **gateway token**
  (`X-Gateway-Token`). This is by design — `/invoke` is the *internal* AP→CUGA seam, not a public
  API — but it means **real isolation depends on an upstream auth layer + a secret gateway token**.
  Signed scope claims / an IdP in front is the hardening path. *This is the #1 item to fix before a
  shared multi-tenant deploy.*
- **Empty `GATEWAY_TOKEN` = open seam.** With no token set, `/invoke` and the poll/webhook endpoints
  are unauthenticated. We now **warn loudly at startup** (`app.py`); fine for local dev, unsafe when
  exposed. Set it.
- **Webhook key gate** (`/api/events/hook/<name>`) is enforced **only if** `EVENTS_WEBHOOK_KEY` is
  set, and now uses a **constant-time compare**. Unset → the endpoint is open (documented; set the
  key in production).
- **OAuth `state` is unsigned** (base64-JSON, trusted on callback). A crafted `state` could steer a
  connection's externalId. Sign/nonce it before public OAuth. Tracked here, not yet fixed.
- **Slack signature verification** is on when `SLACK_SIGNING_SECRET` is set; if unset the receiver
  accepts unverified events (returns `unverified`). Set the secret in production.
- **`_is_admin` returns True when there is no user store** (dev convenience). A production deploy
  must run with the user store enabled.
- **Envelope validation** now runs on every `/invoke` (malformed envelopes → 400) — closed this PR.

---

## D. Operational sharp edges

The full warts-and-all account is in [OPERATIONS.md](OPERATIONS.md). The recurring ones:

- **The quick-tunnel URL is ephemeral.** Every restart of `cloudflared` invalidates
  `EVENTS_PUBLIC_URL`, the Slack Events URL, the OAuth redirect URIs, and AP's `AP_FRONTEND_URL`. A
  named tunnel / real domain ends this. Direct Discord + Box poll (outbound only) don't care.
- **The server caches `.env` at startup** — restart after any `.env` change.
- **Do NOT set `AP_WORKER_TOKEN`** — AP 0.82's entrypoint mints it as a JWT; a random value
  crash-loops the worker.
- **Box dev tokens expire ~60 min** — regenerate for direct-poll tests.
- **Gmail (Testing mode) refresh tokens expire after 7 days** — re-Connect weekly.
- **Run AP Community Edition on Postgres** — the single-container sqlite build wipes its own project.

---

## Fixed in this PR (was stale / broken)

- `ap_engine` project-degrade flag typo (`_project_degraded` → `_degraded`) — the degrade-once
  optimization now actually latches.
- `connectors.py` stale `live:false` / "Phase 3" flags — Slack/Discord are direct-live, integrations
  are AP-live; only `outlook` remains planned.
- `/invoke` now validates the envelope and warns on an empty gateway token; webhook key compare is
  constant-time.
- Docs reconciled: one status table (README), the "Phase 1 & 2" framing retired, obsolete
  `EXAMPLES_CONFORMANCE.md` / `PHASE_1_2_ACCOMPLISHMENTS.md` removed, test counts reconciled to the
  real **61 offline**.
