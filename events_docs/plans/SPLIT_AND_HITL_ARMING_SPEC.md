# CUGA Eventing Split + Human-in-the-Loop Arming — Spec & Plan

**Status:** IMPLEMENTED (2026-08-04), then **superseded in part** (2026-08-05) — see §20–§22.3.
**Scope:** the service split (S1) + **arm-time** human-in-the-loop. Fire-time robustness is **explicitly descoped** (we rely on the agent at fire time) and documented as a known gap (§8).

> ### ⚠ How to read this document
> **§1–§19 are the ORIGINAL PLAN**, written before implementation. Two of its decisions were
> reversed during the build, so those sections are a **historical record, not the current design**:
>
> | The plan said (§1–§19) | What actually shipped |
> |---|---|
> | Keep **combined mode** for local dev (§1, §9.1, §11, §12, §14.2) | **Combined mode was removed entirely**, along with `SupervisorRuntime`, `ClassicRuntime` and `_cuga_bridge`. There is one topology. |
> | The **eventing service is the front door** for channels; adapters POST `/invoke` with `agent=concierge` | **CUGA is the door.** Every channel message goes to CUGA's `POST /run`, which decides chat-vs-arming. `/invoke` is now only the *fire* seam. |
>
> **§20 onward is the current design of record.** For the architecture as built, read
> [../ARCHITECTURE.md](../ARCHITECTURE.md).

---

## 1. Purpose

Two changes, one spec:

1. **Split** today's single combined process into **two services**:
   - the **CUGA (streams) service** — vanilla CUGA, the agent brain (`/stream`), *unchanged*;
   - the **Eventing service** — triggers, scheduler, channels, concierge, subscriptions, delivery, and the `/invoke` seam — which **calls** the CUGA service to execute agents.
2. **Human-in-the-loop (HITL) arming** — nothing gets armed until the user has **explicitly approved the exact prompt** that will be sent to the agent at fire time.

**The guiding principle:** the arm is where we earn trust. Since we rely on the agent at fire time, we make the *arm* legit and the *prompt* explicit and approved.

---

## 2. Glossary — what is what

| Term | Meaning |
|---|---|
| **CUGA (streams) service** | Vanilla CUGA. Owns `/stream` (SSE chat), the graph, policies, tools, the MCP registry. The agent brain. Untouched by eventing. **Callable.** |
| **Eventing service** | New standalone service. Owns triggers, the native scheduler, channels (direct backends), the concierge, subscriptions, delivery, identity, and the `/invoke` front door. **Calls** the CUGA service to run agents. |
| **Channel** | A transport/surface for two-way chat (Slack, Discord, Telegram, WhatsApp, web). The *where*. |
| **Integration / Trigger** | A watch on an external system (Box, GitHub, Gmail) or time (cron/poll) that fires. The *when*. |
| **Concierge** | The eventing component that turns a natural-language "automate…" utterance into an armed subscription via the HITL dialogue. **Not** the front door for chat. |
| **`/invoke`** | The eventing service's universal seam. Every trigger/channel/scheduler POSTs a normalized **envelope** here; the worker branch calls CUGA. |
| **Arming** | Creating a subscription from an utterance: extract → clarify → **confirm** → arm. |
| **Subscription** | A persisted standing automation: trigger + fire-time **prompt** + delivery target + agent. |
| **Fire-time prompt** | The exact instruction handed to the agent on every fire. **In HITL, this is what the user approves.** |
| **Sink / delivery target** | Where a fired result is delivered. |
| **Edge dispatch** | The trivial slash-or-arming check at each entry (web send handler / channel adapter) that routes chat → CUGA and slash/arming → eventing. No classifier, no router service. |

---

## 3. Target architecture (S1)

```
  Web        Slack / Discord / Telegram / WhatsApp
   │                     │
   │        Channel adapters (receive · identity · deliver)
   └──────────┬──────────┘
              ▼  edge dispatch: slash/arming? → eventing ; else → CUGA
      ┌───────────────┐                 ┌──────────────────────┐
      │  CUGA service │  ◀── HTTP ────  │   Eventing service    │
      │  (/stream)    │  (execute agent)│  triggers · scheduler │
      │  graph·policy │                 │  concierge · /invoke  │
      │  ·tools·MCP   │                 │  channels · delivery  │
      └───────────────┘                 └──────────────────────┘
```

**The seam.** Everything upstream of the agent call is already HTTP today (every trigger/channel/scheduler POSTs `/invoke`). The only in-process tie is the worker call inside `/invoke`. The split replaces that one call with an HTTP call to the CUGA service.

**The CUGA call surface — RESOLVED (facts verified in this fork).**
- `/stream` is **SSE-only**; its `stream: false` request flag is **dead code** (never read) — no non-streaming mode exists.
- **A2A is built and complete but NOT mounted** (`server/a2a/`): JSON-RPC `message/send`, executes the real graph, returns the final answer synchronously in the Task result — but `main.py` has zero A2A wiring, `settings.a2a.enabled` is unread, the router has no auth, and it is unproven against the real app. Every A2A path 404s today. Usable only after a *mount + auth + integration-proof* task.
- The non-streaming collector already exists in core: **`AgentLoop.run`** (drains the graph → final answer), the same sibling pattern today's events `/invoke` uses (no HTTP call to `/stream`).

**DECISION (agreed): add a tiny token-guarded `/run` over `AgentLoop.run` for the eventing hop** (`{query} → {answer, status}`, ~15 lines wrapping existing code). `/run` = `/stream` with the live event feed collapsed to the final answer, returned as one JSON body — same graph execution, non-streaming output adapter (the `AgentLoop.run` sibling of `AgentLoop.run_stream`), no HTTP call to `/stream`. **A2A is deferred to a later "public interop" phase** (its logic is ready; it just needs mount + auth + a real-app test). `/run` is the private service hop; A2A is the public standard surface — not mutually exclusive.

**`/run` I/O contract (concrete).** `/run` takes the *same per-run inputs as `/stream`* (it runs the same graph) — only the output collapses to one JSON.

*Key fact — KB is NOT a call-time input.* A knowledge base / `special_instructions` / skills are **not** passed in the request to `/stream` or `/run`. They're attached out-of-band to a `thread_id` (session scope, via `POST /api/knowledge/documents` keyed by `X-Thread-ID`) or to an agent (agent scope, keyed by `agent_id:config_version`), and the run looks them up by `thread_id`/agent at execution time. `/stream`'s only KB-adjacent body field is `attachments` — **metadata only** (names of already-uploaded files). **So `/run` gets KB via `thread_id`**: attach docs to the subscription's thread at arm time; the fire picks them up.

Request:
```json
POST /run   (X-Gateway-Token)
{
  "query": "<approved fire-time prompt>",
  "thread_id": "sub_1234",       // KB + session state + delivery context ride on this
  "agent": null,                  // or target_agent / roster; draft via a flag
  "user_id": "ea::acme::alice",   // principal
  "disable_history": true,
  "attachments": [ ... ],         // optional metadata (already-uploaded files)
  "action_response": { ... }      // optional, HITL resume
  // optional SDK-parity extras: "user_context", "variables"
}
```
Response (terminal `AgentLoopAnswer` serialized to the SDK's `InvokeResult` shape + `status`):
```json
{ "answer": "…", "status": "ok|error|interrupt", "thread_id": "sub_1234",
  "sources": [ {n, cite_id, filename, page, section_path, scope, snippet, score, query} ],
  "variables": { ... }, "error": null }
```
Mental model: **`/run`'s body ≈ `/stream`'s terminal `Answer` event, as the whole response** — intermediate node/`tool_call` frames dropped. Underlying call is `AgentLoop.run` (the non-streaming sibling of `run_stream`); no HTTP call to `/stream`.

**Deployment modes.** Support both:
- **Combined mode** (local dev): both run reachable on localhost (two processes, or eventing pointed at a local CUGA). Keeps local simple.
- **Split mode** (Code Engine): two apps — `cuga` + `cuga-events`.

The test harness is URL-parameterized (`EVENTS_SERVER_URL`) and always targets the **eventing** front door, so the split is invisible to tests.

---

## 4. Channels vs Eventing — two features, kept separate

They are orthogonal and each ships without the other:
- **Channels** = transport adapters (receive, map identity → principal/thread, deliver replies). Channels-without-eventing = "talk to CUGA on WhatsApp."
- **Eventing** = triggers/schedules (subscribe, fire, deliver). Eventing-without-channels = "web chat + schedules delivered to web."

**Dispatch (no router).** Chat is the default and goes straight to CUGA. The eventing path is reached **only** by:
1. an explicit slash verb (`/automate`, `/schedule`, `/watch`, `/trigger`), or
2. an **open arming conversation** on that thread (sticky until armed/cancelled/TTL).

A plain chat never touches eventing. The same two-line dispatch runs at every edge (web + each channel). Slash is a **text prefix** (uniform across WhatsApp/Telegram/Slack/Discord); native slash-commands are a later polish.

---

## 5. The HITL arming feature (the heart of this spec)

Arming becomes an explicit **3-state dialogue**; **nothing arms until the human approves the exact prompt.**

```
DRAFT ──▶ NEEDS_INPUT ──▶ CONFIRM ──▶ ARMED
             ▲   │           │  │
             └───┘        edit│  └─ "yes" → persist subscription
          (one field at       └────┐
           a time)     "/cancel" or 10-min TTL → CANCELLED
```

- **DRAFT** — parse the utterance into a spec `{trigger, prompt, delivery, agent}`.
- **NEEDS_INPUT** — a required/ambiguous field is missing → ask **one** question, wait. (loops)
- **CONFIRM** — spec is complete **and validated** → show it back, require explicit **yes**.
- **ARMED** — persist exactly the confirmed spec. **CANCELLED** — cancel or TTL.

### 5.1 Structured arming contract (replaces today's plain-text return)

Every arming turn returns:
```json
{
  "state": "needs_input | confirm | armed | cancelled",
  "question": "which Slack channel should I send it to?",   // when needs_input
  "summary": {                                                // when confirm
    "trigger":  "every 5 minutes",
    "prompt":   "Fetch the current IBM (NYSE: IBM) stock price and report it as a short message.",
    "delivery": "this chat",
    "agent":    "markets"
  },
  "subscription_id": "sub_…"                                  // when armed
}
```
`state` drives the edge stickiness (needs_input|confirm → keep thread sticky to eventing; armed|cancelled → release) **and** the UI rendering (question vs confirm-card). Today's question is buried in answer text with no flag — this is the fix.

### 5.2 The CONFIRM card (makes the prompt explicit)

> Every **5 minutes** I'll ask the agent:
> *"Fetch the current IBM (NYSE: IBM) stock price and report it as a short message."*
> and send the result to **this chat**, using the **markets** agent.
> **Arm it?**  ·  *edit prompt / schedule / destination*  ·  *cancel*

- **yes** → arm. **edit** → reopen that field (esp. the prompt) → re-confirm. **cancel** → drop.
- The prompt is a first-class, editable, **approved** artifact — not a silently-armed utterance.

### 5.3 "Concierge talks back to CUGA, then to the human"

Two places the concierge consults the model rather than regex-slotting:
1. **Compose the prompt** — refine the loose utterance ("send IBM stock price") into a crisp agent instruction (disambiguate ticker/exchange, state output shape). The human approves the *refined* prompt.
2. **Author the question** — phrase clarifications; *(extension)* use CUGA to enumerate options ("which of your Slack channels?").

### 5.4 Legit = validate before CONFIRM

CONFIRM only ever shows an **armable** spec; any validation failure becomes a NEEDS_INPUT question, never a silent default: cron parses / interval sane; push slots filled + registry-valid; delivery target resolvable (default "reply where you asked" for channels, but **shown** in confirm); agent exists.

### 5.5 State lifecycle

- Parked state **persisted** (thread-keyed, in the durable store), 10-min TTL, explicit **/cancel**, pop-on-resume.
- Fixes today's fragility: in-memory dict (wiped on restart), no cancel, and the `/stream` double-pop resume bug (dissolved by the structured flag + edge-held arming state — no classify-then-pop probe).

---

## 5A. Thread identity & delivery (arm → fire)

"Thread" is **two** concerns; conflating them is a bug.
- **Delivery sink** — *where the fire result lands* = the user's conversation. This is what "respond back to the same thread" means.
- **Execution thread** — *the `thread_id` the agent runs under at fire time* — internal plumbing (worker memory + KB binding); the user never sees it.

**Decision: two separate fields.** Delivery → the origin conversation; execution → a dedicated per-subscription thread.

### Current behavior (verified)
- The subscription stores the **arming `thread_id`** (`p.thread(origin)`), and **every fire executes under that same frozen id** → the worker checkpointer **accumulates history across fires** (fire N sees fires 1..N-1). For "IBM every 5 min" that's wrong — fire #288 drags 287 prior turns.
- Native-fire delivery is routed **from `thread_id`** (channel + native id + in-thread locus encoded in the `gw:…#locus` string), **not** from `deliver_to` (a bare channel name used only for Runs display/dedup).
- **Channels (Slack/Discord/Telegram direct): same-thread delivery already works** — a fire replies into the exact DM/channel, and a Slack-thread arm replies into that thread (`thread_ts=locus`).
- **Web: no live delivery** — no `gw:` prefix → delivery skipped; result only lands in `now_run` → `/api/events/runs` (Studio → Runs). The in-chat toast is **broken for cron/poll** (`isWebFire` matches only `channel` empty/`web`, but native fires log `channel="cron"/"poll"`).

### Target design
1. **Delivery sink = the origin conversation, captured fully at arm time** (channel + chat/DM id + in-thread locus). Preserve today's channel behavior through the split.
2. **Execution thread = a dedicated per-subscription id, decoupled from the chat thread.** KB attaches here. **Memory policy: stateless-per-fire by default** (each fire independent — correct for "get current price"), opt-in **continuous** for tasks that benefit from memory. (Current default = continuous/accumulating = wrong for most watches.)
3. **CONFIRM surfaces the delivery target** — "…and send it to *this chat*" (channel) vs. "…to your *Runs inbox* / your *linked Slack*" (web) — so the user approves *where* results go, not just the prompt.
4. **Web same-thread is a known limitation** (no async connection). v1: **Runs inbox + fix the toast** so web fires notify; offer **"post to my Slack/email"** as the real async path. (Live SSE only while the tab is open is a non-goal.)
5. **Split note:** all thread/delivery logic lives in the **eventing service** (sink capture, `send_direct`, channel adapters). Only *execution* crosses to CUGA via `/run` under the execution thread. The split does not touch delivery.

Per-surface answer to "same thread as I armed from": **Slack/Discord/Telegram (direct): YES** (today + preserved). **Web: NO today** (Runs-tab only); v1 adds inbox notify + optional linked channel.

## 6·0 Current-state vs new-state (functionality + implementation)

Legend: 🟢 new user-facing capability · 🔵 same behavior, implementation changes · ⚪ deliberately unchanged.

| Area | Current | New | Kind |
|---|---|---|---|
| Arm a schedule/watch | slash or NL-classified; arms the prompt **verbatim, unseen** | slash (or open arming turn); **CONFIRM shows exact prompt, requires yes/edit/cancel** | 🟢 |
| Clarify missing info | PUSH required-slots only; cron/poll silently default | widened to cron/poll cadence + delivery; validate-before-confirm | 🟢 |
| Cancel an in-progress arm | none | `/cancel` | 🟢 |
| Automations survive restart/deploy | **No** (`events.db` `:memory:` → fleet vanishes) | **Yes** (durable store) | 🟢 |
| Web: see a fire result | Runs tab only; toast broken for cron/poll | Runs inbox + fixed toast; optional "post to my Slack/email" | 🟢 |
| Channel: fire replies to same DM/thread | Yes | Yes — preserved | ⚪ |
| Plain chat | POST `/stream` with chat-vs-events fork inside it | POST `/stream` — pure chat, fork removed; edge routes slash→eventing | 🔵 (minor UX: NL auto-detect → explicit slash) |
| Where the agent runs | in-process `_cuga_bridge` (needs `app_state`) | HTTP `/run` on CUGA (wraps `AgentLoop.run`) | 🔵 |
| Process / deploy | one combined container (events on CUGA `main.py`), min=max=1 | two services (`cuga` + `cuga-events`); combined mode for local | 🔵 |
| CUGA codebase | events forked into `main.py` | CUGA vanilla + tiny `/run`; eventing separate | 🔵 (upstream-trackable) |
| Secrets | channel/integration tokens in the one image | only in eventing; CUGA carries none | 🔵 (security) |
| Execution thread / memory | same frozen thread every fire → history accumulates | dedicated stateless-per-fire thread | 🔵 (cleaner runs) |
| KB / per-run inputs | thread-scoped, via `/stream` | thread-scoped, via `/run` by `thread_id` | ⚪ |
| Fire-time failure handling | silent (no retry/validate/HITL/notify) | unchanged — descoped by decision | ⚪ |
| Direct integrations (Slack/Box/GitHub/…) | work | work — in eventing, via the `/run` hop | ⚪ |
| Test commands | `make test`/`test-e2e`/`test-ap`/`test-e2e-ce`/`ce-*` | same commands; + new HITL test | 🔵 (parity) |

**Net user-facing change = the five 🟢 rows** (prompt approval/CONFIRM, wider clarification, `/cancel`, durability, web notify). Everything else is identical behavior on cleaner/safer foundations.

## 6. What changes vs. what exists

| Piece | Today | This spec |
|---|---|---|
| Return shape | plain text, no flag | **structured `{state, question?, summary?, subscription_id?}`** |
| Prompt | armed verbatim, unseen | **composed, shown, approved** (CONFIRM) |
| Clarification scope | PUSH required-slots only | **+ cron/poll cadence + delivery**, validate-before-confirm |
| `/stream` resume | **buggy** (double-pop, wrong principal) | fixed via structured flag + edge-held arming state |
| Parked state | in-memory, wiped on restart, no cancel | **persisted**, thread-keyed, **/cancel**, TTL |
| Process | one combined server (events mounted on CUGA `main.py`) | **two services** (eventing calls CUGA over HTTP); CUGA vanilla |
| Worker call | in-process `_cuga_bridge` (needs `app_state`) | **HTTP** backend → CUGA (A2A/MCP); in-process retained for combined mode |
| Secrets | channel/integration creds in the one image | **all in eventing**; CUGA carries none |

---

## 7. Direct integrations after the split — do they keep working?

**Short answer: yes.** All backends live in the **eventing** service and move with it; only the agent-execution step becomes an HTTP hop to CUGA.

| Integration | Kind | Continues? | Notes |
|---|---|---|---|
| **Slack** | Direct channel (default) | ✅ | Receiver + `SLACK_BOT_TOKEN` move to eventing. |
| **Box** | Direct integration (opt-in poller) | ✅ | `EVENTS_BOX_TOKEN` moves to eventing. *Known gap:* "direct" Box still uses an AP schedule as its timer today — could move to the native scheduler. |
| **GitHub** | **AP-backed** (no direct module) | ✅ | Runs via the AP engine, which lives in eventing. Works as long as eventing hosts AP + can reach it. *(Not literally "direct" — clarified.)* |
| **Gmail** | AP-backed | ✅ | Same as GitHub. |
| **Telegram / Discord** | Direct channels | ✅ | Bot tokens move to eventing. |

**The one new dependency for all of them:** the **eventing → CUGA hop** for execution. Trigger *receipt* is unaffected; *execution* now needs CUGA reachable → add timeout + retry on the hop (reuse `GATEWAY_TOKEN` / A2A auth). **Security win:** the CUGA image no longer carries any channel/integration secrets.

---

## 8. Gaps & known limitations

- **Fire-time robustness — DESCOPED (deliberate).** No validation of the agent's answer, no retry/dead-letter, no fire-time HITL, no failure notification. Silent-failure modes remain (native-fire error → no row/no message; empty cron answer → delivered as SUCCEEDED; signal-less poll → suppressed as `nochange`; `next_fire` always advances). We rely on the agent; the mitigation is a **legit arm + approved prompt** (this spec), which removes the arm-time causes of fire-time garbage.
- **`events.db` durability** — defaults to `:memory:`; must move to a durable store for persisted parked-state and subscriptions to survive restart. **Prerequisite (P0).**
- **Multi-instance** — no leader election / row-claim; scheduler assumes single instance (`min=max=1` on CE). Out of scope here; documented constraint.
- **CUGA call surface** — no clean sync run-endpoint; must adopt A2A/MCP or add `/run` (§3, §7).
- **Direct-path credentials** — app-level single-token, bypass the vault resolver (plaintext `.env` unless `vault://`). Documented; not changed here.

---

## 9. Architecture updates required

1. **Standalone eventing ASGI app** — give the events package its own FastAPI app + lifespan owning the background loops (Telegram/Discord/Box/scheduler), instead of mounting on CUGA's `main.py`.
2. **Runtime `http` backend** — `/invoke`'s worker branch calls CUGA over the chosen surface (A2A/MCP); keep the in-process backend for combined/local mode. Backend selected by config.
3. **CUGA call surface** — add a tiny token-guarded sync `/run` over the existing `AgentLoop.run` (returns `{answer, status}`). Rationale: `/stream` is SSE-only (dead `stream:false`) and A2A is built-but-unmounted/unauthed/unproven. A2A deferred to the interop phase.
4. **HITL arming** — structured contract, CONFIRM step, prompt composition, widened clarification, validate-before-confirm, persisted parked state + `/cancel` (§5).
5. **Edge dispatch** — slash/arming routed at the edge; `/stream` reverts to pure chat (delete the in-`/stream` fork).
6. **Durable `events.db`** — off `:memory:` (P0 prerequisite).
7. **Auth + resilience on the hop** — `GATEWAY_TOKEN`/A2A auth, timeout, retry.
8. **Two-service deploy** — CE provisions `cuga` + `cuga-events`; combined mode for local.
9. *(Optional, later)* **Gateway/BFF** for the UI fork (path-routes browser → CUGA vs eventing), or a simple proxy, or keep the combined UI for v1.

---

## 10. Implementation plan (phased, HITL first to de-risk)

**Phase 0 — Prerequisites (no behavior change)**
- P0.1 Durable `events.db` (off `:memory:`).
- P0.2 Add the CUGA call surface: a tiny **sync `/run`** (wraps the existing `AgentLoop.run`), token-guarded, returning `{answer, status}`. *(A2A is built but unmounted/unauthed/unproven — deferred to a later interop phase, not this hop.)*

**Phase 1 — HITL arming (in the current single process; independently shippable)**
- P1.1 Structured arming contract `{state, …}`.
- P1.2 CONFIRM step (compose prompt, summary, yes/edit/cancel).
- P1.3 Prompt composition (LLM refine).
- P1.4 Widen clarification (cron/poll cadence + delivery) + validate-before-confirm.
- P1.5 Persist parked state + `/cancel`; fix `/stream` resume (via P1.1 + edge dispatch).
- P1.6 Edge dispatch; `/stream` back to pure chat.
- P1.7 HITL test harness (yes / no / edit-prompt / cancel).
- P1.8 Thread/delivery model (§5A): dedicated **stateless-per-fire** execution thread (decoupled from the chat thread); sink captures the full origin conversation; CONFIRM surfaces the delivery target; **web Runs-inbox toast fix** + optional "post to my Slack/email" async delivery.

**Phase 2 — The split (S1)**
- P2.1 Standalone eventing ASGI app + lifespan.
- P2.2 Runtime `http` backend → CUGA; retain in-process for combined mode.
- P2.3 Hop auth + timeout/retry.
- P2.4 Move all channel/integration/AP creds to eventing env; strip from CUGA.
- P2.5 Two-service CE deploy; combined local mode.
- P2.6 *(optional)* gateway/proxy for the UI fork.

**Phase 3 — Test parity (local + CE)** — see §11.

---

## 11. Testing plan — "nothing should change" for what you already test

**Guarantee:** every existing target keeps working, both local and CE, because the `/invoke` and `/api/events/*` contracts don't change and the harness targets the eventing front door via `EVENTS_SERVER_URL`.

**Existing targets — must stay green (local):** `make test`, `make test-e2e`, `make test-ap`.
**Existing targets — must stay green (CE):** `make test-e2e-ce`, `make ce-status`, `make ce-logs`, `make ce-smoke`, `make ce-url`.

**Parity work:**
- **Local:** existing targets pass in **both** combined mode and split mode (two local processes; harness → eventing URL). Add a `make run-split-local` to bring up both.
- **CE:** `make ce-deploy` provisions **two** apps (`cuga` + `cuga-events`); existing `ce-*`/`test-e2e-ce` point at `cuga-events`; add per-app ops (`ce-status`/`ce-logs` gain an `APP=` selector or a `-cuga` variant).
- **Direct integrations:** re-run the Slack/Box (+ GitHub/Gmail via AP) arm+fire e2e against both modes and on CE — assert unchanged behavior (the eventing→CUGA hop is the only new failure surface).

**New — HITL test (local `make test-arm-hitl`, CE `make test-arm-hitl-ce`):**
The harness drives the arm dialogue programmatically and asserts on `state`:
1. POST `/automate every 5 minutes send IBM stock price` → assert `state ∈ {needs_input, confirm}`.
2. If `needs_input` → POST the answer → loop.
3. At `confirm` → assert `summary.prompt` present and sensible → POST **"yes"** → assert `state == armed` + a subscription row exists.
4. **Edit case:** at `confirm`, POST "change the prompt to …" → assert re-`confirm` with the new prompt.
5. **Cancel case:** POST `/cancel` → assert `state == cancelled`, no subscription.
This is the "way to say yes/no and update the prompt" — it's just POSTing follow-ups and asserting the structured `state`. Runs identically local and CE (same URL-parameterized harness).

---

## 12. Sequencing / rollout

1. **P0** (durability + call surface) — unblocks everything.
2. **Phase 1 (HITL)** ships and is testable **in the current single process** — lowest risk, immediately valuable, no topology change.
3. **Phase 2 (split)** — after HITL is green; the harness proves parity in combined mode first, then split, then CE.
4. **Phase 3** runs continuously through 1–2.

Rationale: HITL is independently valuable and doesn't need the split; the split is the riskier infra change. Do the valuable low-risk feature first, prove parity, then change topology.

---

## 13. What shipped (2026-08-04)

### Code
| Area | File | What |
|---|---|---|
| Durable store | `server/main.py` | `EVENTS_DB` defaults to `~/.cuga/events.db` (was `:memory:`), falls back to `:memory:` if the dir is unwritable rather than failing boot |
| Pending-arm table | `events/subscriptions.py` | `pending_arm` (thread, state, payload, expires) + `get/set/clear_pending_arm`; **read does not consume** |
| Arming machine | `events/arming.py` **(new)** | states, `read_reply`, `compose_prompt`, `validate`, `summarize`, `render_card`, contextvar state |
| CONFIRM gate | `events/concierge.py` | `_arm_propose` / `_arm_gate` / `_park_arm` / `_clear_arm`; slash now **proposes**, never arms |
| Deterministic arm | `events/concierge.py` | cron/poll arm directly from the validated cadence + approved prompt (was: a 2nd LLM pass free to arm something else) |
| Structured reply | `events/app.py` | `{state, question, summary, subscription_id}` on `/api/concierge` and `/invoke` |
| Edge dispatch | `server/main.py` | `/stream` routes only slash + an OPEN arming dialogue; NL auto-detect removed; parked-state probe is a READ under the same principal (kills the double-pop bug); fixed a latent `NameError` on the arming reply path |
| Exec vs delivery thread | `events/app.py` | fires run on a fresh `fire:<sub>#<trace>` thread (`EVENTS_FIRE_MEMORY=continuous` restores accumulation); delivery still uses the origin thread |
| Web fire visibility | `events/app.py` | runs log the **delivery** channel, so web fires log `web` (were `cron`/`poll`, which the chat toast filter never matched) |
| CUGA call surface | `server/main.py` | `POST /run` — non-streaming sibling of `/stream`, token-guarded, `{answer,status,thread_id,sources,variables,error}` |
| Split runtime | `events/runtime.py` | `HttpRuntime` (backend `http`) → CUGA `/run`, retry on transport/5xx only, no retry on 4xx or agent error |
| Standalone service | `events/service.py` **(new)** | the eventing layer as its own ASGI app + lifespan owning the background loops |
| Targets | `Makefile` | `run-events`, `test-e2e-split` (`EVENTS_PORT`, default 8100) |
| CE | `deploy/ce/2_deploy_app.sh` | explicit `EVENTS_DB=/app/.cuga/events.db` |

### Tests — 318 green (`make test`), +36 new
`tests/events/test_arming_hitl.py` (29): reply parsing, prompt composition, validate-not-default,
confirm→yes, cancel, edit-prompt (and that the **edited** text is what gets armed), clarify-then-arm,
unclear-is-not-approval, two threads independent, **survives a restart**, `/cancel` no-op, plain chat
untouched, and the same dialogue over a **channel** envelope.
`tests/events/test_split_service.py` (7): `/run` hop shape + auth header, retry 5xx, no-retry 4xx,
agent-error surfaced, `make_runtime("http")`, standalone service serves the same contract, and the
two topologies expose **identical route tables**.
Two pre-existing tests were updated (they asserted one-shot arming — the behaviour this changes).

### Verified live (local server, real model)
`/run` → `{"answer":"Paris","status":"ok"}` (single JSON, no SSE) · token guard rejects a missing
token · propose → `confirm` with composed prompt/trigger/delivery · edit → prompt replaced, still
`confirm`, nothing armed · `yes` → `armed`, and the stored fire-time prompt is the **edited** text at
300s · `cancel` → `cancelled`, nothing armed.

### NOT done
- **Two-service CE deploy.** CE still runs combined mode (unchanged, still works). The split runs
  locally (`make run-events`); a CE split needs a second app + image + URL wiring.
- **`test-e2e-split` not executed** — it needs both services up with live channel tokens.
- **Frontend.** The Studio/chat UI does not yet render the confirm card specially (the card is
  plain text, so it reads fine as-is) and does not use the `state` field. No `dist/` rebuild.
- **Fire-time robustness** — descoped by decision; §8 gaps stand.
- **Multi-instance** — no leader election/row-claim; still single-replica.

---

## 14. Live-run findings (2026-08-04, second pass)

### Harnesses had to learn the CONFIRM gate
Arming is a dialogue now, so every harness that armed a flow broke — it posted an utterance and
immediately looked for a subscription. Fixed by having the harness play the human (answer the
clarifying question, then "yes"): `live_e2e.arm_with_confirm`, `live_fire.arm`,
`live_integrations_e2e._concierge`, `live_exhaustive.concierge`.
**If you add a harness that arms, it must drive the dialogue.**

### Real channels — verified end to end (no AP)
| Channel | Result |
|---|---|
| **Slack** | FULL round trip: real `chat.postMessage` → signed Events callback → **bot replied in-thread**; a badly-signed event is rejected 401 |
| **Discord** | concierge answered + **reply landed in the channel** (real REST send) |
| **Telegram** | token valid (`getMe`), concierge answered, **real `sendMessage` delivered** |
| **cron / poll** | armed and **FIRED** with live answers |

New harness phase **`channel-arm`** (`live_fire.py --only channel-arm`) proves the whole HITL story
on a real channel: `@bot /automate …` → the **confirm card posts in-thread** → asserts **nothing is
armed yet** → replies `yes` **in-thread with no new @mention** → armed → and the tick is
**delivered back into that same Slack thread**. PASS.

### Three bugs found by running it
1. **`live_fire` never @mentioned the bot.** With `EVENTS_SLACK_CHAT=mention` a channel message is
   correctly gated away from chat, so the bot never replied and the case looked like a broken round
   trip. `live_e2e` already handled this; `live_fire` now does too.
2. **Split mode fired ticks at the wrong process.** Every trigger posts to
   `127.0.0.1:$EVENTS_CUGA_PORT/invoke`. Mounted, that is CUGA's port and also the events layer's.
   Split, `/invoke` belongs to the EVENTS service — ticks went to CUGA's port, whose store had never
   heard of the subscription. Flows armed and silently never fired. `service.py` now repoints
   `EVENTS_CUGA_PORT` at itself (after resolving CUGA's address) and refuses a `CUGA_URL` that
   points at its own port (self-call loop).
3. **`HttpRuntime` didn't know the canonical agent.** The roster lives in CUGA's process, so the
   split's agent store is empty and every tick returned `404 unknown agent 'cuga'`. It now
   synthesizes the "cuga" spec, matching `SupervisorRuntime`/`find_or_create_flow`.

### Two operational caveats (not bugs in this work)
- **One Telegram bot cannot be long-polled by two processes** — running the mounted and standalone
  services side by side gives `409 Conflict` on `getUpdates`. In a real split, only the events
  service runs the channel loops.
- **httpx INFO logging writes bot tokens into logs** (`api.telegram.org/bot<TOKEN>/getUpdates`).
  `service.py` now pins the httpx logger to WARNING (`EVENTS_LOG_HTTPX=1` to restore).

### Split verified
`cron/pricebot` armed on the events service (:8100) and **FIRED** with a live answer fetched through
CUGA's `/run` (:7860). The `poll/weatherbot` case armed but its first tick didn't land inside the
harness's 180s window — first-observation suppression plus the extra hop; not split-specific.

---

## 15. Deploying (combined and split)

Both topologies run from **one image** — the split is a different entrypoint, not a different
build. Build once (`make ce-build`), then pick a deploy.

**Combined (default, what CE has run so far).** One app, events mounted on CUGA.
```
make ce-build && make ce-deploy
make ce-smoke ; make test-e2e-ce
```

**Split (`deploy/ce/3_deploy_split.sh`, `make ce-deploy-split`).** Two apps:

| App | What it is | Command |
|---|---|---|
| `cuga-core` | vanilla CUGA — `/stream`, `/run`, the UI. **`EVENTS_ENABLED` is not set**, so no triggers, no scheduler, no channel loops | `uv run cuga start demo` |
| `cuga-events-svc` | the eventing service — triggers, scheduler, channels, concierge, `/invoke`; executes via `cuga-core`'s `/run` | `uv run python -m cuga.backend.events.service` |

The script resolves `cuga-core`'s URL first and injects it as `CUGA_URL`, sets `EVENTS_PUBLIC_URL`
to the **events** app (it serves the Slack/OAuth callbacks), and writes both URLs to
`deploy/ce/.ce_urls_split.env`. Point the harness at the events front door — the wire contract is
identical, so nothing else changes:
```
make ce-deploy-split
make test-e2e-ce CE_URL=<events url>
```

**Locally**, the same split is `make run-events` (CUGA on :7860, eventing on :8100) plus
`make test-e2e-split`, which allows a longer tick budget (`FIRE_TICK_WAIT_SECS`, default 300)
because a split fire crosses the wire and CUGA's tool servers scale to zero.

**Carried over, both topologies:** single instance each (scheduler + channel loops are process-wide
singletons → `min=max=1`), and the container filesystem is ephemeral — `EVENTS_DB` survives a
restart but **not** a revision replace. Mount a volume or move to Postgres when subscriptions must
outlive a redeploy.

**Not yet split:** the CE secret. Both apps read the same one today, so `cuga-core` can still see
channel tokens it never uses. Emitting two env files from `make_env_ce.sh` is the next tightening
step — the code boundary is already correct (only the events service reads them).

---

## 16. CE results (2026-08-04) — combined GREEN, split deploys but has an open gap

### Combined (`cuga-events`) — the primary deployment. GREEN.
Rebuilt from this branch and redeployed. `make ce-smoke` green. HITL verified on the deployed app:
propose → `confirm` (with the composed prompt) → edit → `confirm` (edited prompt) → `yes` → `armed`.
`make test-e2e-ce`: **29 passed · 0 failed · 5 skipped** (all skips AP-intentional) and the fire
half **2/2 FIRED** (`cron/pricebot`, `poll/weatherbot`) with live answers. Slack, Discord and
Telegram all round-tripped against the deployed app.

### Split (`cuga-core` + `cuga-events-svc`) — deploys and connects, but does NOT yet pass e2e.
What works: both apps come up; `cuga-events-svc /health` reports its CUGA target; **`cuga-core`'s
`/run` answers over HTTPS** (`{"answer":"Paris","status":"ok"}`); `cuga-core` is genuinely vanilla
(its `/api/events/*` is the SPA catch-all serving HTML, not the events API — check the body, not the
status code).

**The open gap — agent identity does not cross the hop.** `make test-e2e-ce` against the split
front door: 23 passed · 6 failed. Every failure traces to one cause: the events service reports
`0 agents`, and answers come back "not available through the tools I have access to". The worker
now executes on **cuga-core**, so that is where the roster (`EVENTS_SUPERVISOR` +
`supervisor_agents.yaml`) and the tool wiring must live — but the split script puts the supervisor
env on the **events** app, where the `http` runtime only forwards, and `HttpRuntime.run` does not
send a target-agent selector to `/run` at all. Consequences:
1. `/api/events/agents` lists the events service's own (empty) store → "0 agents".
2. Every fire runs as cuga-core's *default* agent, not the sub-agent the subscription targeted.

**The fix (not done):** teach `/run` to accept an `agent` and have `HttpRuntime` pass
`target_agent`, then put the supervisor roster on `cuga-core`. Until then the split is a working
transport with the wrong agent selection — fine for a single-agent deployment, not for a roster.

**Cost note:** `cuga-core` and `cuga-events-svc` are deployed at `min-scale 1` (always warm). Tear
them down with `ibmcloud ce app delete -n cuga-core -n cuga-events-svc` when not iterating; the
combined `cuga-events` app is untouched and remains the working deployment.

---

## 17. The split's remaining blocker: the roster does not follow execution across the hop

Three fixes landed and are verified on CE (`/api/ui/config` carries `events_api_url`; the events
service reports CUGA's roster instead of its own empty store; CORS allows the UI origin). Split e2e
went 23/6 → **25 passed · 5 failed**. The residue has ONE cause, and it is architectural rather
than a wiring mistake:

**`EVENTS_SUPERVISOR` + `supervisor_agents.yaml` are an EVENTS-LAYER concept.** The roster is
loaded by `SupervisorRuntime`, which builds a `CugaSupervisor` from the YAML. In the split, the
worker moved to `cuga-core` — a **vanilla** CUGA with no events layer, hence no `SupervisorRuntime`
and no roster. Setting `EVENTS_SUPERVISOR=1` on `cuga-core` is inert. Its `/run` therefore executes
whatever agent CUGA itself was started with (`cuga start demo` → the single "Digital Sales Agent"),
which is why answers come back without the expected tools.

This is exactly the intended model — *"the CUGA server has a supervisor and its sub-agents; events
just targets whatever is loaded"* — and the events side already honours it (it always targets the
one agent `"cuga"`; no selector crosses the hop, and none is needed). The gap is on the CUGA side:
**vanilla CUGA has no way to be started AS the supervisor with a roster.**

Options, in preference order:
1. **Teach CUGA to load a supervisor roster natively** — a start flag/setting that makes
   `cuga start …` build a `CugaSupervisor` from `supervisor_agents.yaml`, so `/run` *is* the
   supervisor. This is the clean fit for the model and makes the roster a CUGA-side concern where
   it belongs.
2. **`cuga start demo_supervisor`** — a preset that already enables CugaSupervisor, but it is "the
   CRM demo + supervisor" (it also starts the CRM API and friends), so it is not a drop-in for a
   general deployment.
3. **Accept single-agent split** — works today end to end (`/run` answers, fires deliver); fine
   when the deployment has one agent, wrong for a roster.

Until one of those lands, **combined mode remains the supported topology** and is fully green;
split is a working transport with the wrong agent loaded.

---

## 18. CUGA preloaded as a supervisor (option 1 — implemented)

`CUGA_SUPERVISOR_ROSTER=<supervisor_agents.yaml>` starts CUGA **as** the supervisor. `/run` builds a
`CugaSupervisor` from the roster once (cached), and every call routes through it — sub-agent
selection happens inside CUGA, where the roster belongs. Callers never name a sub-agent: they
address the supervisor. One agent in the file or twenty-seven, the caller is unchanged.

- Unset → unchanged behaviour (`/run` drives `event_stream`, as before).
- Only `/run` takes this path; `/stream` and the UI keep their existing agent, so enabling a roster
  cannot disturb the interactive surface.
- A roster that fails to load returns a clear 500 naming the file rather than silently degrading to
  the default agent — the failure mode that made this bug hard to see in the first place.
- `deploy/ce/3_deploy_split.sh` now sets `CUGA_SUPERVISOR_ROSTER` on **cuga-core**.
  `EVENTS_SUPERVISOR` is deliberately NOT set there: that flag is read by the events layer's
  runtime, which cuga-core does not have, so it was inert — the root cause in §17.

**Verified:** the supervisor builds from `supervisor_agents.yaml` in-process
(`_get_supervisor()` → `CugaSupervisor`). **Not yet verified:** a full split e2e with the roster
live — that needs `make ce-build && make ce-deploy-split` and a re-run of
`make test-e2e-ce CE_URL=<events url>`.

## 19. Split status after the preloaded supervisor — one layer left

The supervisor now loads on cuga-core (`roster on cuga-core (preloaded supervisor)`) and `/run`
routes through it — §18 works. The remaining failure is one layer down: **the sub-agents have no
tools.** `GET /api/apps` on cuga-core lists only `digital_sales` (the demo app), not the `cuga-*`
MCP servers (finance/web/geo/knowledge/code/text) the roster's agents declare. So `/run` answers
"the available toolset does not include…".

`MCP_SERVERS_FILE` is set on cuga-core, and the identical image serves those tools correctly in the
COMBINED app — so the image and the file are fine. The difference is the start command:
combined runs `cuga start demo --events`; cuga-core runs `cuga start demo`. Next step is to find
what `--events` (or the events mount) does to register `MCP_SERVERS_FILE` with the registry, and do
the same on the vanilla path. **Not diagnosed further.**

Split status: transport ✓, UI wiring ✓, CORS ✓, supervisor loading ✓, **sub-agent tools ✗**.
Combined remains the supported, fully-green topology.

## 20. The split closes (2026-08-04, third pass) — the roster belongs to whoever executes

§19's tool gap was the CLI: `cuga start demo` (no `--events`) hard-set `MCP_SERVERS_FILE=none`,
clobbering the value Code Engine had passed in, so cuga-core served only `digital_sales` and every
roster agent came up tool-less. `src/cuga/cli/main.py` now keeps FILE mode whenever a roster or an
explicit `MCP_SERVERS_FILE` is present, and only falls back to `none` for a plain demo start.
Verified: cuga-core `/api/apps` lists all seven `cuga_*` servers and `/run` answers a live price.

That left three defects, all the same root cause — **the events layer was guessing at a roster it
does not own** — plus one that only ever bit the split.

### 20.1 CUGA now publishes what it has loaded: `GET /run/agents`

The machine sibling of `/run`, guarded by the same shared secret. Returns
`{ok, supervisor, roster, agents:[{name, description, mcp_servers}]}` — `"cuga"` (the supervisor
itself, always addressable) followed by every sub-agent in the loaded roster.

Deliberately **not** `/api/agents`: that one is the dashboard's, sits behind the manage-access
cookie, and returns one UI card for the configured agent. Reusing it is what made the events side
report "1 agents" while CUGA was serving nine.

Descriptions come from the roster YAML, not from the loaded objects: `load_supervisor_config`
builds `CugaAgent`s and drops the YAML's descriptive fields, and those fields are exactly what the
concierge routes on. `/run/agents` reads `description` (falling back to `special_instructions`) and
`mcp_servers` — the same fields the in-process `SupervisorRuntime` reads, so both topologies
describe the roster identically.

### 20.2 `HttpRuntime` asks instead of assuming — and CUGA wins

`list_agents()`/`get_agent()` now resolve against `/run/agents` (60s cache; `/api/agents` then the
local store as fallbacks). Two bugs died here:

- **`?agent=incident_triage` → 404 "unknown agent".** The pinned-agent webhook named a real
  sub-agent; only this process's store was consulted, so the call failed before the supervisor ever
  got a say. This was the `HTTP 200: None` webhook failure — the hook returned 200 with a null
  answer because its inner `/invoke` had 404'd.
- **A stale local row masked the live roster.** One leftover `Digital Sales Agent` in
  `~/.cuga/events.db` beat the remote roster, because the local store was consulted first.
  Precedence is now remote-first: in a split, execution is on the CUGA side, so its roster is the
  only truth and the local store is strictly a can't-reach-CUGA fallback.

### 20.3 The pinned agent crosses the hop

`HttpRuntime.run()` sends `agent` in the `/run` body, and `/run` in supervisor mode turns a known
sub-agent name into an explicit delegation directive. It stays a directive, not a bypass — the
supervisor is still the executor, so policies, tools and HITL are unchanged, and an unknown name is
ignored rather than failing the run. Verified end to end: the pinned webhook answer comes back
signed `— incident_triage · via cuga-text`.

### 20.4 The standalone service now loads `.env`

Mounted on CUGA, the events routes inherit an environment `cuga.config` populated at import time.
Standalone, nothing had done that: `make run-events` came up with an empty `GATEWAY_TOKEN` (its
`/run` calls came back 401, and the roster read fell through to the stale local row) and no channel
tokens at all. `main()` — the process entrypoint, *not* `create_app()`, so tests never inherit live
bot tokens — now loads `.env` with `override=False`, so real environment variables still win and a
Code Engine secret can never be shadowed.

### 20.5 The capability report tells the truth in a split

`capability.report()` takes the remote roster and says
`supervisor: ON (on CUGA, over /run) — 8 sub-agent(s): …` instead of reading this process's absent
`EVENTS_SUPERVISOR` and claiming `OFF — one plain CUGA agent`.

### Result

Local split e2e: **30 passed · 2 failed · 2 skipped** (was 25 · 4). All three webhook assertions
pass. Both remaining failures are "Activepieces is not running", which is the current intended
state — AP is off by decision.

### 20.6 The roster trim had silently un-routed 31 triggers

Cutting the roster from 27 agents to 8 dropped the HANDLES lines that told the supervisor which
sub-agent owns which event. `test_every_registry_trigger_is_claimed_by_a_sub_agent` caught it:
31 registry triggers — all of github's repo events, all of gmail, box's file/folder events, most of
slack and discord, google_calendar, pinterest, youtube, rss — had no claim at all, so an armed
watch on any of them would have left the supervisor guessing.

Fixed in the roster rather than the test, by extending the HANDLES lines of the three agents that
already owned neighbouring triggers in the same app:

| agent | now also claims |
|---|---|
| `pr_reviewer` | every remaining `github/*` repo event |
| `incident_triage` | the rest of `slack/*`, `discord/*`, `box/*`, all `gmail/*`, all `google_calendar/*` |
| `webpage_summarizer` | `rss/new_item`, `youtube/new_video`, all `pinterest/*` — each is "here is a URL, summarize it" |

The one test change is the size floor (`>= 20` → `>= 5`), which encoded the old 27-agent roster;
the coverage gate itself is unchanged and now passes on the small roster.

**This only mattered because AP is off.** These are the AP-backed integration triggers, so nothing
in today's runs exercised them — the gate is what found it, which is the argument for keeping the
gate strict.

### Verified (2026-08-04/05)

| | e2e | fire | notes |
|---|---|---|---|
| Local combined | 30 ✓ · 2 ✗ · 2 – | 2/2 | 8-agent roster, 7 tool servers |
| Local split | 30 ✓ · 2 ✗ · 2 – | 2/2 | identical to combined |
| Offline suite | 330 passed · 46s | — | with the stack DOWN |

Both e2e failures in both topologies are the same pair of "Activepieces is not running" assertions.

`make test` must be run with nothing on :7860. The offline tests fire webhooks at
`127.0.0.1:$EVENTS_CUGA_PORT`; with the stack up those reach the live server and a 46-second suite
spends 25+ minutes on real LLM calls. `create_app()`'s deliberate repointing of that variable used
to leak into every later test in the session — now scoped by a fixture.

### 20.7 Final verification (2026-08-05) — all four deployments

| deployment | e2e / flows | fire | notes |
|---|---|---|---|
| Local combined | 30 ✓ · 2 ✗ · 2 – | 2/2 | 8-agent roster, 7 tool servers |
| Local split | 30 ✓ · 2 ✗ · 2 – | 2/2 | identical to combined |
| CE combined | 16 ✓ · **0 ✗** · 5 – | 2/2 | smoke green incl. public URL |
| CE split | 29 ✓ · **0 ✗** · 5 – | 2/2 | was 25 ✓ · 4 ✗ before §20 |

Offline suite: **330 passed in 46s**.

Every local failure is the same pair of "Activepieces is not running" assertions; CE reports the
same condition as SKIPS because AP is deliberately unconfigured there rather than
configured-but-down. Nothing else fails anywhere.

Both CE topologies run the same image, built from this tree. `cuga-core /run/agents` lists
`cuga` + 8 sub-agents; `cuga-events-svc` reports
`supervisor: ON (on CUGA, over /run) — 8 sub-agent(s)`; combined web chat answers signed by a
sub-agent (`— wiki_dive · 8.1s`), which is the roster demonstrably routing in production.

Two things that look like failures and are not:

* **CE cold start.** CE tool servers scale to zero. The first call needing a cold `cuga-*` server
  can exceed the webhook's internal timeout and return `502`; warm, the same routed webhook
  answers in ~32s naming `pr_reviewer`. A first-run webhook failure is worth re-running before
  believing.
* **`POLL (asked for CRON)`** in the fire report — the classifier arms a 1-minute "every minute"
  utterance as a poll. It fires correctly; the label is the classifier's, not a defect.

One genuine wart, unresolved and non-deterministic: one CE combined poll answered
`Current weather in Tokyo: {weather_info}` — the sub-agent emitted an unsubstituted template
placeholder instead of the value. The tick fired and delivered; only that answer's content was
junk. The same agent returned real values on every other run (local and CE split included).

## 21. Combined mode REMOVED (2026-08-05) — there is one topology

The eventing layer is a separate service. Full stop. The "combined" topology — events routes mounted
onto CUGA's own FastAPI app — is deleted, not deprecated.

It was never a design choice; it was where the feature started, before the split existed. Keeping
both meant two code paths, two deploy scripts, two sets of instructions, and a class of bug that
only ever appeared in one of them (§20: every split defect was invisible in combined, because
sharing a process hides the seam).

### What was removed

| | |
|---|---|
| `server/main.py` events mount (`register_events_routes`, stores, engine, concierge wiring) | −104 |
| `server/main.py` background-loop launcher | −14 |
| `runtime.py` `SupervisorRuntime` + `ClassicRuntime` | −131 |
| `cli/main.py` `--events` flag and its branch | −33 |
| `scripts/run_events_server.py` (combined debug entrypoint) | deleted |
| `deploy/ce/2_deploy_app.sh` (combined deploy) | deleted |
| `EVENTS_ENABLED` / `events.enabled()` | gone — running the service IS enabling it |

**CUGA core now imports nothing from `cuga.backend.events`.** It carries no triggers, no scheduler,
no channel loops and no bot tokens. What it still owes the eventing service is exactly three things,
all plain HTTP: `POST /run`, `GET /run/agents`, and `events_api_url` on `/api/ui/config`.

### The one thing that needed replacing: main-chat arming

`/stream` used to call the concierge in-process, so `/automate …` typed in CUGA's MAIN chat box
armed a flow. Standalone, core has no concierge — and handing that utterance to the plain agent is
the silent-failure trap this whole feature exists to close (it tries to *implement* the schedule
with a loop and sleeps).

So core keeps a **forwarder**, not a concierge: `_forward_slash_to_events` POSTs to the eventing
service's `/api/concierge` and streams the reply back as a single SSE `Answer` frame. No events
import, no shared DB. Open dialogues are tracked in an in-memory set keyed off the `state` field the
service already returns, so a bare `yes` still routes there — deliberately a routing HINT, not
state: the service holds the real parked entry (10-minute TTL) and is the only thing that can arm.
Lost on restart, which costs one retype at worst.

If `EVENTS_API_URL` is unset, `_forwards_to_events` returns False and `/stream` behaves exactly as
upstream CUGA does. Vanilla CUGA with no eventing service is a supported configuration.

### One runtime

`make_runtime` always returns `HttpRuntime`. `"cuga"` survives as a legacy alias (an existing
`EVENTS_WORKER_BACKEND=cuga` in a `.env` keeps working — it means the same thing now), and an
unknown backend raises instead of silently falling back to something that quietly does nothing.

A test written for this caught a real fragility: `CugaRuntime.get_agent` called `self._store.get(…)`
unguarded, so a missing store raised *before* `HttpRuntime`'s `or AgentSpec("cuga")` fallback could
run — breaking the "the one agent is always addressable" guarantee every scheduled tick depends on.

### Deploy

`make ce-deploy` → `deploy/ce/2_deploy.sh` → both apps from one image. There is no `ce-deploy-split`
because there is nothing to distinguish it from. `make up` / `make up-noap` start both processes
locally; every harness targets the eventing service on `:8100`.

## 22. CUGA IS THE DOOR (2026-08-05) — channels enter at /run, not at the eventing layer

Every channel utterance now goes to CUGA's **`POST /run`**. CUGA decides whether it is chat or
eventing, and calls the eventing service only for the latter.

```
BEFORE                                  NOW
slack ──▶ events /invoke                slack ────┐
          └─ concierge decides          telegram ─┤
             └──▶ CUGA /run             discord ──┼──▶ CUGA /run ── decides
                                        web ──────┘        ├─ chat      → the agent
                                                           └─ /automate → events /api/concierge
```

The eventing layer was the front door for *chat*, which is backwards: chat is CUGA's job and
eventing is the exception. Inverting it means the default path never consults the eventing layer at
all, and CUGA — the thing a user thinks they are talking to — owns the decision.

### The adapters stayed put, and that is the point

The Slack webhook handler, the Telegram long-poll and the Discord gateway still live in the eventing
service: they own the sockets and the bot tokens, and moving them into CUGA would re-acquire exactly
the coupling §21 removed (bot tokens, process-wide loops, singleton scaling). They are now **pure
transport** — normalise a message, hand it to `/run`, post the answer back. Zero routing logic.

`events/cuga_door.py` is the whole client: `ask(text, channel=…, native_id=…, user=…, locus=…)`.
**Slack's Request URL is unchanged** — the receivers did not move, only the decision did.

### The rule, in one place

```
slash verb (/automate /watch /schedule /cron /poll /push /cancel)   → eventing
this thread already has an arming dialogue open                     → eventing
everything else                                                     → the agent
```

The second line carries the multi-turn arming conversation. A bare `yes`, or
`change the prompt to …`, means nothing on its own — so CUGA remembers which threads are mid-dialogue
in an in-memory set, updated from the `state` the eventing service returns (`confirm`/`needs_input`
→ open; `armed`/`cancelled` → closed). Deliberately a routing HINT, not state: the eventing service
holds the real parked entry with its 10-minute TTL and is the only thing that can arm. Lost on a CUGA
restart, which costs one retype.

`thread_id` (`gw:<channel>:<native>#<locus>`) carries both the memory locus and the delivery
address, so a flow armed from a Slack thread fires back into that thread with nobody passing a
"reply-to" anywhere. Verified: `ARMED cron for cuga → slack`.

Fires do NOT come through this door: a tick is already-decided work aimed at a known agent and needs
delivery + run-logging, so the scheduler keeps calling `/invoke`.

### Two bugs found while building it

**`EVENTS_USER_ID` died with the `--events` flag.** Deleting the CLI branch (§21) took the events
layer's defaults with it. Without `EVENTS_USER_ID=admin`, an unlinked channel sender resolved to user
`local` while the Studio and every harness browse as `admin`: a Slack-armed flow was stored in one
scope and listed from another. It armed, fired, delivered — and was invisible in the Flows tab. The
defaults now live in `service._apply_defaults()`, with tests.

**The offline suite was never hermetic.** Its inner calls target
`127.0.0.1:$EVENTS_CUGA_PORT/invoke`; with a dev stack listening, they reached a real server and made
real LLM calls, so a 50-second suite took 25+ minutes and looked like a hang in whatever you had just
changed. `tests/events/conftest.py` now points every loopback seam at a closed port for the session.
Proof: **339 passed in 75s with the stack up on both ports.**


### 22.1 Three defects found by chasing one "failing" channel-arm test

The Slack channel-arm harness kept reporting `replied "yes" in-thread but nothing armed`. The
product was arming correctly the whole time — but chasing it turned up three real problems, only
one of which the test was written to catch.

**1. The harness raced itself.** It posts the human's `yes` with the *bot* token, so Slack
attributes that message to the bot; `_slack_wait` counted it as "the bot replied" and returned
before the arm finished. It only surfaced now because the extra HTTP hop made arming slower.
Fixed with `seen + 1`.

**2. The harness demanded a NEW subscription.** Arming legitimately REUSES an equivalent flow
(same agent, cadence, sink, owner) — `REUSING existing flow … (subscription cuga-xxxx)`. The
harness now takes the id out of the confirmation and accepts a reused flow.

**3. `find_by_dedup_key` ignored the tenant — a real isolation leak.** The reuse lookup searched
every tenant, so arming could answer *"REUSING existing flow (subscription X)"* where X belonged to
somebody else: invisible in the caller's Flows list, undeletable by them, still delivering to the
other tenant's channel. Observed live — a flow owned by `default/default/local` was handed to
`default/default/admin`, which had zero flows and was told one had been reused. Now scoped at all
three call sites (`scope=p.scope`), with an unscoped fallback for callers that have no principal.

Also hardened: `_SLASH_VERBS` tolerates a leading `<@mention>`. Slack and Discord normally strip it
before we see the text, but that depends on a bot-id lookup succeeding — and if it ever fails,
`<@U123> /automate …` silently becomes ordinary chat and the agent tries to *implement* the
schedule. That is precisely the silent failure this feature exists to prevent.

### 22.2 CUGA stands alone — proven, not asserted

| Scenario | Behaviour | Test |
|---|---|---|
| eventing never deployed (`EVENTS_API_URL` unset) | forward disabled; `/run` + `/stream` behave exactly as upstream CUGA — a slash verb is just text | `test_cuga_is_standalone_when_no_eventing_service_is_configured` |
| eventing configured but DOWN | chat unaffected (never calls out); arming returns an honest sentence naming the unreachable URL | `test_cuga_degrades_gracefully_when_the_eventing_service_is_down` |
| the arming gate | releases on `armed`/`cancelled` — a thread is never hijacked permanently | `test_an_open_dialogue_closes_when_the_flow_arms` |

The coupling is one variable and one direction: CUGA imports nothing from `cuga.backend.events`
(enforced by `test_the_events_package_no_longer_imports_cugas_graph`'s sibling check), and the only
link out is an HTTP POST guarded by `EVENTS_API_URL`. The dependency runs the other way — the
eventing service needs CUGA, because CUGA executes every agent call.

`/stream` is unchanged for chat and returns the arming card as a single SSE `Answer` frame, so the
UI cannot tell the two apart.


### 22.3 The gate leaked — two matchers, one rule (2026-08-05)

Code Engine caught the most serious defect of the day, and it was self-inflicted:

```
✗ channel-arm/slack — a subscription was armed BEFORE the human confirmed — the gate leaked
  response: "Armed poll for cuga → runs every 1 minute, sending you the Bitcoin price"
```

A flow armed that **no human had approved** — the one safety property this feature exists to
provide.

**Cause.** The decision is made in two places: CUGA's door (`_SLASH_VERBS`, "is this arming?") and
the concierge (`_slash_parse`, "which verb is it?"). Hardening the door to tolerate a leading
`<@mention>` — without doing the same to the parser — made the door strictly MORE permissive.
Mention-prefixed slashes were forwarded, missed by `_slash_parse`, and fell through to the NL
pre-router, **which arms directly, with no confirmation card**.

It only appeared on CE because Slack's mention-strip depends on a bot-id lookup that behaved
differently there — exactly the condition the hardening was meant to survive.

**Fix.** Both matchers tolerate the same shapes, and a test fails if either drifts:

```python
for text in forwarded_by_the_door:
    assert _SLASH_VERBS.match(text)         # the door forwards it
    assert _slash_parse(text) is not None   # …so the parser must claim it
```

The one legitimate asymmetry is documented in its own test so nobody "fixes" the lockstep by
breaking it: `/cancel` is the door's verb only — the arming GATE handles it (it drops a parked
draft), not the parser.

**A second lesson, cheaper but sharper.** The first version of that test did
`from cuga.backend.server.main import _SLASH_VERBS`. That import pulls the entire CUGA server in,
and its module-level side effects broke **17 unrelated tests** in the same session while passing in
isolation. The test now lifts the pattern out of the source file. Nothing in `tests/events/` should
import CUGA's server.

**Design note.** Two copies of one rule is the actual smell here; the lockstep test is a guard, not
a cure. The rule cannot live in `events/` (CUGA must not import it) so a shared home would need a
third, dependency-free module. Worth doing if a third matcher ever appears.
