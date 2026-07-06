# events_docs — the event-driven agent platform on CUGA

**Status: built + tested (Phase 1 & 2).** An **event-driven agent platform** mounted on CUGA's
FastAPI server, behind `EVENTS_ENABLED` (off → CUGA byte-for-byte unchanged). Builders create
agents in the **web** interface; end users then talk to them and set up triggers from **anywhere**
(web, Telegram, Discord, Slack); **Activepieces** owns every connection, trigger, and delivery.

## Start here
| Doc | What it is |
|---|---|
| **[PHASE_1_2_ACCOMPLISHMENTS.md](PHASE_1_2_ACCOMPLISHMENTS.md)** | **the one-pager** — what's built, how to test it (backend + UI), the enabled examples, the pitch. |
| **[DESIGN.md](DESIGN.md)** | the goal/architecture: `AgentRuntime` port (cuga + react), the endpoints, invariants, phases. |
| **[phase_1_2/](phase_1_2/)** | **sequence diagrams (+PNGs)** and **[TESTING_WALKTHROUGH.md](phase_1_2/TESTING_WALKTHROUGH.md)** (narrated, backend-first). |
| **[TEST_COVERAGE.md](TEST_COVERAGE.md)** | the test matrix (offline + live) + how to run each, with status. |
| **[HOW_TO_TEST.md](HOW_TO_TEST.md)** | terse one-by-one test commands. |

## Setup & operations
| Doc | What it is |
|---|---|
| **[SETUP.md](SETUP.md)** | **fresh-machine setup cost** + the one-command bootstrap (`scripts/events_up.sh`). |
| **[CHANNELS_SETUP.md](CHANNELS_SETUP.md)** | procure + wire Telegram/Discord/Slack + Box/GitHub (bots, tokens, tunnel). |
| **[MCP_SETUP.md](MCP_SETUP.md)** | give CUGA workers their tools via the registry (the cuga-apps servers). |
| **[STUDIO_UI.md](STUDIO_UI.md)** | the Studio (Concierge/Channels/Integrations/Flows/Examples/Profile/Admin) — dumb, additive. |
| **[TODO.md](TODO.md)** | what's done, what's next, infra/ops notes. |

## Decisions (ADRs — the source of truth for the model)
[decisions/](decisions/): 0001 AP-as-engine · 0002 tenancy/isolation · 0003 credential ownership ·
0004 endpoints · **0005 runtime router over pre-built agents** · **0006 auth/connect (CUGA hosts,
AP holds token)** · **0007 identity, profiles & permissions**.

## Reference / history
[REUSE_MAP.md](REUSE_MAP.md) (what CUGA is reused vs new) · [IMPACT_AND_COMPATIBILITY.md](IMPACT_AND_COMPATIBILITY.md)
(blast radius + the verified multi-agent finding) · [EXAMPLES_CONFORMANCE.md](EXAMPLES_CONFORMANCE.md)
(early design walkthrough vs 27 utterances — *predates the router model; kept for history*).

## The model in one breath
Builders build **agents** (skill + MCP tools + policies) and enable their **channels &
integrations** (shared vs per-user). End users chat from web/Telegram/Discord/Slack; the
**concierge routes** — answer now via a CUGA worker, reuse/create a **flow**, or decline. Per-user
integrations → the user **logs in** with their own account (CUGA hosts the connect, AP holds the
token). Isolation by `Principal → scope`; channel identity via account-linking.

## First thing to run
```bash
python3 tests/events/preflight.py     # tests every integration from .env (watsonx/AP/TG/Discord/Slack/Box/MCP)
```
