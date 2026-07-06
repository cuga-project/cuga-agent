> **OBSOLETE (2026-07-03):** this is a **pre-code, pre-router** design walk-through. It predates the
> runtime-router model (decisions 0005–0007): the concierge no longer creates agents, so the
> `provision_agent` / `create_subscription` tool names and the "provisioning" rows below are
> superseded by `list_capabilities` / `answer_now` / `find_or_create_flow`. Verified current
> utterances + outcomes live in [PHASE_1_2_ACCOMPLISHMENTS.md](PHASE_1_2_ACCOMPLISHMENTS.md). Kept
> for history (design rationale + the 27-utterance coverage proof).

# Examples — design conformance test

Running the [DESIGN.md](DESIGN.md) against real examples (box_qa Q&A + the resume watcher, and
a spread across channels/integrations/trigger-modes from the refactor catalog). Since there's
no code yet, this is a **design-level walk-through**: for each utterance we resolve the
`source → skill → sink` triple, the `agent_id` + backend, the AP flow, the `thread_id`, and
flag any gap. The point is to prove the design covers them — and to surface what it *doesn't*.

Legend: **backend** = which `AgentRuntime` adapter (cuga | react). **thread_id** shows context
preservation. ✅ handled · ⚠️ handled with a caveat.

---

## A. box_qa — Q&A tab (conversational, NOW)
**Utterance:** *"what do my Box files say about the Q4 roadmap?"* (web or Telegram)

| Field | Resolution |
|---|---|
| source → skill → sink | channel(web/telegram) → box_qa skill → same channel |
| trigger | **NOW** — rides the standing inbound flow (channel) / web SSE; no new AP flow |
| agent_id · backend | `box_qa` · **cuga** (wants CUGA tools/knowledge) *or* react |
| tools | Box tools (`list_box_folder`,`get_file_content`) + mcp-text `extract_text` |
| flow | web: SSE; telegram: `Telegram·New Message ▸ /invoke ▸ Telegram·Send` |
| thread_id | the chat id → CUGA memory → **follow-ups keep context** ("and the Q3 one?") |
| verdict | ✅ pure channel-NOW; reads happen in the worker, AP not involved |

## B. box_qa — Resume Watcher (integration event, PUSH)
**Utterance:** *"when a resume lands in the hiring Box folder, email me the fit vs the JD"*

| Field | Resolution |
|---|---|
| source → skill → sink | integration(box) → resume_judge → integration(gmail) |
| trigger | **PUSH** — Box `New File` (instant/webhook) |
| agent_id · backend | `resume_judge` · **react** (lightweight reactor) or cuga |
| tools | Box tools + mcp-text (worker reads bytes) |
| flow | `Box·New File ▸ /invoke(resume_judge) ▸ Router⟨MATCH⟩→Gmail·Send / ⟨SKIP⟩→stop` |
| thread_id | **stable per-subscription** id → each watcher accrues its own context |
| provisioning | concierge: `provision_agent(resume_judge)` → `AgentRuntime.upsert_agent`; `create_subscription(push, box/new_file)` → AP flow |
| verdict | ✅ the canonical case. ⚠️ Box auth in two places (AP trigger + worker reads); AP Box piece has no read action (by design, reads stay in worker) |

Concrete flow JSON already drafted: `~/explorations/event-agent-ap/refactor/examples/sample_flow.box-resume-gmail.json`.

---

## C. Full catalog run-through (all 27 from refactor/examples/utterances.html)
Every example from the catalog, mapped to the design. `bk` = backend (c=cuga, r=react).
`thread` = chat (conversational, follow-ups keep context) · sub (stable per-subscription) ·
req (per-request). box_qa Q&A = **#1**; the resume watcher = **#14** (detailed as A/B above).

**T1 — Conversational (NOW)** · trigger = channel message · thread = chat
| # | Utterance | agent · bk | AP flow | Verdict |
|---|---|---|---|---|
| 1 | Box files about the Q4 roadmap? | box_qa · c | `Telegram·NewMsg ▸ /invoke ▸ Telegram·Send` (web: SSE) | ✅ reads in worker |
| 2 | @assistant summarize this thread + action items | summarizer · r | `Slack·NewMention ▸ /invoke ▸ Slack·Send(thread)` | ✅ |
| 3 | !ask what does the runbook say about DB failover | runbook_qa · r | `Discord·NewMsg ▸ /invoke ▸ Discord·Send` | ✅ ⚠️ msg-content intent |
| 4 | 3 latest arXiv papers on LLM agents | research · r | `WhatsApp·NewMsg ▸ /invoke ▸ WhatsApp·Send` | ⚠️ WA 24-h window |
| 5 | text me a bug → open a GitHub issue, confirm | bug_filer · r | `Telegram·NewMsg ▸ /invoke ▸ GitHub·CreateIssue + Telegram·Send` | ✅ |

**T2 — Scheduled (CRON)** · trigger = clock · thread = sub
| # | Utterance | agent · bk | AP flow | Verdict |
|---|---|---|---|---|
| 6 | weekday 8am overnight support digest → #support | support_digest · c | `Schedule 0 8 * * 1-5 ▸ /invoke ▸ Slack·Send` | ✅ tickets via worker tool |
| 7 | every morning email summary of new CVs in Box | cv_summarizer · c | `Schedule ▸ /invoke(reads Box) ▸ Gmail·Send` | ✅ Box = tool, not trigger |
| 8 | Fri 5pm WhatsApp me this week's closed issues | issue_reporter · r | `Schedule 0 17 * * 5 ▸ /invoke(GitHub read) ▸ WhatsApp·Send` | ⚠️ WA template |
| 9 | every morning post my Outlook calendar to Slack | cal_poster · r | `Schedule ▸ /invoke(Graph cal read) ▸ Slack·Send` | ⚠️ M365 |

**T3 — Event reactions (PUSH)** · trigger = app event · thread = sub
| # | Utterance | agent · bk | AP flow | Verdict |
|---|---|---|---|---|
| 10 | PR opens → review, comment RISKY/OK, Slack if risky | pr_reviewer · r | `GitHub·NewPR ▸ /invoke ▸ Router⟨RISKY⟩→Comment+Slack /⟨OK⟩→Comment` | ✅ ⚠️ big diffs → summarize |
| 11 | issue labeled 'bug' → triage + assign owner | issue_triager · r | `GitHub·NewIssue(label) ▸ /invoke ▸ Comment + CustomAPI(assign)` | ✅ ⚠️ assign via Custom API |
| 12 | customer email → draft reply + Slack heads-up | mail_triage · c | `Gmail·NewEmail ▸ /invoke ▸ Gmail·Draft + Slack·Send` | ✅ ⚠️ tune query |
| 13 | high-importance Outlook mail → Telegram summary | mail_summary · r | `Outlook·NewEmail(high) ▸ /invoke ▸ Telegram·Send` | ✅ ⚠️ M365 |
| 14 | resume lands in Box → email me the fit vs JD | resume_judge · r | `Box·NewFile ▸ /invoke ▸ Router⟨MATCH⟩→Gmail /⟨SKIP⟩→stop` | ✅ canonical (see B) |
| 15 | #incidents post → classify; SEV1 → page on-call | incident_classifier · c | `Slack·NewMsg ▸ /invoke ▸ Router⟨SEV1⟩→HTTP(pager)+Slack /else→Slack` | ✅ ⚠️ pager via HTTP |
| 16 | lead-form webhook → enrich → CRM + Slack | lead_enricher · r | `Webhook·Catch ▸ /invoke ▸ HTTP(CRM) + Slack·Send` | ✅ |
| 17 | WhatsApp FAQs; refunds → escalate to #support | wa_support · c | `WhatsApp·NewMsg ▸ /invoke ▸ Router⟨refund⟩→Slack /else→WhatsApp` | ⚠️ WA window |

**T4 — Watchers (POLL)** · trigger = timer, emit-on-change · thread = sub
| # | Utterance | agent · bk | AP flow | Verdict |
|---|---|---|---|---|
| 18 | status page every 5 min; down → alert #ops | status_monitor · r | `Schedule/5m ▸ /invoke(fetch; on-change) ▸ Slack·Send` | ⚠️ change-state per sub |
| 19 | watch IBM; Telegram on >2% move | pricebot · r | `Schedule/15m ▸ /invoke(suppress no-op) ▸ Telegram·Send` | ⚠️ change-state per sub |
| 20 | watch blog; DM new posts mentioning 'acquisition' | blog_watch · r | `RSS·NewItem ▸ /invoke(filter) ▸ Telegram·Send` | ✅ RSS dedups |
| 21 | Slack me when a new langchain release ships | release_watch · r | `GitHub·NewRelease ▸ /invoke ▸ Slack·Send` | ✅ |
| 22 | call API nightly, diff, email what changed | api_differ · r | `Schedule/daily ▸ /invoke(fetch; diff vs stored) ▸ Gmail·Send` | ⚠️ needs stored prior state |

**T5 — Routers & fan-out (PUSH)** · thread = chat/sub
| # | Utterance | agent · bk | AP flow | Verdict |
|---|---|---|---|---|
| 23 | route Slack DMs: billing→email, bug→issue, else answer | dm_router · c | `Slack·NewDM ▸ /invoke ▸ Router: billing→Gmail / bug→GitHub·Issue / else→Slack` | ✅ multi-sink |
| 24 | high-priority email → fan out to WhatsApp AND Telegram | alert_fanout · r | `Gmail·NewEmail ▸ /invoke ▸ WhatsApp·Send + Telegram·Send` | ⚠️ WA template |
| 25 | GitHub issue → Slack + create Linear ticket | issue_bridge · r | `GitHub·NewIssue ▸ /invoke ▸ Slack·Send + HTTP(Linear)` | ⚠️ Linear via HTTP |

**T6 — Webhook / HTTP glue**
| # | Utterance | agent · bk | AP flow | Verdict |
|---|---|---|---|---|
| 26 | webhook that answers support Qs from docs, returns answer | doc_qa · r | `Webhook·Catch ▸ /invoke(knowledge) ▸ Webhook·Return` (thread=req) | ⚠️ ~30 s AP timeout |
| 27 | monitoring POSTs an alert → summarize + page right team | alert_router · r | `Webhook·Catch ▸ /invoke ▸ Router by service → Slack / HTTP(pager)` | ✅ |

**Result: 27/27 resolve to the one flow shape.** 0 need a bespoke endpoint or a CUGA core
change; 8 carry a known caveat (WA templates ×4, M365 ×2, HTTP-fallback ×3, change-state ×3,
webhook-timeout ×1 — some overlap), none architectural.

---

## What this proves about the design
1. **Every example resolves to the same shape:** `trigger (source × cadence) ▸ POST /invoke
   (AgentRuntime.run) ▸ sink`. No example needed a bespoke endpoint or a CUGA core change.
2. **Both backends exercised** — **cuga** where policies/knowledge/tools/routing matter
   (#1, 6, 7, 12, 15, 17, 23), **react** for lightweight reactors (the other 20). The
   `AgentRuntime` port carries both cleanly.
3. **thread_id preserved everywhere** — chat id for conversational NOW (follow-ups work),
   stable per-subscription id for standing triggers (watchers accrue context). Nothing bypasses
   CUGA's memory.
4. **Multi-agent used, not bypassed** — each example is a distinct `agent_id`, run on demand via
   `get_or_build_agent_graph` (cuga) or `create_react_agent` (react).
5. **AP owns all connections/triggers/delivery** — CUGA never stores an integration credential.

## Gaps / caveats the walk-through surfaced (nothing blocking)
- **Runtime arbitrary `agent_id`** — requires the `get_or_build_agent_graph` helper (verified:
  ~150 LOC, additive, `/stream` untouched). This is the one new runtime piece.
- **Emit-on-change state (POLL, #4)** — needs per-subscription "last value" storage (small; put
  it on the subscription row or via the worker's `get_state/set_state` tools).
- **Synchronous webhook (#6)** — bounded by AP's ~30s response timeout; long jobs must go async
  and deliver on another channel.
- **AP piece coverage** — thin pieces fall back to Custom API Call / HTTP (Box reads → worker;
  no native Linear/PagerDuty → HTTP). Verify per integration before promising a flow.
- **WhatsApp (#9)** — outbound outside the 24-h window needs pre-approved templates.
- **Two-place auth for source+read apps (Box)** — AP connection for the trigger, worker tools
  for content. Accepted trade-off.

## Conclusion
The design handles box_qa (both tabs) and the full channel/integration spread with **one flow
shape, two agent backends, and zero CUGA core changes beyond the additive `get_or_build`
helper**. The gaps are known and scoped, not architectural.
