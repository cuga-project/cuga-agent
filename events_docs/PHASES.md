# Phases

The plan is **Crawl · Walk · Run · Sprint · Fly**. The MVP is Crawl + Walk + Run (P1–P3); Sprint
broadens the connector set; Fly is cloud & scale. Forward-looking detail is in [ROADMAP.md](ROADMAP.md);
this doc says what each phase *is* and where we are.

| Phase | | What it delivers | Status |
|---|---|---|---|
| **P1 Crawl** | 🐛 | Core runtime & the `/invoke` seam | ✅ Done |
| **P2 Walk** | 🚶 | Concierge + Activepieces + Studio + channels live | ✅ Done |
| **P3 Run** | 🏃 | **MVP** — all 4 channels + Gmail/GitHub/Box PUSH + webhook; NL→flow rigor | 🏃 ~75% |
| **P4 Sprint** | ⚡ | Breadth — new channels & integrations | Next |
| **P5 Fly** | 🚀 | Cloud & scale (multi-tenant) | Later |

## P1 · Crawl — the seam (done)

The events layer folded into CUGA's server behind `EVENTS_ENABLED`; the `/invoke` seam +
`X-Gateway-Token`; the `CugaRuntime` worker (a full CUGA graph per agent); `SubscriptionStore`
persistence; web NOW; the offline test scaffold.

## P2 · Walk — concierge & flows (done)

The runtime-router concierge (`list_capabilities` · `answer_now` · `find_or_create_flow` · `decline` —
it never creates agents); the deterministic planner (dry-run/preview, no-LLM fallback); AP flow
builders (inbound / schedule / push); credentials + identity + permissions; the Studio UI; and a full
live Telegram path (inbound + scheduled delivery).

## P3 · Run = MVP — where we are (~75%)

**Live and proven end-to-end this cycle:**

- **All 4 channels** — web, Telegram (AP), **Slack & Discord (direct)**. Slack is signature-verified.
- **PUSH integrations** — **GitHub** (OAuth connection, real repo webhook), **Gmail** (polling
  watcher, verified on a real inbound email), **Box** (direct poll + the server-side **download step**
  that hands the agent file *content*, not just a filename).
- **Generic webhook — IN** — any external system POSTs JSON → an agent triages it.
- **Schedule / poll delivery** — cron & poll flows fire and deliver (the send-step wiring was fixed).
- **Debug fire** — `POST /api/events/subscriptions/{id}/run` fires an armed flow out of band.

**The remaining P3 work — and the strategic one is NL→flow rigor:**

1. **NL→flow rigor** *(largely CLOSED, 2026-07-15)* — shipped: a typed **FlowSpec**
   (`events/flowspec.py`) + deterministic pre-router (ask-till-legit), the registry **validation
   gate** before arming, and a **47-case labeled benchmark in CI** gated on zero-wrong-at-high.
   With the single-agent world ([decisions/0009](decisions/0009-single-agent-supervisor.md)) the
   supervisor's routing is benchmarked too (`make test-delegation`). Remaining: the LLM seam
   scored the same way + a model bake-off. The flow builder is still linear
   (trigger → invoke → publish); branching/ROUTER flows are designed, not built.
2. **Webhook — OUT** — deliver an answer to any HTTP endpoint (optional HMAC); enables flow→flow.
3. **Email delivery sink** — "…email me the brief" delivers to an inbox, not a chat.

See [GAPS.md](GAPS.md) for the smaller known limitations, and [ROADMAP.md](ROADMAP.md) for the
sequenced plan.

## P4 · Sprint — breadth (next)

New connectors, no new architecture: WhatsApp, Email-as-a-channel, Google Calendar, Drive/Sheets, one
"work" tool (Notion/Jira/Linear), RSS/feeds. Fly adds **no** new connectors — it scales these.

## P5 · Fly — cloud & scale (later)

Multi-tenant cloud: real isolation, IdP/OIDC, managed infra, stable URLs, a secrets vault (replace
`.env`), observability, scale.

## Committed connectors, by phase

| Phase | Channels (converse) | Integrations (watch/act) | Delivery sinks |
|---|---|---|---|
| Walk ✅ | Web, Telegram | connect model | channel |
| **Run (MVP)** | + Discord ✅, Slack ✅ (direct) | Box ✅, GitHub ✅, Gmail ✅, generic webhook-IN ✅ | channel ✅, email ▫, webhook-OUT ▫ |
| Sprint | + WhatsApp, Email-as-channel | + Calendar, Drive/Sheets, one work-tool, RSS | (same) |
| Fly | scale existing | scale existing | — |

✅ live · ▫ remaining in Run
