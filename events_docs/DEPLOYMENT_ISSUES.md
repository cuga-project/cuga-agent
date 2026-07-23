# Multi-User Cloud Deployment — Isolation & Scaling Issues

**Status:** Blocking for multi-user cloud. Single-user / trusted-demo is fine today.
**Scope:** Analysis of whether one deployed CUGA + FastAPI + Activepieces instance can safely serve
multiple real users, each with their own integrations (e.g. my Gmail vs. your Gmail), without data leaking.
**Date:** 2026-07-21

---

## TL;DR

- The **design** for per-user secret isolation is correct: per-user Activepieces connection external-ids
  (`ea::<tenant>::<user>::<app>`), tokens held encrypted in AP where the agent never sees them, a `Principal`
  isolation key threaded through each request, and a channel `IdentityMap` link-token flow.
- The **runtime does not yet honor that design** on the web path, and two shared singletons will actively
  collide under concurrency.
- **Verdict today: UNSAFE for multi-user cloud.** With the current web entry point, two users both resolve to
  `user_id="local"` → they share one AP Gmail connection → one user's agent can act on the other's Gmail token.
- **All issues are fixable.** Four are contained fixes on the events branch; one (ActivityTracker) is an
  upstream CUGA change. After the three critical-path fixes, the design is defensible for multi-user.

**REVISED after deeper tracing (2026-07-22).** On the events `/invoke` path specifically, the ONLY genuine
cross-user *content leak* is **Issue #1 (the `user_id="local"` credential collapse)** — plus box-direct's
by-design single token (#4). The answer, conversation memory, and tools are all isolated per `scope::thread_id`
(coroutine-local answer; per-principal thread). **Issue #3 (ActivityTracker) is telemetry-only on the events
path — trajectory/token-count corruption, not a leak** (its content-leak paths require the browser or the
embedded SDK, which events does not use). Issues #2/#5 are persistence/capacity, not leaks. **Net: fix identity
injection (#1) and the events world does not leak user content.**

---

## Safety rating

| Scenario | Safe? |
|---|---|
| Single user, cloud | ✅ Yes |
| Trusted demo (people who accept shared state) | ⚠️ Tolerable |
| Multiple untrusted users, web UI, own integrations | ❌ **No — data leaks by default** |
| Channels only (Slack/Telegram), each user link-verified | ⚠️ Mostly OK (tracker concurrency still applies) |
| Multi-user cloud **after** critical-path fixes below | ✅ Design is sound |

---

## Issues

### 1. No `X-User-Id` established on the web path — DOMINANT ISSUE
**Layer:** events (this branch) · **Effort:** small · **Severity:** critical

`Principal.user_id` is resolved as: explicit arg → `X-User-Id` header → env → default `"local"`
(`src/cuga/backend/events/principal.py:67-93`). But there is **no auth middleware** that populates
`X-User-Id` from an authenticated session — `register_events_routes` is wired with no principal/auth
dependency (`src/cuga/backend/server/main.py:1766-1830`). The docstring in `principal.py:70-73` even says
"pass `user_id=current_user.sub`" but that wiring does not exist. The CLI hardcodes `EVENTS_USER_ID=admin`
(`src/cuga/cli/main.py:1161`).

**Consequence:** every browser user collapses to `user_id="local"` → shared scope → shared conversation
memory, shared subscriptions, and **shared AP connection selection**. User B's Gmail/GitHub action runs
against user A's token. It fails silently — it looks like it works.

**Fix:** Add auth middleware that sets the principal from the authenticated `sub`. The isolation machinery
downstream is already correct; it just needs a real identity at the front door.

---

### 2. `EVENTS_DB` defaults to `":memory:"`
**Layer:** events (this branch) · **Effort:** trivial · **Severity:** high (for scaling)

`_ev_db = EVENTS_DB or ":memory:"` (`src/cuga/backend/server/main.py:1775`; read in
`src/cuga/backend/events/app.py:117`). All stores (users, identity map, subscriptions) share this.

**Consequence:** users/identity/subscriptions don't persist across restarts and aren't shared across replicas.
Cannot horizontally scale the FastAPI host; a restart forgets all account links.

**Fix:** point `EVENTS_DB` at a durable shared database (Postgres, or at minimum a persistent SQLite file on
shared storage). Required before running more than one replica.

---

### 3. ActivityTracker is a process-global singleton — CONCURRENCY DATA BLEED
**Layer:** CORE CUGA (upstream, not this branch) · **Effort:** larger · **Severity:** critical under concurrency

`src/cuga/backend/activity_tracker/tracker.py:63-94` is a `__new__`-cached singleton with **class-level
mutable state** (`intent`, `steps`, `prompts`, `images`, `token_usage`, `score`, `tools`). One instance,
one set of state, for the whole process. It was built for **benchmark/eval runs** (`start_experiment`,
`finish_task`, `results.csv`, `merge_experiments`) — one task at a time, one process. In that context the
singleton is fine.

The events layer sets `tracker.intent = text` per run at `src/cuga/backend/events/_cuga_bridge.py:88-89`,
and ClassicRuntime forces every run under `DEFAULT_SCOPE` (`src/cuga/backend/events/runtime.py:353-359`),
sharing one graph object.

**Consequence — worse than "corrupted logs".** Most tracker fields ARE write-only telemetry (`intent`,
`prompts`, `token_usage`, `user_id`) — clobbering those only corrupts trajectory files / token counts. BUT
three fields are read *back into* live runs, making concurrency a genuine cross-user **content leak**, not just
an observability bug (verified 2026-07-22):
- `tracker.images` → a node reads `images[-1]` and sends it as the multimodal `img` to the vision model
  (`plan_controller_agent.py:70-71`+89, `browser_planner_agent.py:80`+87 — which already carries a "singleton,
  no lock" race comment). User A's screenshot can enter User B's vision prompt.
- `tracker.tools` / `tracker.apps` → the exposed AND executed tool set is read from the singleton
  (`invoke_tool` `tracker.py:96`; `api_utils.py:62,131,171`). A per-run `set_tools()` overwrite means User B can
  be exposed to and actually execute User A's tools/connections — capability bleed.
- `tracker.final_answer` → the delivered answer round-trips through the singleton between set and read
  (`controller.py:242→267/275`). A concurrent overwrite can deliver User A's answer to User B.

The trap is that it is *named* a tracker but is overloaded to also be the tool registry and answer-passing
channel — on the BROWSER path (`images`) and the embedded-SDK path (`set_tools`).

**CORRECTION for the events path (traced 2026-07-22):** the events `/invoke` runtime does NOT exercise any of
those leak paths. `_cuga_bridge.run_graph` builds a FRESH `ActivityTracker()` per run and only *writes*
`intent` to it; the delivered answer is a coroutine-local variable from the streamed `event.answer`
(`_cuga_bridge.run_graph`; also `runtime.py:322`, `concierge.py:1209`), never read back from the tracker; and
`set_tools` is never called on the events path (tools come from the registry). So **in the events world the
ActivityTracker is telemetry-only — trajectory/token-count corruption under concurrency, NOT a content leak.**
The `images`/`final_answer`/`set_tools` leaks require the browser or the embedded SDK, which the events layer
does not use. This issue therefore drops from "content leak" to "observability/billing corruption" for events
deployments — real, but not a breach. (It remains a genuine leak for anyone running core CUGA WITH the browser;
see the core-CUGA analysis.)

**Why latent:** the bug has existed ~10 months but never bit because CUGA has been single-session (benchmark
harness / CLI / one demo user). The events layer is the first thing that invites concurrent multi-user
traffic into one process — it removes the assumption that hid the bug.

**Fix (upstream):** per-run isolation instead of process-global state — and crucially it is NOT enough to fix
logging; the read-into-run fields (`images`, `tools`, `apps`, `final_answer`) must become per-run. Preferred:
store the active tracker / its mutable state in a `contextvars.ContextVar` (matches how the events layer already
threads `_principal`/`runmeta`). Alternatives: make the tracker a real per-run object passed down the graph, or a
`dict[thread_id → Tracker]` registry. Better still, separate concerns: telemetry can stay a sink, but the tool
registry and answer channel should not live on a shared singleton at all. Pervasive call sites across
`cuga_graph/nodes/*`, `sdk.py`, tools registry — genuinely an upstream change, not a branch patch.

---

### 4. Box-direct is single-identity
**Layer:** events (this branch) · **Effort:** moderate · **Severity:** medium (demo feature)

Box direct mode returns one global `BOX_DEV_TOKEN`/`EVENTS_BOX_TOKEN` for all users
(`src/cuga/backend/events/box_direct.py:54-65`), and its watermark is one global file keyed by `folder_id`
only, **not by user/scope** (`box_direct.py:29-51`; advanced globally at
`src/cuga/backend/events/app.py:1230`).

**Consequence:** all users share one Box identity; two users watching the same folder share one `since`
cursor (whoever polls first advances it, the other sees "no new files"). Effectively single-tenant.

**Fix:** route Box through the per-user AP connection scheme like the other integrations, or fence box-direct
explicitly as a demo-only path. Same caution applies to any "direct" shared-token backend (e.g. Slack direct
bot token).

---

### 5. Activepieces is one shared instance / admin / project (capacity ceiling)
**Layer:** events (this branch) + infra · **Effort:** infra/topology · **Severity:** medium (scaling, not leak)

`APEngine` uses a single `AP_EMAIL`/`AP_PASSWORD` (or `AP_API_KEY`) and one `AP_PROJECT_ID`
(`src/cuga/backend/events/ap_engine.py:42-53`), caching one admin JWT for the whole process. Project
isolation grain is `EVENTS_AP_PROJECT_GRAIN` (default `tenant`). **Auto-degrade trap:** if the AP plan caps
projects (Community Edition = 1 project → HTTP 402 / `FEATURE_DISABLED`), `ensure_project()` sets
`_degraded=True` and routes every principal's flows into the single default project
(`ap_engine.py:372-404`).

**Consequence:** on self-hosted CE, "per-tenant projects" silently collapse to one shared project. Credential
selection is still safe (connections stay per-user by external-id), but all tenants' flows live in one project
under one admin JWT — a single throttle point and SPOF. 100 users × 6 integrations ≈ 600 AP flows all on one
instance.

**Fix (topology, not code):** AP Enterprise (multi-project), or per-tenant AP instances, and vault the
`AP_PASSWORD`. Not a data-leak bug — a capacity/robustness ceiling. Log the degrade instead of silently
collapsing.

---

## What already holds up per-user (do not rebuild)

- `Principal` / `credentials` per-user connection external-id scheme (`ea::<tenant>::<user>::<app>`) —
  correct, conditional on a correct `user_id`.
- Channel `IdentityMap` link-token flow (`/start <token>` / `/link <token>`) — real per-user identity on
  shared bots, after each user links.
- Contextvar threading of `_principal` / `runmeta` / `_origin` / `_utterance` through a single request —
  correct.
- AP flow bodies frozen with the arming user's scope at arm time — correct.
- Tokens held encrypted in AP; the agent never sees raw credentials.

---

## Can an admin see the credentials?

Short answer: **through CUGA, no. Through the Activepieces deployment, ultimately yes** — and in the current
single-shared-AP topology the CUGA admin and the AP admin are the same person, so effectively a determined
admin *can*, just not via the CUGA product surface.

**What CUGA exposes — metadata only, never the token:**
- Secrets flow only *inward*. CUGA writes tokens into AP and never reads a secret value back. The one path that
  reads connections (`src/cuga/backend/events/ap_engine.py:710-725`) is consumed only by `connection_exists()`
  (compares `externalId`) and `list_connections()` (projects to `{id, externalId}` and discards the rest). No
  `get_connection`, no `decrypt`, no reading of `access_token`/`secret_text` anywhere.
- No endpoint dumps credentials. The only connection-facing route is `GET /api/events/integrations`
  (`app.py:769-785`) — connected/not-connected badges. No `/credentials`, no admin dump. Studio UI is status-only.
- Tokens are never logged (log lines carry externalId/scope/app ids only).
- So an admin sees: **which user connected which app** (externalId encodes `ea::<tenant>::<user>::<app>`),
  connected/not-connected status, project names, per-user vs shared ownership. **Never the token.**

**The caveat — the token isn't invisible, it's just not exposed by CUGA:**
- The deployment holds one shared `AP_EMAIL`/`AP_PASSWORD`. AP's connection API returns values
  encrypted/redacted (plaintext is only injected by AP's worker at flow-run time), so the UI won't hand it over.
- But whoever can reach **AP's Postgres + AP's `AP_ENCRYPTION_KEY`** (which lives in AP's own deployment config,
  not this codebase) can decrypt stored tokens. That is an Activepieces-level capability, outside CUGA's control.
- **Precise statement:** CUGA gives an admin no path to the raw credentials; whoever controls the Activepieces
  deployment ultimately can recover them.

## Making the tokens genuinely safe — AP deployment hardening

The weak link is not CUGA leaking tokens — it is that **one plaintext `AP_PASSWORD` on a publicly-tunneled AP is
the single key to the whole vault**. Three escalating deployment options:

1. **Managed Activepieces Cloud** — AP hosts; tokens live in their infra under their KMS, never on your box. You
   hold only an API key to *drive* AP, not the vault. Least burden; a trust/data-residency decision.
2. **Self-host AP with managed cloud primitives** (the usual middle path):
   - AP's Postgres → managed DB (RDS / Cloud SQL / Azure DB) with **encryption at rest via KMS** + TLS in transit.
   - `AP_ENCRYPTION_KEY` and `AP_PASSWORD` → a **real secrets manager** (AWS Secrets Manager, GCP Secret Manager,
     Vault), injected at runtime — never in the image, `.env`, or git.
   - **Split custody:** keep `AP_ENCRYPTION_KEY` in a different trust boundary from the DB. A DB snapshot alone is
     ciphertext; the key alone is useless. One leak ≠ compromise.
3. **Envelope encryption / external KMS** — tokens encrypted with a data key wrapped by a cloud KMS/HSM, so
   decryption is a live, auditable, revocable KMS call. This is what "not even the infra admin can casually read
   tokens" actually requires.

**"Lock down AP's network exposure" means** (today AP is publicly tunneled on :8081, guarded only by the plaintext
`AP_PASSWORD`):
- Put AP on a **private network/VPC**; CUGA reaches it over *internal* networking; the admin API/UI is **not**
  publicly routable.
- Expose only what must be public — **OAuth callback + webhook ingress** — through a reverse proxy / API gateway,
  admin surface excluded. Not "tunnel the whole thing."
- Bind the admin UI to a private subnet / behind VPN or SSO, not `0.0.0.0`.
- TLS everywhere + firewall/security groups restricting inbound to known sources.

**"Lock down DB access" means:**
- AP's Postgres **never publicly reachable** — private subnet, security group admitting only the AP app.
- **Least-privilege DB user** (not a shared superuser), unique rotated password in the secrets manager.
- Encryption at rest (KMS) + in transit (TLS); **encrypted backups** with restricted snapshot-read perms — a DB
  snapshot *plus* the key = plaintext tokens.
- **Separate who can read the DB from who holds `AP_ENCRYPTION_KEY`** (split custody again).

The through-line: replace "one plaintext password on a public tunnel" with a design where token recovery requires
*multiple* independently-guarded compromises.

## Deploying on IBM Code Engine (private endpoints)

Can the AP hardening above be achieved on IBM Code Engine (CE)? **Mostly yes** — CE primitives map cleanly onto
the goals — **with one structural catch: OAuth callbacks + push webhooks force a minimal public surface**, so AP
can't be *fully* private.

Grounding note: today AP is **not** on CE. The CE deployment hosts the `cuga-agent-apps` gallery image; AP runs
locally under podman behind a **cloudflared tunnel that exposes everything** — which is the actual problem. This
section is "if we moved AP onto CE."

**CE / IBM Cloud mechanism per hardening goal:**

| Goal | Code Engine mechanism |
|---|---|
| AP admin UI/API off the public internet | Deploy AP as a CE app with `--visibility project` (cluster-local) or private; CUGA (same project) calls it over internal `*.svc.cluster.local`. |
| Private ingress, VPC-only | Virtual Private Endpoint (VPE) gateway — private IP, no public route. |
| Private Postgres | IBM Cloud Databases for PostgreSQL with a **private service endpoint**; CE egress over the private backbone. |
| Encryption at rest / KMS / split custody | Key Protect or Hyper Protect Crypto Services (HPCS) as BYOK/KYOK for ICD *and* to wrap `AP_ENCRYPTION_KEY`. HPCS gives the provider-excluded custody split. |
| Secrets, not plaintext `.env` | IBM Secrets Manager → injected as CE secret refs at runtime; `AP_PASSWORD`/`AP_ENCRYPTION_KEY` never in the image or `.env`. |
| Least-privilege network | CE project + VPC security groups; app-to-app internal calls only. |

**The structural catch — public ingress for OAuth + webhooks.** OAuth redirect URIs (Gmail, Box) and push
webhooks (Telegram, GitHub) require a publicly reachable HTTPS URL — this is why the cloudflared tunnel exists
today. A fully-private AP cannot receive them. The clean CE pattern:

- **AP itself → private** (`visibility project` / VPE): admin UI + connection API not public.
- **A thin public ingress → only the callback/webhook paths.** Either a small second CE app (public visibility)
  that reverse-proxies *only* `/redirect`, `/api/v1/webhooks/*`, and the OAuth callback into the private AP
  service, or an API Gateway / CE domain mapping scoped to those paths. Everything else stays private.
- Result: the internet hits only the two paths that must be public; the vault, admin surface, and DB stay private.
  (The tunnel today exposes *everything* — that's the difference.)

**Always-on caveat:** CE apps are request-driven and scale to zero. Fine for CUGA and the proxy, but **AP's
poll/webhook workers and its Postgres want to be always-on** — run AP as a **min-scale-1** CE app (or a small
always-on deployment), not scale-to-zero.

**Net:** DB + secrets + encryption + split-custody + private admin surface — yes, via ICD + Key Protect/HPCS +
Secrets Manager + CE private visibility/VPE. Fully-private AP — no; design it as a path-scoped public proxy in
front of a private AP.

## Credential isolation, per integration (traced 2026-07-22)

Gmail is not special. Every per-user integration names its Activepieces connection with the SAME formula —
`ea::<tenant>::<user>::<app>` (`principal.py:57`), the `<user>` coming from the shared `Principal` that defaults
to `local` (`principal.py:26-28`). So the "collapse" hits every per-user OAuth app identically. A second,
DIFFERENT problem — single shared bot tokens by design — hits the direct-backend apps. Three apps need no
credential at all.

| Integration | Backend | Isolation today | Problem class | Fixed by identity injection (#1)? |
|---|---|---|---|---|
| Gmail | AP OAuth (per-user) | ❌ shared | **Collapse** | ✅ yes |
| GitHub | AP OAuth (per-user) | ❌ shared | **Collapse** | ✅ yes |
| Google Calendar | AP OAuth (per-user) | ❌ shared | **Collapse** | ✅ yes |
| Pinterest | AP OAuth (per-user) | ❌ shared | **Collapse** | ✅ yes |
| Box (default AP mode) | AP OAuth (per-user) | ❌ shared | **Collapse** | ✅ yes |
| Slack | Direct bot token (`SLACK_BOT_TOKEN`) | ❌ shared | **Single-token-by-design** | ❌ no — needs per-user OAuth install |
| Discord | Direct bot token (`DISCORD_BOT_TOKEN`) | ❌ shared | **Single-token-by-design** | ❌ no |
| Telegram | Direct bot token (`TELEGRAM_BOT_TOKEN`) | ❌ shared | **Single-token-by-design** | ❌ no |
| Box (direct mode) | Direct dev token (`BOX_DEV_TOKEN`) | ❌ shared | **Single-token-by-design** | ❌ no |
| YouTube | AP, public feed (`auth:none`) | ✅ n/a | No credential | — |
| RSS | AP, public feed (`auth:none`) | ✅ n/a | No credential | — |
| Webhook | Direct inbound endpoint | ✅ n/a | No credential | — |

Evidence: externalId formula `credentials.py:49` → `principal.py:53-57`; ownership default per-user
(`credentials.py:29`, `.get("ownership","per-user")` at `concierge.py:351,613,720,955`); direct tokens
`slack_direct.py:33`, `discord_direct.py:47`, `box_direct.py:61`, `connectors.py:33`; no-cred
`connectors.py:57-59`.

**Two problems, two fixes:**
- **Collapse (5 OAuth apps)** — ONE root cause (shared principal), so ONE fix (inject real `user_id`) isolates
  all five at once. They are not five bugs; they are one bug wearing five app names.
- **Single-token-by-design (4 direct apps)** — a shared team bot in one workspace. Often *correct* (it's one
  team's bot); a leak only if different users are meant to have different Slack/Discord/Box identities. Making
  these per-user needs per-user OAuth installs — a feature, not a config fix. Identity injection does NOT touch
  them.

---

## Remediation plan

The finish line: **your Gmail (and GitHub, Calendar, Pinterest, Box) and another user's become physically
distinct encrypted AP connections — `ea::t::alice::gmail` vs `ea::t::bob::gmail` — selected per authenticated
request; and no user's answer, memory, or tools cross over.** Sequenced by leverage.

### Phase 0 — Hold the line (now)
Until Phase 1 ships, deploy **single-user or trusted-demo only**. Document it. Do not point untrusted
multi-user traffic at a shared instance — the collapse leaks credentials silently.

### Phase 1 — Close the leak: identity injection ← THE critical fix
The single highest-leverage change. Everything else is correctness/hardening, not the leak.
- Add a FastAPI auth dependency/middleware on the events routes that authenticates the request (session cookie /
  JWT / OAuth) and sets `Principal.user_id` from the authenticated subject (`current_user.sub`) — the docstring
  at `principal.py:70-73` already describes this; it just isn't wired (`main.py:1766-1830` has no auth dep).
- Both the CONNECT flow (store) and the ARM flow (read) derive the externalId from the same principal, so fixing
  the principal fixes both halves at once — all 5 collapse apps isolate together.
- Confirm the web UI actually logs a user in and forwards identity; the channel path already isolates via the
  IdentityMap link-token flow.
- **Exit test (write it):** a two-user concurrency leak test — connect two different Gmail accounts as two
  authenticated users, fire both flows concurrently, assert each acts only on its own inbox and the externalIds
  differ. This is the regression guard the whole story hinges on.

### Phase 2 — Persistence & horizontal scale
- Point `EVENTS_DB` at a durable shared database (not `:memory:`) so identity map / subscriptions persist and
  are shared across replicas.
- For >1 instance: either sticky sessions, or swap the in-process `MemorySaver` for a shared checkpointer so any
  instance can serve any `thread_id`. (Also applies to core CUGA headless.)

### Phase 3 — Decide the bot-token model (Slack / Discord / Telegram / Box-direct)
- **Option A (keep):** formally scope these as single-workspace team bots; document that all users share the
  team's Slack/Discord identity by design. Fine for a team deployment.
- **Option B (isolate):** implement per-user OAuth installs so each user authorizes the app into their own
  workspace and gets their own token. A feature; only do it if per-user chat identity is a real requirement.
- Either way: **choose explicitly and write it down** — today it's shared by accident-looking-like-design.

### Phase 4 — Observability & billing correctness (not a leak, but needed for real ops)
- The `ActivityTracker` singleton is telemetry-only on the events path (no content leak), but concurrent runs
  interleave trajectories and **sum token counts globally** → wrong per-user traces and billing. Make trajectory
  path / `task_id` and token accounting per-thread; ideally per-run tracker via contextvars (upstream CUGA
  change — pervasive call sites, schedule risk). Do this before you bill or debug per-user.

### Phase 5 — Token-at-rest hardening (see the AP sections above)
- Vault `AP_PASSWORD` and `AP_ENCRYPTION_KEY` (stop plaintext `.env`); put AP on a private network (CE
  `--visibility project` / VPE), private managed Postgres, split custody; expose only OAuth/webhook ingress.
- Un-silence the AP Community-Edition project-degrade (`ap_engine.py:391-399`) or move to per-tenant AP.

### Dependency order
Phase 1 is the only thing standing between "leaks credentials" and "doesn't leak content." Phases 2/5 gate real
production (scale + token safety). Phases 3/4 are correctness/completeness. **Ship Phase 1 + its leak test
before ANY multi-user exposure; the rest can follow.**

---

## Deploying the demo (e.g. "Run demo" → live cloud) — two tiers

"Can we host event-driven CUGA on Code Engine and let different people use it with their own credentials?" Yes —
but be clear which of two very different deployments you mean. Fixing the leak (Phase 1) is **necessary but not
sufficient** for the public tier.

### Tier A — Trusted / team deployment (known users log in)
**Easy and safe after Phase 1 + Phase 2 + Phase 5.** Users authenticate, each connects their own Gmail/GitHub
into their own `ea::t::<user>::<app>` slot, tokens vaulted, isolation holds. Low-risk. This is the realistic
near-term target and the one to build first.

### Tier B — Public "anyone clicks Run Demo and connects their real Gmail"
Achievable but **the CUGA fixes are the small part**; the long pole is deployment/legal/operational, mostly
outside the code:
- **You become custodian of strangers' OAuth tokens** → Phase 5 hardening (vault, private AP DB, split custody,
  CE private endpoints) becomes MANDATORY, not optional. One AP breach = everyone's inbox.
- **OAuth app verification — the sleeper blocker.** Google/Box gate *sensitive scopes* (Gmail) behind app
  verification / a CASA security assessment before public use. Unverified = a scary consent warning + ~100-user
  cap. This is often the actual long pole and has nothing to do with CUGA. Budget weeks.
- **Abuse & cost:** public runs spend your LLM budget and fire agents — needs auth, rate limits, quotas, abuse
  controls before it's internet-facing.
- **Privacy/data posture:** holding people's inbox/repo data implies a privacy policy, retention, and delete-on-
  request.

### Recommended: make "Run demo" credential-light
You do NOT need strangers' real credentials for a compelling live cloud demo. Lowest-risk, highest-impact:
- **Lean on the no-cred triggers** — RSS, YouTube, webhook, cron (already in the safe set). "Run demo" on
  *"summarize new items from this feed"* or *"triage whatever POSTs to this webhook"* runs live, isolated by
  `thread_id`, with **no OAuth, no custody, no verification**.
- **Use `POST /api/events/synth-fire`** so a card can show a real Gmail/GitHub *agent run* against a **sandbox
  demo account you own** — the visitor sees the full watch→reason→act loop without handing over anything.
- **Gate real per-user OAuth behind login** for users who explicitly want to connect their own accounts — Tier B,
  done properly, opt-in, after Phase 1 + Phase 5.

**Bottom line:** Tier A is easy/safe after Phases 1/2/5. Tier B is doable but the code is ~20% of it (OAuth
verification + custody + abuse controls are the rest). The credential-light demo gives you the live "Run demo →
cloud" experience now-ish, without waiting on the hard public-credential parts.
