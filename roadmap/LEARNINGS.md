# Event-Driven CUGA — Learnings, Issues & Gaps

A working record from a design/review session exploring the event-driven runtime
(`src/cuga/backend/events/`) and how it reshapes the "cuga app" model. This is **not** a
spec — it captures what we learned, what's genuinely true vs. still an idea, and the open
questions worth resolving next.

Companion docs: [capabilities.html](capabilities.html) (honest technical account) ·
[workflows.html](workflows.html) (60+ use-case ideas) · [exec.html](exec.html) (overview) ·
source of truth for gaps: [../events_docs/KNOWN_GAPS.md](../events_docs/KNOWN_GAPS.md).

> **Legend for claims below:** ✅ verified in code/docs · 🧪 partially proven · 💡 design idea
> from this session (not built) · ❓ open question.

---

## 1. The core learning — the "app" collapses to tools

The central insight: on this runtime, a cuga-app no longer needs to bundle a UI, an
orchestration loop, a scheduler, and delivery code. All of that is provided by the platform.

- 💡 **An app becomes two files**: an MCP **tool server** (a few functions, often thin HTTP
  GETs) + a **seed manifest** of `AgentSpec`s wiring those tools to a trigger + channel.
- ✅ This is structurally supported: an agent *is* `AgentSpec(prompt, mcp_servers,
  integrations, channels, backend)`; the runtime owns triggers (AP), delivery (direct/AP),
  and routing (concierge). Confirmed against `runtime.py` / `concierge.py`.
- 💡 The practical payoff: a working automation is **~30 lines of config**, not a project.

### Corollaries that fell out of the discussion
- 💡 **Trigger mode is a free axis.** The same tool bag yields NOW / CRON / POLL / PUSH
  variants with zero new tools. Most existing cuga-apps were "NOW + web UI"; re-expressing
  them as scheduled/watcher/reactive multiplies them into many workflows.
- 💡 **One tool bag → many agents.** `cuga-finance` powers a market brief *and* a price-alert
  watcher; `cuga-code` powers PR review, triage, release notes, standup, DORA, security.
- 💡 **Integration × Channel is the new surface.** "Box → Slack", "Gmail → Telegram",
  "GitHub → Discord" are pure config now that the app owns no UI.

---

## 2. What we confirmed actually works (✅ / 🧪)

Grounded in `events_docs/README.md`, `ARCHITECTURE.md`, `KNOWN_GAPS.md`, and the events package.

- ✅ **Opt-in & non-breaking.** Behind `EVENTS_ENABLED`; flag off = byte-for-byte vanilla
  CUGA. Two guarded touch points in `main.py`. Import is `try/except`-wrapped.
- ✅ **Four channels live** — web, Slack (direct/signed), Discord (direct/Gateway),
  Telegram (AP). Two-way.
- ✅ **GitHub & Box** proven against real accounts (`live_github_e2e.py`, `live_box_e2e.py`).
- ✅ **All five trigger shapes** (NOW/CRON/POLL/PUSH/WEBHOOK) fire and deliver; hourly
  "GitHub-trending → Slack" runs today.
- ✅ **NL + `/automate`** create real AP flows; lifecycle (pause/resume/delete + run log)
  is driven from CUGA — operators never open the AP console.
- ✅ **61 offline tests** green (14 core + 27 dimensions + 20 Studio API), ~0.6s, no creds.
  (`make test` = `pytest tests/events`; `make test-all` for the full suite.)
- ✅ **`AgentRuntime` port** — framework swap is one adapter; backends `cuga` (default,
  fails loud, no silent react fallback), `react` (reference + the concierge itself), `stub`.
- 🧪 **Gmail** — OAuth connection live and watcher arms, but a real *fire* is unproven
  (needs an email sent to the connected inbox).

---

## 3. Issues & gaps surfaced (✅ from docs unless noted)

### 3a. Integration/runtime gaps
- 🧪 **Gmail PUSH fire leg unproven** — mechanism is AP-native and identical to Box's proven
  push path; just not exercised end-to-end.
- ✅ **Box watcher passes file *name*, not *content*** — `resume_judge` judges filename +
  metadata; reading the body needs a download step before `/invoke`.
- ✅ **GitHub PR trigger creates a repo webhook on publish** — needs a PAT with explicit
  *Webhooks: R/W* (repo-admin fine-grained PAT alone → 403). Concierge now surfaces this as
  an actionable message and auto-extracts `owner/repo` from the utterance.
- ✅ **`instance_id` effectively always `default`** — instance-level isolation is nominal.
- ✅ **Two flow-builder code paths** (`flows.py` pure-JSON vs `ap_engine.py` live REST) render
  the same shapes independently → **drift risk**. Plan: one consumes the other, or a
  shared-shape test.
- ✅ **Outlook / M365** — the one genuinely-unbuilt connector; kept as a shape.

### 3b. Security posture (before any shared/external deploy)
- ✅ **`/invoke` trusts a caller-asserted `scope`** behind the gateway token. Fine for the
  internal AP→CUGA seam; a multi-tenant public deploy needs an upstream IdP / signed scope
  claims. **This is the #1 hardening item.**
- ✅ **Open-if-unset defaults** — empty `GATEWAY_TOKEN` → open seam (warns loudly);
  webhook-key gate & Slack signature verification only enforced when their secrets are set;
  `_is_admin` returns True with no user store (dev convenience).
- ✅ **OAuth `state` is unsigned** (base64-JSON, trusted on callback) — sign/nonce before
  public OAuth. Tracked, not yet fixed.

### 3c. Operational sharp edges
- ✅ **Ephemeral tunnel URL** — every `cloudflared` restart invalidates the public URL, Slack
  Events URL, OAuth redirects, AP frontend URL. A named tunnel ends this. (Direct Discord +
  Box poll are outbound-only, unaffected.)
- ✅ `.env` is **cached at startup** — restart after any change.
- ✅ **Do NOT set `AP_WORKER_TOKEN`** — AP 0.82 mints it as a JWT; a random value crash-loops
  the worker.
- ✅ Box dev tokens expire ~60 min; Gmail (Testing) refresh tokens expire after 7 days.
- ✅ Run AP CE on **Postgres** (sqlite build wipes its own project); fresh AP must sync its
  piece catalog — a network blip → `piece_metadata_not_found` 404s; fix with `make ap-pieces`.

### 3d. The stats/tracking wrinkle we hit
- 💡 **Analytics needs history.** "Stars gained this week" / "error rate vs. yesterday" needs
  a time series, but a pure tool is stateless. Two clean resolutions, both keep the app light:
  1. **Let the source keep history** — GitHub returns the live count; the usage-collector
     already exposes daily/monthly JSON + CSV. The tool fetches a snapshot/series; the agent
     narrates the delta. **No new state.** (Verified the usage-collector exposes JSON/CSV
     exports.)
  2. **A shared `cuga-metrics` store** — generic record/query time-series, reused by every
     tracker. Shared infra, not per-app.
- **Decision this session:** lean on **source-provided history** (option 1); **do not** build
  `cuga-metrics` for now. Revisit only if a tracker needs trends the source can't provide.

---

## 4. Ideas that are NOT yet built (💡 — don't mistake for capabilities)

These came out of the ideation and are compelling, but are **design, not delivered**:

- 💡 **60+ workflow use-cases** in [workflows.html](workflows.html) — reactive, scheduled,
  watcher, tracking, enterprise, and multi-step pipelines. Illustrative, not seeded.
- 💡 **Human-in-the-loop approval (the "✋" pattern)** — "reply yes/no in Slack to approve"
  as a first-class workflow step. This was proposed as a pattern; ❓ **it is not confirmed
  that a durable approval/resume gate exists in the runtime today** (see open questions).
- 💡 **Multi-step branch/enrich/act pipelines** (Release Captain, On-Call Router, Onboarding
  Runner) — these assume the agent's reasoning carries the branch and the concierge carries
  the hop. Plausible on the current primitives, but unbuilt and untested as flows.
- 💡 **New tool servers implied** — `cuga-stats`, `cuga-docs` (docling extract/OCR),
  `cuga-cost`, `cuga-crm`, `cuga-data`, `cuga-support`, `cuga-ops`. None exist yet.

---

## 5. Open questions (❓)

1. **Approval/resume gate.** Does a standing flow support a *pause-for-human-approval* step
   that survives a restart and resumes on a chat reply? The "✋" workflows depend on it. If
   not, what's the smallest mechanism (a pending-action store keyed by thread_id)?
2. **Authoring the lightweight app.** Is there a defined **manifest format** for "tool server
   + seed AgentSpecs", or is seeding still hand-edited in `seed.py`? A first-class app
   manifest is what makes the "~30 lines" real for non-core builders.
3. **Migration of the 37 existing cuga-apps.** What's the path from a full app (UI +
   orchestration) to a tool-server + AgentSpecs? Which apps are pure-tool today vs. carry
   logic that would need to move into a prompt?
4. **State for tracking.** Confirm option 1 (source history) covers the real tracking
   use-cases; identify the first tracker that genuinely needs `cuga-metrics`.
5. **Box content leg.** Prioritize the download-before-`/invoke` step — several document
   pipelines (resume, contract, invoice) are blocked on file *content*, not name.
6. **Flow-builder drift.** Decide the fix (shared builder vs. shared-shape test) before more
   flow shapes are added.
7. **The security hardening pass.** Sequence the `/invoke` scope-trust fix (IdP / signed
   claims) relative to any shared-deploy timeline — it gates external multi-tenant use.

---

## 6. Recommended next steps (opinion, not committed)

1. **Close the two integration legs** that block the best demos: Box *content* extraction and
   the Gmail *fire* proof. Both are small and unlock the document/inbox pipelines.
2. **Define an app manifest** (tool server + seed AgentSpecs) so a builder can add an app
   without editing core — this is what makes the lightweight-app thesis usable, not just true.
3. **Prove one multi-step + approval workflow** end-to-end (e.g. Release Captain or a resume
   pipeline) to validate the "✋" pattern and the branch/hop assumptions — or discover the
   missing primitive early.
4. **Ship 2–3 tracking agents on source history** (star tracker, nightly usage digest) to
   validate option 1 before deciding on `cuga-metrics`.
5. **Schedule the security hardening pass** (`/invoke` scope trust) ahead of any shared
   external deployment; it's documented and shouldn't surprise anyone late.

---

*Sources: `events_docs/README.md`, `ARCHITECTURE.md`, `KNOWN_GAPS.md`, the events package,
the cuga-apps gallery (37 apps), and the usage-collector dashboard. Verified claims are marked
✅; ideas from this session are marked 💡 and are not delivered features.*
