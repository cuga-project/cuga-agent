# Triggers + Actions — Finalized Design (BUILT — Gmail pilot)

**Status:** ✅ v1 built for the Gmail pilot · **Branch:** `feat/events_1` · **Date:** 2026-07-18

## Build status (what shipped)

| Step | File(s) | State |
|---|---|---|
| Action registry | `events/actions.py` (Gmail: send_email/reply_to_email/create_draft_reply) | ✅ |
| Generator | `scripts/gen_actions.py` (drafts rows from live `GET /api/v1/pieces/<piece>`) | ✅ |
| `action_step` renderer + `send_step` fold | `events/flows.py` | ✅ |
| `resolve_action` (tool-first/AP-fallback) | `events/actions.py` | ✅ |
| Branch predicates (Option B) + assembler + approval | `events/flows.py` (`router_step`, `build_action_tail`, `approval_step`) | ✅ |
| Concierge action gate + verb-alignment + follow-ups | `events/concierge.py` (`find_or_create_flow`) | ✅ |
| **NL on-ramp** — `actions.extract_action` wires fast-path/slash/LLM to the gate (the fix that made chat utterances actually ACT, from any channel) | `events/actions.py` + `events/concierge.py` | ✅ |
| same-app guard (reply/draft need the app's own trigger) + box-direct honest message | `events/actions.py` (`same_app_trigger`) + `events/concierge.py` | ✅ |
| Live arming (sequential action steps) | `events/ap_engine.py` (`create_push_flow(actions=…)`, `_action_op`) | ✅ |
| dedup_key += action (sink dropped when acting) | `events/concierge.py` | ✅ |
| Examples + slides | `events/catalog.py` (act-gmail-*, act-github-email) regenerated | ✅ |
| Tests | `tests/events/test_events_actions.py` (21) + `test_events_action_gate.py` (5) + `live_gmail_action_e2e.py` | ✅ 224 offline pass |

### Remaining backlog (v2)

| # | Item | Size | Note |
|---|---|---|---|
| 1 | Live **branched** arming | large | LLM `branches` vocab + `ap_engine` ROUTER ops. Offline builder + Option-B predicates already done + tested. |
| 2 | **Multi-action** sequential in the concierge | small | `build_action_tail` chains a list; the concierge sends ONE action — loop it. |
| 3 | Run-time **approval** wired into arming | small | `approval_step` built; the gate must insert it for `destructive`/opt-in. No destructive Gmail action ships, so untested live. |
| 4 | **Tool-first** proof (D3) | small | equip one agent with a real action tool → exercise `resolve_action`'s tool branch live. |
| 5 | **Nested** branches | medium | gated on the step-0 AP nested-router probe. |
| 6 | Non-Gmail **action rows** | trivial | run `gen_actions.py` for github/box/slack/discord/telegram → verify → commit (DATA only). |

### Gmail "golden" pass (2026-07-19)

Native Gmail actions are complete and live-verified:
- **send_email / reply_to_email / create_draft_reply** — all arm as VALID + ENABLED AP steps
  (found the AP gotcha: `send_email` requires EVERY declared prop present, optionals as typed
  empties — `render_params` now emits them all).
- **Multi-action** — "email me a summary AND reply to the sender" arms a valid 2-action chain
  (`extract_actions` + span-dedup so "draft a reply" stays ONE action).
- **subject / cc from NL** — "…with subject 'X' cc a@b.com".
- **Return-to-caller** — armed from Slack → the answer posts back to Slack (direct adapter, proven)
  AND for AP channels (Telegram) an AP send step is appended.
- **archive / mark-read / delete** (custom_api_call, the piece has no native action) — the raw-call
  step does NOT validate as an armable AP step, so they're **gated with an honest message** rather
  than arming broken flows. Deferred (see ACTIONS_TODO). Delete additionally needs run-time approval.

### Live-test status (2026-07-18, Gmail connected + on-ramp wired)

- ✅ **`live_gmail_action_e2e.py` PASS** — a chat utterance ("draft a reply…") arms a REAL AP flow
  `gmail·new_email ▸ /invoke ▸ gmail·create_draft_reply` (verified valid + ENABLED in AP: step_2 =
  `@activepieces/piece-gmail create_draft_reply` with `{{trigger.message.id}}` + `{{step_1.body.answer}}`).
- ✅ **Works from any channel origin** — armed `gmail/reply_to_email` from a Slack-origin thread.
- ✅ **ASK path live** — "email me" with no address → "Who should I send the email to?" (no silent arm).
- ✅ **`live_action_arm_probe.py` PASS** — direct-engine arm carries the action step.
- ⚠️ **Box→Gmail action** only via Box **AP mode**. Box is in DIRECT mode here (token poll), whose
  non-AP schedule flow can't carry an AP action step — the concierge says so instead of dropping it.
  The generic AP-trigger→action path (proven for gmail; identical code for github/box-AP) is the route.
- **Was broken, now fixed:** before the on-ramp, the deterministic fast-path armed a PLAIN watcher
  and silently dropped the action. `actions.extract_action` (registry-driven) now feeds the gate on
  every path.

### Where this is documented (doc index)

| Doc | What |
|---|---|
| `events_docs/plans/TRIGGERS_ACTIONS_DESIGN.md` | THIS file — design, build status, backlog, decisions |
| `events_docs/actions/README.md` + `.png`s | architecture + 3 sequence diagrams (`gen_action_diagrams.py`) |
| `events_docs/setup_action.md` | setup (extends `SETUP.md`): connect the acting app, add-a-piece flow, testing |
| `events_docs/checklist_actions.html` | 28-item acceptance checklist, tagged (ACTIONS live / v2 / ask / trigger-half) |
| `events_docs/api/examples.html` + Studio Examples tab | the 3 action examples, marked with an **ACTIONS** label (`action` field) |
| memory `feedback_action_examples_label.md` | standing rule: label ACTION examples in the UX |

---

**Original design (pre-build) below.** · **Date:** 2026-07-18

This finalizes the "trigger → agent → action" work. It is grounded in the code that already
exists in `src/cuga/backend/events/` (all 39 files are NEW vs `main`; `main` is classic CUGA and
this design does not touch it).

---

## 0. The one-paragraph summary

We already have a **generic trigger half** (`triggers.py` registry, 33 triggers, generated from the
live AP catalog) and a **send-only action half** (`flows.send_step` + `CHANNELS`). The work is to
add the **missing generic action half**: an **action registry** (`actions.py`) generated the same
way from `GET /api/v1/pieces/<piece>`, a **single `action_step` renderer**, an **action-resolver**
that prefers an agent's own tool and falls back to an AP action step, a **thought-through branching
model**, and an **action validation gate** in the concierge that asks a follow-up when NL→flow is
not confident. Adding a new piece = **regenerate registry rows (data), write no code.**

---

## 1. What exists today (do NOT rebuild)

| Concern | Where | Genericity |
|---|---|---|
| Trigger registry | `triggers.py` — 33 rows, one `Trigger` dataclass; "adding a trigger = one row" | ✅ generated from live AP |
| Trigger → flow | `flows._piece_trigger`, `ap_engine.create_push_flow` | ✅ registry-resolved |
| Channels / sinks | `flows.CHANNELS` + `flows.send_step` | ✅ one row per channel — **but send-only** |
| Branching | `flows.router_step` + `build_push_flow(branches=…)` + `build_resume_watcher_flow` | ⚠️ works; answer-prefix only |
| NL→flow | `classify.py` (deterministic) + `concierge` LLM + `triggers.validate` gate | ✅ trigger side; ❌ no action side |
| Dedup | `subscriptions.dedup_key` + UNIQUE index | ✅ (must fold in actions) |
| Agent tools | `tools_bridge.py` = builtins + MCP servers | ✅ enumerable per agent |

**Principle already in the code (concierge.py):** *AP owns trigger + sink; the agent holds no
credentials.* Actions live in the flow, not as agent tools — unless an agent already has the tool.

## 2. The gap

`actionName` in the whole codebase is only ever `send_request` (the /invoke call), `send_email`, or
a channel send. **There is no action registry and no generic action step.** Agents declare
`HANDLES TRIGGERS:` but nothing declares actions. That is the entire scope of this work.

---

## 3. Design

### 3.1 Action registry — `events/actions.py` (mirrors `triggers.py`)

```python
@dataclass(frozen=True)
class Action:
    app: str            # "gmail", "github", "box", "slack", …
    name: str           # OUR canonical action id ("send_email", "create_issue")
    title: str          # human name (mirrors AP displayName)
    backend: str = "ap" # "ap" | "tool"   (tool = an agent already does it in-run)
    piece: str = ""     # AP piece key
    ap_action: str = "" # AP actionName (from GET /api/v1/pieces/<piece>)
    params: dict = None # {param: {"type","required","source_hint"}} — from live AP props
    slots: tuple = ()   # config the user must supply that the LLM can't infer
    phrases: tuple = () # regex fragments for the deterministic classifier ("email me", "reply")
```

Rows are **generated** by `scripts/gen_actions.py` hitting `GET /api/v1/pieces/<piece>` (the same
endpoint `ap_engine._piece_version` already calls), reading `.actions{}.props`, emitting rows with
types + required flags. You verify + commit. **Adding a piece's actions = run the generator.**

> **Reality check baked into the generator (verified live 2026-07-18):** the Gmail piece exposes
> `send_email, reply_to_email, create_draft_reply, gmail_get_mail, gmail_search_mail,
> custom_api_call`. It has **no** archive/label/delete/star action — those are only reachable via
> `custom_api_call`. The generator marks such "raw-only" capabilities so the concierge can be honest
> ("I can email/reply/draft natively; archive needs a raw API step").

### 3.2 One renderer — `flows.action_step(app, action, params, name)`

Generalizes `send_step`. Looks up the `Action` row, renders
`{pieceName, actionName, input: params}`. `send_step` becomes a thin wrapper (back-compat). Params
are **native AP templates** — `{{step_1.body.answer}}`, `{{trigger.<path>}}`, `{{trigger._raw}}` —
so there is **no custom param-resolution engine**; AP interpolates at run time.

### 3.3 Action resolver — the "tool-first, AP-fallback" rule (your requirement)

At provision time, for each desired action, `resolve_action(app, action, agent_spec)`:

```
1. Does agent_spec's bound toolset (tools_bridge: builtins + MCP servers) contain a tool
   that performs this action?   →  backend="tool":
        add NO flow step. Instead the agent DOES it in-run; we augment the agent prompt
        ("… then send the summary to {from} using your email tool").
2. Else the action exists in actions.py (backend="ap")   →  append action_step to the flow.
3. Else                                                   →  ask a follow-up (§3.5).
```

This is the generic seam: **agent tool wins; AP is the fallback.** `send email` demonstrates both —
if the agent has a send tool it uses it; otherwise the flow gets a Gmail `send_email` step. Matching
tool↔action is a name/alias + description match over the enumerated toolset (no per-piece code).

### 3.4 Branching — thought through

**Model (generic, one router):** a flow carries an ordered `branches: list[Branch]` + a fallback.

```python
@dataclass
class Branch:
    when: Predicate           # field, operator, value
    do: list[ActionRef]       # one or more action_steps (registry actions)

@dataclass
class Predicate:
    field: str                # "answer"  |  "trigger.<path>"
    op: str                   # STARTS_WITH | CONTAINS | EQUALS | GT | LT
    value: str
```

- **v1 operators:** `STARTS_WITH` + `CONTAINS` + `EQUALS` on `answer`; `CONTAINS`/`EQUALS` on
  `trigger.<field>`; `GT`/`LT` for numeric trigger fields. Renders to `router_step` conditions
  (which already speak AP's `firstValue/operator/secondValue`). `router_step` grows two operator
  mappings — no structural change.
- **The answer-as-condition contract:** when a branch tests `answer`, the concierge **injects the
  contract into the agent prompt** ("Begin your reply with one of: URGENT, NORMAL"). This is exactly
  how the resume watcher works today (`"Start your reply with MATCH or SKIP"`). For `CONTAINS` we
  don't need the contract (looser match), which is why we support both.
- **Multi-layer branching — recursive spec, phased compiler (§3.8).** The data model is a recursive
  tree from day one (a branch child can itself be an action → agent call → another branch), so the
  representation never boxes us in. The COMPILER phases: v1 = one router whose branches hold a
  multi-step sequence incl. an agent call; v2 = true nesting via nested AP routers where the runtime
  supports it, else auto-chained flows. **We verify AP's real nesting limit (build step 0) instead of
  guessing** — see §3.8.
- **Every branch action goes through `resolve_action`** (§3.3), so a branch can be an agent-tool
  action or an AP action step, uniformly.

### 3.4b Approval — TWO distinct gates (do not conflate)

**(a) Arm-time approval — the anti-"send→delete" defense (design-time).** Reuses `flowspec.py`'s
cardinal rule: *resolve to the right flow or to a question, never silently to the wrong one.* Extended
to actions:
  * The arm-time preview **names the exact action** ("I'll **send_email** … — arm it?"). A mis-picked
    `delete` shows as "I'll **delete_email**" → the user catches it.
  * Each `Action` row carries **`destructive: bool`** (delete/trash/archive/overwrite=true;
    send/reply/draft/comment=false). **Destructive actions cannot arm on `ambiguous` confidence** —
    they force explicit typed confirmation.
  * **Verb-alignment:** if the utterance verb doesn't match the chosen action's `phrases`, confidence
    drops to `ambiguous` → ask, never guess. "send email" therefore cannot silently compile to
    `delete_email`.

**(b) Run-time approval — per-fire human-in-the-loop (execution-time).** A step COMPILED INTO the
flow, before the action. Inserted only when the action is `destructive` OR the user said "ask me
before…". Built on AP's native approval (Approval piece / Gmail `request_approval_in_mail`): pings the
origin channel, waits for yes/no. Non-destructive actions (the whole Gmail pilot) get NO run-time
gate. Does not exist today — new. (The existing `pause` is admin subscription-pause, unrelated.)

### 3.5 NL→flow — the core piece, made rock-solid

Today NL→flow is **LLM-picks-slots, registry-disposes** — the LLM only chooses
`(kind, source, event, slots, prompt)`; `triggers.validate()` accepts / asks / rejects. We keep that
spine and add a symmetric **action gate**:

```
utterance
  → classify.py (deterministic slash + regex)         → kind = now|cron|poll|push
  → concierge LLM (CONCIERGE_PROMPT + trigger vocab + ACTION vocab)
        proposes: source, event, slots,  actions[], branches[]
  → triggers.validate(source, event, config)          → ask on missing slot / reject on unknown
  → actions.validate(app, action, params)  [NEW]      → ask on missing REQUIRED param / reject unknown
  → resolve_action(...) per action                    → tool | ap | ASK
  → APPROVAL step (origin channel, §3.6)               → arm
```

**How the LLM fills pieces & why it's reliable:**
- The LLM never free-forms AP JSON. It only emits **typed slots into a validated tool**
  (`find_or_create_flow(... actions=[{app,action,params}], branches=[...])`). The registries
  validate every field before anything is built — the same guardrail that makes the trigger side
  solid today.
- **Deterministic back-fill first** (like the existing repo-regex / quoted-label extraction): pull
  obvious params from the utterance before asking.
- **Confidence → follow-up.** The follow-up fires when: (a) trigger slot missing [exists today],
  (b) a **required action param** can't be filled, (c) action mapping is **ambiguous** (>1 registry
  action matches the verb), or (d) `resolve_action` returns ASK. The question is specific:
  *"When a PR opens, what should I do with it — reply on the PR, email you, or post to Slack?"* or
  *"Which agent should judge the resume — resume_judge or mailbot?"* (the "what agent does the job"
  ask you wanted). In the single-agent world the agent is always `cuga`, but the **specialist**
  ambiguity (which of the 27) is a legitimate follow-up when the trigger maps to several.
- **No silent fallback.** Unknown action/trigger raises loudly at build (matches the existing
  triggers philosophy — the old silent `new_item` fallback was deliberately deleted).

### 3.8 Multi-layer branching — recursive spec, phased/verified compiler

**Data model (never rework this):** an automation is a recursive `FlowSpec` tree:

```python
Node = AgentCall(prompt) | Action(app, name, params) | Branch(children: list[(Predicate, Node)])
# a Branch child's Node can itself be a chain that ends in another Branch → arbitrary depth
```

**Compiler (phased, honest about AP):**
  * **Build step 0 (verify FIRST):** arm a 2-level nested-router flow in the live AP and confirm the
    runtime executes it. AP's flow JSON structurally allows a ROUTER child to chain via `nextAction`
    into another `/invoke` and another ROUTER; whether the engine RUNS deep nesting is an empirical
    question we answer before committing the compiler — not an assumption.
  * **If AP runs nested routers** → compile the tree to one flow with nested routers (to the depth it
    survives).
  * **If AP caps nesting** → **auto-chain flows**: a branch's terminal step fires a synthetic trigger
    that arms the next flow. Unbounded depth, invisible to the user.
  * **v1 scope:** one router level, but each branch holds a multi-step sequence INCLUDING an agent
    call (`branch → action → /invoke → action`) — works today via child `nextAction` chaining, no
    nesting. **v2:** true nested branches (`branch → agent → branch`) gated on step 0.

So `branch → decision→action → agent → branch` IS the target; the only hidden detail is one-flow vs
chained-flows. We are not restricting the model — we are sequencing the compiler and verifying AP's
real limit.

### 3.6 Approval — per originating channel (your answer)

Reuse the existing origin seam: inbound carries `thread_id = gw:<channel>:<native>`; the concierge
already resolves replies back there. Approval is **one more return-a-question-and-wait** step —
renders "I'll `reply_to_email` on the triggering message with the summary — arm it? (yes/no)" back
to web / Telegram / Slack / Discord wherever the request originated. No new UI. Web chat, Discord,
Slack, Telegram all covered because they all flow through `run` → origin thread.

---

## 4. Genericity — "no rebuild when a piece is added"

| Adding a new piece requires | Code? |
|---|---|
| Trigger rows | **No** — `gen_triggers` (exists) |
| Action rows | **No** — `gen_actions` (new generator, one-time) |
| Rendering its trigger | **No** — `_piece_trigger` is registry-driven |
| Rendering its action | **No** — `action_step` is registry-driven |
| Tool-first resolution | **No** — matcher enumerates the agent's toolset generically |
| Branching on it | **No** — router is field/operator generic |
| Dedup | **No** — `dedup_key` gains an `actions` component once |

**Net: adding a piece = regenerate two data tables + verify. Zero renderer/flow code.** The only
genuinely new *code* is written **once**: `actions.py`, `gen_actions.py`, `action_step`,
`resolve_action`, the branch predicate mapping, and the action gate. After that it's all data.

---

## 5. Per-app trigger/action coverage (all 8 surfaces)

Web/Telegram/Slack/Discord are **channels** (where the human talks); Gmail/GitHub/Box/webhook are
**integrations** (events + actions). Slack/Discord are both (channel + trigger source).

| App | Triggers (have) | Native actions (live AP) | Notes |
|---|---|---|---|
| **gmail** | 4 | send_email, reply_to_email, create_draft_reply (+ get/search; archive/label via custom_api_call) | pilot app |
| **github** | 14 | create_issue, create_comment, etc. (gen to confirm) | richest trigger set |
| **box** | 3 | upload, move, comment (gen to confirm) | box_direct exists |
| **slack** | 8 | send_channel_message (send_step today) | direct backend |
| **discord** | 2 | sendMessageWithBot (send_step today) | direct |
| **telegram** | 1 | send_text_message (send_step today) | AP-backed channel |
| **webhook** | 1 | (sink = any HTTP) | generic net |
| **web** | (chat) | (reply in chat) | origin channel |

`gen_actions.py` fills the "native actions" column from live AP for each; the table above is the
verification checklist.

---

## 6. Validation — utterance matrix

Each row is an end-to-end test. ✅ = should work after this build; tool/AP = which path §3.3 takes.

**A. Trigger → action, single step**
1. `/automate when a PR opens on o/r, reply on the PR with a one-line risk summary` → github/new_pr → github/create_comment (AP)
2. `/automate when I get an email labeled "Invoices", reply to it confirming receipt` → gmail/new_labeled_email → gmail/reply_to_email (AP)
3. `/automate when a file lands in Box folder 123, post its name to Slack #ops` → box/new_file → slack/send_channel_message (AP)
4. `/automate when a new issue opens on o/r, email me a triage summary` → github/new_issue → gmail/send_email (tool if mailbot has send; else AP)

**B. Branching (answer-as-condition)**
5. `/automate when a resume lands in Box, judge fit and email me only if it's a MATCH` → box/new_file → router⟨answer STARTS_WITH MATCH → gmail send⟩⟨else stop⟩ *(the existing resume watcher, now via the generic path)*
6. `/automate when a PR opens, if it touches >300 lines email me, otherwise just comment "small PR"` → github/new_pr → router⟨trigger.changed>300 → gmail⟩⟨else github/create_comment⟩ *(numeric trigger-field branch)*
7. `/automate when an email arrives, if it mentions "urgent" post to Slack #urgent, else create a draft reply` → gmail/new_email → router⟨answer CONTAINS urgent → slack⟩⟨else gmail/create_draft_reply⟩

**C. Tool-first vs AP-fallback (same utterance, two agent configs)**
8. `email me a summary` with an agent that HAS an email tool → resolve_action = tool (no flow step)
9. same with an agent that has NO email tool → resolve_action = AP `send_email` step

**D. NL→flow follow-up (the "ask" path)**
10. `when a PR opens do something about it` → action ambiguous → concierge asks: *"reply on the PR, email you, or post to Slack?"*
11. `when an email arrives, label it read-later` → gmail has no native label action → concierge: *"Gmail can't label natively — do a raw API step, or reply/draft instead?"*
12. `when a file lands in Box, tell the team` → sink ambiguous → *"which channel — Slack, Discord, email?"*

**E. Channels (converse) + approval**
13. From Telegram: `/automate when o/r gets a star, ping me here` → approval asked **in Telegram**, delivery back to Telegram.
14. From Slack: same → approval + delivery **in Slack** (direct backend).

**F. Dedup**
15. Arm #1 twice → second reuses (dedup_key). Arm #1 then #1-with-a-different-action → NOT deduped (action is part of the key).

---

## 7. Are the 27 agents sufficient?

**For the reasoning + trigger side: yes.** The roster maps specialists to triggers (mailbot↔gmail,
pr_reviewer↔github, resume_judge↔box, repo_watcher, incident_triage, support_digest, feed_watcher…)
and covers web/text/code/finance/geo/knowledge. Every trigger in §5 has a plausible handler.

**For the "tool-first" side: not yet — one deliberate gap.** No sub-agent currently carries an
integration **action** tool, so tests C-8 (tool path) can't pass until at least one agent is given a
real action tool (e.g. wire an MCP server that exposes a Gmail-send tool to `mailbot`, or surface
CUGA main's API-registry tools). **Everything else (all AP-action tests) works with the 27 as-is.**
Recommendation: build the AP path first (covers 13 of 15 tests), then add ONE action-tool-equipped
agent to prove the tool-first branch of `resolve_action`.

---

## 8. Build order (once approved)

| # | Step | Gates |
|---|---|---|
| 0 | **Verify AP nested-router execution** (probe live AP) | shapes 6 |
| 1 | `gen_actions.py` + `actions.py` seeded for **gmail** only (pilot) | 2,3,4 |
| 2 | `flows.action_step` + fold `send_step` into it | 5,6 |
| 3 | `resolve_action` (tool-first, AP-fallback) | 4 |
| 4 | Concierge **action gate** + follow-up questions | 5,7 |
| 5 | **Approval — two-tier**: arm-time (destructive rules + verb-alignment) + run-time gate compiled into the flow for destructive/opt-in | — |
| 6 | Generic **branch predicate** (Option B) → `router_step` mapping; compiler emits nested flow or auto-chains per step 0 | — |
| 7 | `dedup_key` += actions | — |
| 8 | Regenerate action rows for github/box/slack/discord/telegram; run the §6 matrix | — |

Dependency root is step 1; step 0 is an independent probe gating only step 6. Nothing changes `main`
or the trigger side; it is additive to `events/`.

---

## 9. Decisions — SIGNED OFF

- **D1 — Gmail action scope:** ✅ **native-only** (send_email / reply_to_email / create_draft_reply).
  No archive/label/delete in the pilot.
- **D2 — Branch operators:** ✅ **Option B** — answer conditions (STARTS_WITH/CONTAINS/EQUALS) **+
  trigger-field conditions** (EQUALS/GT/LT on `{{trigger.*}}`).
- **D3 — Tool-first proof:** ✅ **defer** — build the AP action path first; add one action-tool agent
  last to prove `resolve_action`'s tool branch.
- **D4 — Approval:** ✅ **two-tier** (§3.4b): arm-time always (stricter for destructive); run-time
  gate compiled in only for destructive/opt-in actions.
- **D5 — Multi-layer branching:** ✅ **recursive spec now, phased compiler** (§3.8); build step 0
  verifies AP nested-router execution before the compiler commits.

## 10. "Adding a new piece" contract (agreed)

New piece = **regenerate registry rows (data, via `gen_actions.py`) + add agent(s) + examples +
tests.** NO renderer / flow / resolver / router / approval code changes — those are all
registry-driven. If a new piece ever forces a code change to those, the abstraction is wrong.
