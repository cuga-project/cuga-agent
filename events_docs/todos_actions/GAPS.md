# Actions — the complete gap registry

One authoritative list of everything NOT done in the trigger→action work, as of **2026-07-20**. When
someone asks "does X work?", check here first. Area docs: [`gmail.md`](gmail.md) ·
[`branching.md`](branching.md) · [`direct_actions.md`](direct_actions.md) ·
[`../plans/CHANNEL_BACKEND_DECISION.md`](../plans/CHANNEL_BACKEND_DECISION.md).

Legend: 🔒 blocked/live-pending · 🟡 partial · ⬜ open/not started · 💤 coded but dormant

The **v1 tracked checklist** below is the finish line (see [`../roadmap.html`](../roadmap.html)); the
lettered sections after it are the full detail. Check items off here as they land.

---

## ★ v1 TRACKED CHECKLIST (the finish line — ~4 weeks)

**Definition of done:** 4 channels converse · 6 integrations watch+act (live-vetted) · 5 flagship agents
proven · deployed + blog. Everything not on this list is v2 (see §G and roadmap).

### Week 1 — Freeze & harden the core
- [ ] Fix the direct-executor **AP run-level error** (`INTERNAL_ERROR`, §D) — or formally scope v1 to
      AP-push actions and document the direct path as v2.
- [ ] Prove **Telegram return-to-caller** (§D) — the one channel delivery nuance.
- [ ] Live-vet **Gmail actions** end-to-end: real email → real draft / reply / send (§F, §J).
- [ ] Lock **credentials/security**: AP owns tokens; vault `AP_PASSWORD`.
- [x] ~~Anti-silent-failure gates + benchmark + Studio surfacing~~ (done).

### Week 2 — Breadth (the "act" half) + the Level-2 benchmark
**Tier 1 core (the DoD):**
- [ ] **GitHub actions** — create_issue / create_comment (§A, §B). `gen_actions.py github --check`.
- [ ] **Slack action** — post-message (§A, §C). Bot-token adapter (no OAuth wall).
- [ ] **Google Calendar** — trigger (event soon) + create-event action + OAuth entry (§A).
- [ ] **Google Sheets** — new/updated-row trigger + append-row action + OAuth entry (§A).
- [ ] **Box** upload action — optional, if cheap (§A).
- [ ] Each new action: board example + gate test + **Level-1** benchmark case (CORRECT-AT-ARM 100%).
- [ ] **Build the Level-2 execution harness** — arm → fire (synth/real/manual) → assert AP run SUCCEEDED →
      **verify the effect in the target app** → answer delivered. Metrics: FIRE-RATE · EXECUTION-CORRECT ·
      EFFECT-VERIFIED · DELIVERY-OK. (Today: only fragments — validity gate, `--check`, github synth.)

**Tier 2 stretch (only if core is green):**
- [ ] **Outlook / MS 365** — new-mail trigger + send action + OAuth (Gmail parity for Microsoft shops).
- [ ] **HTTP "any-API" action** — a generic request action on the AP HTTP piece (it *validates*, unlike
      Gmail's custom_api_call). The work is the **NL→request mapping** (url/method/body), not the plumbing.

**Tier 3 social (opportunistic, auth-gated):**
- [ ] **LinkedIn** create-post · [ ] **X** post — demo candy; gate on getting a usable API key/OAuth.

### Week 3 — Build & vet the 5 flagship agents
- [ ] **Inbox Concierge** (Gmail ▸ Gmail) · [ ] **PR Sentinel** (GitHub ▸ GitHub+Gmail) ·
      [ ] **Meeting Prep** (Calendar ▸ Gmail) · [ ] **Standup Digest** (cron ▸ GitHub ▸ Slack) ·
      [ ] **Lead Router** (webhook ▸ Sheets+Slack).
- [ ] **Per-agent vetting checklist** (the §J "truly working" bar): arm → real event fires → right action
      runs → answer delivered. Screenshots + logs as receipts.

### Week 4 — Ship & tell the story
- [ ] Deploy to **HuggingFace Spaces**; smoke-test hosted.
- [ ] Final security + error-handling pass; regenerate deck / API spec / checklist.
- [ ] **Blog + demo video**; squash + open the PR.

*Explicitly deferred to v2:* §A non-core pieces (Salesforce/Jira/Notion/Outlook), §C rich slack/discord
actions + HTTP-out, §E nested/numeric branches, §H approval gates, §I tool-first, WhatsApp channel.

---

## A. Action registry — only Gmail exists

- ⬜ **Only Gmail actions are registered.** `actions.py` has `send_email`, `reply_to_email`,
  `create_draft_reply`. **No slack / discord / github / box actions at all.** So any utterance whose
  ACTION is non-Gmail ("post to #general", "create a GitHub issue", "upload to Box") extracts **no
  action** — the gate arms a plain watcher or declines. This is the single biggest gap: the registry is
  the vocabulary.
- ⬜ **GitHub actions** (create_issue, create_comment, add_label…) — `gen_actions.py github --check` +
  phrases. Unlocks gmail→github, github→github.
- ⬜ **Slack actions** (send_message, upload_file, add_reaction, create_channel, update_message…, the
  ~30 on activepieces.com/pieces/slack) — see area C for the routing nuance.
- ⬜ **Discord actions** (send_message, create_channel…) — same as slack.
- ⬜ **Box actions** (upload, move, comment) — for box-AP mode.
- ⬜ **Per-app sender templates.** `send_email` "reply to sender" only resolves gmail's `from`. A generic
  `sender_field` per trigger would let any app with a real email sender work.

## B. Non-Gmail actions on AP-push triggers

- ⬜ Once B-actions are registered, `github → slack`, `gmail → github`, etc. work through the **existing**
  `create_push_flow(actions=…)` path — mostly DATA, no new engine code. Not done because the rows aren't
  written.

## C. Slack / Discord AS action targets (the piece actions)

**ASSUMING NO OAUTH WALL** (per 2026-07-20 decision to assume AP's slack/discord pieces connect cleanly),
there is **no architectural blocker and no per-integration engine code** — slack/discord actions run
through the SAME two paths already built:
  * direct trigger → slack action  →  the **executor** (`catch_webhook ▸ slack/send_message`, Option A)
  * AP-push trigger → slack action  →  the existing **`create_push_flow(actions=…)`** step
The `direct_send` bot-adapter path becomes an optional latency optimization, not a requirement.

Remaining work is REGISTRY DATA + per-action wiring (the same pattern Gmail needed), not new plumbing:
- ⬜ **Register + `--check` slack/discord action rows** (`gen_actions.py slack --check`). Some actions may
  not validate as an armable AP step (as Gmail's custom_api_call didn't) — the check tells us which.
- ⬜ **Map each action's params to sources** — e.g. slack `add_reaction` needs channel + message `ts` from
  the trigger; `send_message` needs the target channel. Uses the existing `Param(source=trigger|answer|
  user|static)` model.
- ⬜ **Thread trigger fields into the executor body** for direct triggers whose slack action references
  trigger data (CUGA has the payload; just wire the fields into `executor_body`).
- ⬜ **gmail → slack (post) as an ACTION** — falls out of the above once slack rows exist (distinct from
  delivery / return-to-caller).
- 💤 **`direct_send` (bot-adapter, message-only)** stays as an optional fast path; superseded by the
  executor for the general case.

## D. Direct-trigger → action (Option A, built 2026-07-20)

- ✅ **Arm + validity gate LIVE-verified (2026-07-20).** The real concierge arms slack→gmail with an
  executor; `ensure_action_executor` creates `exec-gmail-send-email` and AP judges it valid. Fixed two
  bugs found live: the connect-gate false-negative (direct trigger gated on the source) + `catch_webhook`
  needing `authType`. See [`direct_actions.md`](direct_actions.md).
- 🔒 **The executor RUN errors at the AP platform level.** Firing produces `INTERNAL_ERROR`,
  `stepsCount=0` (fails before the gmail step), consistently. Needs AP worker/sandbox logs (or a
  known-good webhook-flow comparison) to tell AP-instance health from the webhook-flow shape.
  **This is now the top blocker — the send does not execute on this AP yet.**
- 🔒 **Full slack→gmail (real Slack message → dispatch → send)** still unproven (Slack not connected on
  the test instance); blocked behind the run-level error above anyway.
- 🟡 **Telegram return-to-caller.** The action fires; reporting the answer BACK to the telegram chat is
  uncertain (telegram delivers via AP, not the direct adapter `deliver=True` uses; the consumed-watcher
  early-return carries no answer). Slack/Discord report-back works.
- ⬜ **box-direct → action.** box-direct arms via `create_box_poll_flow`, a separate path that returns
  before the direct-action code — still declines. Wire the poll dispatch to `run_action_plan`, or use
  box-AP.
- ⬜ **Executor cleanup.** `exec-<app>-<action>` flows are created lazily + reused but never deleted when
  the last watcher using them goes. Harmless but they accumulate.
- ⬜ **Cross-scope/tenant executors** — one per (app, action) per project; not load-tested at many tenants.

## E. Branching

- 🔒 **Live-fire correctness harness** — proven flows ARM valid; never proven AP ROUTER routes to the
  RIGHT branch at runtime. "Valid ≠ correct." Easiest to prove on a synth-fireable github trigger.
- ⬜ **Numeric conditions** ("if >300 lines", "over $500") — the operators exist (`_OP_MAP`); the NL
  parser (`extract_branches`) only does text-contains + from-address.
- ⬜ **Answer-based conditions** ("if the agent thinks it's spam") — needs auto-injecting a token contract
  ("begin your reply with URGENT/NORMAL") into the agent prompt.
- 🟡 **Nested branches** — a branch that itself branches; needs a live probe first (AP runtime support
  for a 2-level router is unconfirmed).
- 🟡 **Direct-trigger branches are Python-evaluated** (not AP ROUTER): content CONTAINS vs message+answer;
  from-address EQUALS vs sender. Fine for the common case; numeric/nested not covered.

## F. Gmail actions (native gaps)

- ⛔ **archive / mark-read / delete / label / forward / star** — no native Gmail piece action; only
  `custom_api_call`, which does NOT validate as an armable AP step. Currently GATED (honest decline).
  Needs: crack custom_api_call validity OR a piece with native modify.
- ⬜ **Attachments / reply-all / bcc from NL** — params exist on `send_email`, not wired to language.
- ⬜ **Nicer confirmation text** — channel gets the raw answer prefixed "⚡ flow fired", not "✅ replied
  to alice@… — <summary>".
- ⬜ **Verifier-mismatch parking** — a flagged mismatch asks; parking it (like recipient parking) so the
  next message resolves it.
- 🔒 **Real email → real draft/reply** — needs a human-sent email to prove the final leg.

## G. Channel / backend architecture (decision accepted, not executed)

- ⬜ **AP-default not yet the default.** The decision (web=native, else=AP, slack/discord direct
  exceptions) is documented but the trigger axis still defaults to `direct` for slack/discord; making AP
  the default + `direct` an opt-in flag is not done.
- ⬜ **Telegram trigger AP-flip** — optional (actions already work via the executor); only needed for
  real-time trigger latency.
- ⬜ **Slack AP path** — behind `EVENTS_SLACK_BACKEND=ap`, still carries the OAuth2 wall + the payload-
  eating trigger bug; unfixed (we chose direct).
- ⬜ **Discord AP path** — poll-only (~1 min floor via `AP_TRIGGER_DEFAULT_POLL_INTERVAL`); never instant.
- ⬜ **WhatsApp** — not started (deferred by user).

## H. Approval / safety

- ⬜ **Run-time approval, live.** `approval_step` is built but never inserted live (no destructive action
  ships). Needed before delete/trash can ever ship.
- ⬜ **Approval delivery to origin channel** — confirm the approve/reject prompt lands where the request
  came from and the answer resumes the flow.
- ⬜ **Verb-alignment audit** — expand the false-positive set (forward/cc/trigger words that look like
  actions) so extraction stays narrow.
- ⬜ **Destructive-action gate for the executor path** — `_approve` is stashed on linear steps but the
  direct-executor runner doesn't yet pause for approval.

## I. Tool-first path (design D3)

- ⬜ **Equip an agent with a real action tool** (e.g. an MCP gmail-send) and prove `resolve_action`'s
  tool branch live. Currently AP-fallback only.

## J. Live-verification debt (what's green offline but unproven live)

- 🔒 slack/discord/telegram → gmail (Option A executor) — offline only.
- 🔒 AP ROUTER branch routing correctness — arm-verified only.
- 🔒 Gmail real-fire (draft appears from a real inbound email).
- 🔒 box-AP → gmail (blocked on your Box OAuth).
- 🔒 Telegram return-to-caller after an action.

## K. Ops / housekeeping

- ⬜ **Live matrix script** (`live_actions_matrix.py`) running the checklist end-to-end.
- ⬜ **checklist_actions.html is stale** (pre-2026-07-20; predates direct-decline + Option A). Not a
  combinations matrix. Either regenerate or point people at these md docs.
- ⬜ **Studio: surface the action** on an armed subscription; add an "actions" filter chip to the board.
- ⬜ **PR prep** — squash the action-half commits, ensure `main` untouched, fill the PR body.
- ⬜ **Remove hardcoded** `push-gmail-new-email-cuga` test flows left by harnesses.

---

## The 3 that matter most right now
1. 🔒 **Live-fire slack → gmail once** — turn Option A from offline-green into proven.
2. ⬜ **Register slack + github actions** — unlocks the whole cross-app matrix (mostly data).
3. 🔒 **Branch live-fire correctness harness** — the "valid ≠ correct" risk.
