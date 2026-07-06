# Phase 1 & 2 — what's built, how to test it, the pitch

The one-page answer: what's done, how to prove it (backend + UI), the examples you can click, and
how to present it. Model details: [decisions/0005](decisions/0005-runtime-router-over-prebuilt-agents.md)
(runtime router) + [decisions/0006](decisions/0006-auth-connection-model.md) (auth/connect).

---

## The model in one breath
**Builders** create agents (skill + MCP tools + policies) and enable the **channels &
integrations** each may use (shared vs per-user). **End users** chat; the **concierge routes**:
answer-now via an existing agent · reuse/create a **flow** · **decline** if nothing fits. **CUGA**
workers do the reasoning + call MCP tools. **Activepieces** owns triggers/delivery/connections.
Per-user integrations → the user **logs in** with their own account (CUGA hosts OAuth, AP holds
the token). All behind `EVENTS_ENABLED` (off → CUGA unchanged).

## Laundry list (built + verified)
- **AgentRuntime port**, workers default to **CUGA** (per-agent `DynamicAgentGraph` via CUGA's real
  `AgentLoop`; no silent react fallback). Live: `A: 61319`.
- **CUGA workers load MCP tools** from CUGA's registry — all 7 event-agent-ap servers. Live: a
  worker called `cuga_finance.get_crypto_price` → real price.
- **Concierge = router over PRE-BUILT agents** (`list_capabilities` · `answer_now` ·
  `find_or_create_flow`); **no agent creation**; declines when nothing fits. Live e2e 6/6.
- **Agent↔connector binding** — `AgentSpec.channels` + `AgentSpec.integrations=[{app,ownership}]`;
  seeded demo fleet (`seed.py`, `EVENTS_SEED_AGENTS=1`).
- **Flow dedup / grain follows credentials** — `(agent, source, cadence, sink, owner-scope)`;
  shared→tenant-wide, per-user→per-user. Live: repeat request **reused** the flow.
- **Per-user connect (CUGA hosts OAuth, AP holds token)** — `/api/events/connect/<app>` (+callback),
  `/connect/<app>/token`, `/connections`. Token path **live against real AP** (Telegram →
  `ea::default::local::telegram`). OAuth path built + degrades cleanly.
- **AP timer watchers** (CRON/POLL) + subscription index + `/invoke` seam + delivery.
- **Identity, profiles & permissions** (decision 0007) — a local **user store** (admin/alice/bob) +
  reuse OIDC; an **identity map** `(tenant, channel, native_id) → user` with profile-issued
  **link tokens** (Telegram `/start`, Discord code); **per-agent permissions** (`AgentSpec.access`);
  agents are **tenant-shared**, run-state is **per-user**. Live e2e **8/8**.
- **Isolation** (per tenant/user), **statefulness** (stateless fleet), **Studio UI** (dumb;
  Concierge/Channels/Integrations/Flows/Examples/**Profile**/**Admin** + Connect + Link actions).
  Opt-in, non-breaking.

---

## How to test — BACKEND (backend first, understand each step)
| Run | Expect |
|---|---|
| `python3 tests/events/test_events_core.py` | **14/14** |
| `python3 tests/events/test_events_dimensions.py` | **14/14** — connectors, catalog, credentials, isolation, **seed**, **dedup**, **grain-follows-creds**, **oauth registry** |
| `.venv-events/bin/python tests/events/test_events_studio_api.py` | **5/5** — Studio endpoints |
| `.venv/bin/python tests/events/live_cuga_worker_check.py` | CUGA worker executes + answers |
| `.venv/bin/python tests/events/live_cuga_worker_mcp_check.py`¹ | worker calls `cuga_finance` → `A: 61397` |
| `.venv/bin/python tests/events/live_server_e2e_check.py`² | **6/6** router e2e: answer-now → **flow create** → **reuse (dedup)** → **decline** |
| `.venv/bin/python tests/events/live_identity_check.py`² | **8/8** identity e2e: 2-user profiles · **per-agent permissions** · **channel account-linking** |
| `.venv-events/bin/python tests/events/live_phase2_watchers.py` | AP CRON watcher fires → deliver |
| `.venv-events/bin/python tests/events/live_isolation_check.py` / `…statefulness…` / `…credentials…` | isolation / fleet / shared-vs-per-user |

¹ registry up (MCP_SETUP.md). ² registry + server with `EVENTS_SEED_AGENTS=1` (see the test docstring).

**Token connect against real AP** (per-user login, no OAuth app needed):
```bash
curl -X POST localhost:8100/api/events/connect/telegram/token \
  -H 'content-type: application/json' -d '{"token":"<your telegram bot token>"}'
curl "localhost:8100/api/events/connections"   # → your ea::…::telegram connection
```

## How to test — FROM THE UI (so it settles in your mind)
The Studio is a **pre-built** webpack bundle; rebuild + republish it (and restart the server)
after any `.tsx` change:
```bash
scripts/frontend_build.sh    # pnpm install + build + publish → src/cuga/frontend/dist
# one command brings up registry + tunnels + the CUGA server on :8100:
scripts/events_up.sh
```
Open **http://localhost:8100/studio** (or `/manage` → **Studio**). What each tab does (and the
call behind it):
- **Concierge** — type an utterance → the router answers-now / arms a flow / reuses / declines
  (`POST /api/concierge`). Preview toggle = dry-run plan.
- **Examples** — click **Try it** to drop an utterance into Concierge (the list below).
- **Flows** — armed subscriptions appear here after you arm one (`GET /api/events/subscriptions`).
- **Integrations** — gmail/box/github/slack + a **Connect** button: OAuth apps open the login
  popup; token apps prompt for a token (`/api/events/connect/*`). Shows real connection status.
- **Channels** — web/telegram/discord/slack status.
- **Agents** — reuse CUGA's existing manage UI (builder creates agents there).

### The examples enabled (Studio → Examples)
| Utterance | Outcome | Agent |
|---|---|---|
| "what is the current price of bitcoin in usd?" | **answer-now** | pricebot (cuga_finance) |
| "what is the capital of Japan?" (+ "and its population?") | **answer-now** + memory | geobot |
| "what's the weather in Tokyo right now?" | **answer-now** | weatherbot |
| "3 latest arXiv papers on mixture-of-experts" | **answer-now** | papers |
| "every 1 minute send me new arXiv papers on mixture-of-experts" | **flow (cron)** — reuse on repeat | papers |
| "every weekday at 8am send me a market brief" | **flow (cron)** | market_briefer |
| "watch bitcoin every 2 minutes and ping me on any move" | **flow (poll)** | pricebot |
| "summarize my gmail every morning" | **connect** → log in to your Gmail | mailbot |
| "book me a flight to Tokyo next Friday" | **decline** — ask a builder | — |

---

## The pitch (how to present Phase 1 & 2)
**One line:** *A builder sets up agents once; anyone can then just say what they want on Telegram
or the web, and CUGA figures out whether to answer now or wire a standing automation — reusing
what exists, logging you into your own accounts when needed, and never doing anything CUGA's core
wasn't built to do.*

**The shape:**
1. **Builders build agents** (skill + tools + policies + which channels/integrations they may use).
2. **Users chat; the concierge routes** — answer-now, reuse-or-create a flow, or decline. It never
   invents agents.
3. **CUGA does the reasoning** and calls the real MCP tools; **Activepieces** owns triggers &
   delivery.
4. **You log in with your own account** — the builder enables Gmail; you OAuth *your* Gmail at
   first use (CUGA hosts the login, AP keeps the token). Flow grain follows the credential.
5. **Multi-tenant, stateless, non-breaking** — one flag; off → vanilla CUGA.

**Why it's credible:** **33 offline checks** + a live suite — real CUGA workers calling real MCP
tools, a real Activepieces watcher, a **full HTTP router e2e (6/6)**, and a **real per-user
connection** created in AP. One `trace_id` end to end. The tests even caught real bugs (the
`/api/concierge` handler shadowing, the hyphen-in-tool-name `NameError`).

**The money demo:** on Telegram, *"price of bitcoin?"* → **$61,444** (a CUGA agent called a live
tool); *"every morning summarize my gmail"* → *"connect your Gmail"* → you log in → it's armed —
*your* inbox, *your* token. Same portal, two outcomes, zero hand-configuration.

**Since Phase 1 & 2 (now wired):** two-way **channel inbound** (channel·message → `/invoke` →
concierge router → channel·send) and **PUSH** integration flows (`create_push_flow`, the Box resume
watcher) are wired end-to-end; **scheduled/poll flows now DELIVER** (cron/interval → `/invoke` →
channel send back to the caller's native id — the concierge infers the delivery channel from the
origin thread `gw:<channel>:<native>`). **Live-verified: only the Telegram round-trip.** Discord and
Slack are wired from AP piece metadata but **not yet live round-trip-tested**. Slack's
`send_channel_message` requires a constant `sendAsBot=true`.

**Still next:** the builder's connector-config UI; live Discord/Slack round-trips; real Gmail/Box
OAuth once the platform's OAuth app is registered.
