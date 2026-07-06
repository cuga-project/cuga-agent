# Testing Phase 1 & 2 — a narrated walkthrough

This is the "sit down and actually test it" guide. It goes **backend first** (so you understand
what each piece does and *why* you see what you see), then **from the UI** (where you watch the
same machinery through CUGA's own Studio). Every step says **run this → what you'll see → why →
which diagram**. The sequence diagrams (PNG) live in [`png/`](png/) — open them alongside.

## The mental model (30 seconds)
```
BUILDER (design time) ─► creates AGENTS (skill + MCP tools + policies)
                         + enables their CHANNELS & INTEGRATIONS (shared | per-user)

USER (run time) ─chat─► CONCIERGE (react ROUTER over the pre-built agents)
                          • existing agent answers NOW      → run it, reply
                          • standing request                → reuse-or-create a FLOW
                          • nothing fits                     → DECLINE (ask a builder)
                          • per-user integration not logged in → CONNECT (you OAuth your account)
                             │
                             ▼
                        WORKER (CUGA) does the reasoning + calls MCP tools
                             │
                             ▼
                        ACTIVEPIECES — triggers, delivery, connections (tokens)
```
- **Builder builds agents; the concierge only routes** (never creates agents/tools) — decisions
  [0005](../decisions/0005-runtime-router-over-prebuilt-agents.md) / [0006](../decisions/0006-auth-connection-model.md).
- **Worker = CUGA** (`EVENTS_WORKER_BACKEND=cuga`) does the answering; **Activepieces** owns
  triggers/delivery/connections. One **`trace_id`** per hop; one **`scope`** isolates everything.

> The authoritative "what's built + how to test + the examples + the pitch" is
> [../PHASE_1_2_ACCOMPLISHMENTS.md](../PHASE_1_2_ACCOMPLISHMENTS.md). This walkthrough is the
> narrated backend-first run; some NOW-era phrasing below predates the router but the commands hold.

---

# Part 0 — Setup

There are **two backends you can run**, pick per what you're testing:

| Env | What it can run | When |
|---|---|---|
| **`.venv-events`** (focused, light) | offline core, react worker, concierge, AP watchers | fast loop; **workers fall back to react** (no CUGA stack) |
| **`.venv`** (full CUGA, from `uv sync`) | everything above **+ real CUGA workers** + the whole server + UI | the real thing — CUGA does the answering |

**Enabling CUGA (already done here):**
```bash
uv sync --python 3.12      # installs the full CUGA stack → .venv   (done)
```
**What CUGA needs from you (all already in `.env` at the repo root):**
- `WATSONX_APIKEY` / `WATSONX_URL` / `WATSONX_PROJECT_ID` — the LLM CUGA's agents use.
- `EVENTS_ENABLED=1` — mounts the events layer (off → vanilla CUGA).
- `EVENTS_WORKER_BACKEND=cuga` — workers run on CUGA (this is the default; set `react` to force react).
- For Phase 2: `AP_BASE_URL` / `AP_EMAIL` / `AP_PASSWORD`, `GATEWAY_TOKEN`, `HOST_CALLBACK_URL`.
- Optional delivery: `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`.

> ⚠️ The `cuga-*` MCP servers (Code Engine) scale to zero — **warm them** before a demo by
> hitting one once, or the first tool call is slow.

---

# Part 1 — Test the BACKEND (understand what you see)

## 1.1 Offline core — no network, no deps → **14/14**
```bash
python3 tests/events/test_events_core.py
```
**You see:** `14/14 passed`.
**Why:** this exercises the pure logic with zero I/O — envelope parsing, the 7-server MCP
catalog, the flow builders (cron/poll/push/router), the subscription index, the classifier, the
reason→build planner, and a 12-utterance oracle. If this is green, the *shapes* are all correct
before any LLM or network is involved. **Nothing here touches CUGA or AP.**

## 1.2 Studio endpoints — the dumb UI's data source → **four 200s**
```bash
.venv-events/bin/python - <<'PY'
import importlib.util, os, sys
EV=os.path.join("src","cuga","backend","events")
s=importlib.util.spec_from_file_location("events",os.path.join(EV,"__init__.py"),submodule_search_locations=[EV])
p=importlib.util.module_from_spec(s); sys.modules["events"]=p; s.loader.exec_module(p)
from fastapi import FastAPI; from fastapi.testclient import TestClient
from events.app import register_events_routes
app=FastAPI(); register_events_routes(app, runtime=object(), store=None, concierge=None, engine=None)
c=TestClient(app)
for path in ["/api/events/status","/api/events/channels","/api/events/integrations","/api/events/examples"]:
    print(c.get(path).status_code, path, "→", list(c.get(path).json())[:3])
PY
```
**You see:** four `200`s; `status` includes `worker_backend: cuga`, `concierge_backend: react`.
**Why:** these are exactly what the Studio tabs render — the UI computes *nothing*, it just paints
these. `status` is also the **gate**: if it's not ok, the Studio hides itself. See diagram
[07](png/07_studio_ui-1.png).

## 1.3 The CUGA worker actually answers (the headline of your ask) — needs `.venv`
```bash
.venv/bin/python tests/events/live_cuga_worker_check.py
```
**You see (real output, verified 2026-07-02):**
```
provisioned worker 'helper' · backend = cuga
Q: Briefly, what is your role?
… building DynamicAgentGraph on demand + running (first build is slow) …
A: I'm Cuga Agent, a helpful assistant that runs code using the available tools to
   retrieve and process data, then provides concise answers.
executed via: CUGA DynamicAgentGraph
RESULT: PASS — CUGA DynamicAgentGraph executed + answered via the real AgentLoop
NOTE: worker had no MCP tools; attaching spec.mcp_servers to the CUGA graph is the next step.
```
**Why:** this is the piece CUGA lacked — running an **arbitrary worker agent** on demand. It
provisions a worker tagged `backend=cuga`, then `CugaRuntime.run` builds a **per-agent
`DynamicAgentGraph`** (faithful to CUGA's own startup at `main.py:814`) and drives it through
CUGA's real `AgentLoop` (the same engine `/stream` uses) to a final answer. The line
`executed via: CUGA DynamicAgentGraph` proves it did **not** use the react fallback. Diagram
[01](png/01_now_worker_invoke-1.png).
> **Tools note:** a CUGA worker is *tool-oriented*. With **no MCP tools** attached it answers
> from reasoning (as above). To give it tools, run the **registry** with the cuga-apps config and
> use the MCP test — see 1.3b. (No silent react fallback: if the CUGA stack is missing the run
> **raises**, unless you set `EVENTS_CUGA_FALLBACK_REACT=1`.)

## 1.3b The CUGA worker calls an MCP tool (the full chain) — needs the registry
```bash
# start the registry with the cuga-apps config (all 7 event-agent-ap servers)
MCP_SERVERS_FILE=src/cuga/backend/tools_env/registry/config/mcp_servers_cuga_apps.yaml \
  .venv/bin/python -m uvicorn \
  cuga.backend.tools_env.registry.registry.api_registry_server:app --port 8001 &
.venv/bin/python tests/events/live_cuga_worker_mcp_check.py
```
**You see (verified 2026-07-02):**
```
Loaded 2 tools for 'cuga_finance'
CUGA runs: btc_price_info = await cuga_finance_get_crypto_price(symbol="bitcoin", vs_currency="usd")
A: 61397
RESULT: PASS — CUGA worker used its MCP tool (numeric price)
```
**Why:** proves the whole chain — a `backend=cuga` worker with `mcp_servers=['cuga-finance']` →
`CombinedToolProvider(app_names=['cuga_finance'])` pulls the tool from the **registry** → CUGA
calls `get_crypto_price` → a real number. Details + the hyphen→underscore gotcha:
[MCP_SETUP.md](../MCP_SETUP.md).

## 1.4 The concierge reuses-or-creates a worker → NOW answer
```bash
.venv/bin/python tests/events/live_concierge_check.py     # or .venv-events (react workers)
```
**You see:** "bitcoin price" → **creates `pricebot`** → a live number; "ethereum" → **reuses
`pricebot`** (no duplicate) → PASS.
**Why:** the concierge (react) decides; the worker (cuga in `.venv`) answers. This is the whole
NOW story and the reuse logic. Diagram [02](png/02_concierge_reuse_or_create-1.png).

## 1.5 A watcher fires through Activepieces (Phase 2) → delivery
```bash
.venv-events/bin/python tests/events/live_phase2_watchers.py    # ~3 min; needs AP on :8081
```
**You see:** an **arXiv CRON watcher** armed on your AP, then (after AP's clock fires) a captured
arXiv result on a real HTTP sink → PASS; the flow is cleaned up.
**Why:** proves the Phase-2 loop end to end: concierge → `create_subscription` builds a real AP
schedule flow → AP fires → `POST /invoke` (with `scope` + `agent` in the body) → worker runs →
delivery. Diagrams [03-arm](png/03_cron_poll_watcher-1.png) + [03-fire](png/03_cron_poll_watcher-2.png).

## 1.6 (Optional) The full server, by hand
```bash
scripts/events_up.sh    # registry + tunnels + CUGA server on :8100 (EVENTS_ENABLED=1, seeded)
# in another shell (dev port 8100):
curl -s localhost:8100/api/events/status | jq          # worker_backend=cuga, concierge=react
curl -s localhost:8100/api/concierge -H 'content-type: application/json' \
  -d '{"text":"what is the bitcoin price right now?","thread_id":"web:1"}' | jq
curl -s localhost:8100/api/events/subscriptions | jq
```
**Watch the server log** — you'll see the mount line
`concierge=react, worker_backend=cuga, ap=True`, then per request a `trace_id` threaded across
`inbound → concierge → (provision) → worker.done`.

---

# Part 2 — Test from the UI (CUGA's own Studio)

The Studio is **added into CUGA's existing frontend** (not a new app) and is **dumb** — each tab
just renders a `GET /api/events/*` and the Concierge tab POSTs your text. So testing from the UI
*is* testing the backend; you just see it visually.

## 2.1 Build the frontend once + run the server
The frontend is a **pre-built** webpack bundle; rebuild + republish after any `.tsx` change, then
restart the server:
```bash
scripts/frontend_build.sh    # pnpm install + build + publish → src/cuga/frontend/dist
scripts/events_up.sh         # registry + tunnels + CUGA server on :8100
```
Open **http://localhost:8100/studio** (or **`/manage`** → the **Studio** nav item / **"Open Event
Studio →"** button, hidden in vanilla CUGA).

At the top you'll see: `scope default/default/local · workers cuga · concierge react · AP connected`
— that header is your confirmation that **CUGA is the one doing the work**.

## 2.2 Concierge tab — ask a question, watch a CUGA worker answer
- Type *"what is the bitcoin price right now?"* → **Send**.
- **You see:** a reply with a live price. **Behind it:** `POST /api/concierge` → concierge
  (react) creates/reuses `pricebot` → runs it as a **CUGA** worker → answer. (diagram
  [02](png/02_concierge_reuse_or_create-1.png))
- Flip the **Preview** toggle → Send → **you see** the plan JSON (mode/agent/source) and **no
  side effects** — that's `?dry_run=1`.

## 2.3 Examples tab — click-to-load
- Click **Try it** on "Geography + follow-up memory" → it drops the utterance into the Concierge
  tab. Send, then ask *"and its population?"* → **memory holds** (same `thread_id`).
- **Behind it:** `GET /api/events/examples` (the catalog) + `POST /api/concierge`.

## 2.4 Flows tab — arm a watcher, see it appear
- In the Concierge tab: *"every 1 minute send me new arXiv papers on mixture-of-experts"* → Send.
- Switch to **Flows** → **Refresh** → **you see** the armed subscription with a **CRON** badge,
  its agent, backend, and delivery target. **Behind it:** the concierge built a real AP flow;
  `GET /api/events/subscriptions` lists it. (diagram [03](png/03_cron_poll_watcher-1.png))

## 2.5 Channels & Integrations tabs — real status
- **Channels:** web = connected; telegram/discord = connected only if their bot token is set.
- **Integrations:** gmail/box/github/slack with **live AP-connection** status + a **Connect** button
  (CUGA-hosted `/api/events/connect/*`; token path live vs real AP). **Behind it:**
  `GET /api/events/integrations` calls `engine.list_connections` scoped to you. (diagram [07](png/07_studio_ui-1.png))

---

# Part 3 — Reading the signals (so you *know* what happened)

| Question | Where to look |
|---|---|
| **Did CUGA (not react) answer?** | `live_cuga_worker_check.py` prints `executed via: …`; the server logs a `cuga worker backend failed … falling back to react` warning **only** on fallback; the Studio header shows `workers cuga`. |
| **What did one request do, end to end?** | grep the **`trace_id`** across the server log: `inbound → concierge → flow.build → invoke → worker.done → deliver`. |
| **Whose data is this?** | every response carries `scope` (tenant/user); Flows/Integrations are filtered to your scope. |
| **Did the trigger really fire?** | Activepieces run history (the flow's runs) + the capture sink / delivery channel. |
| **Is the events layer even on?** | `GET /api/events/status` → `{enabled:true, worker_backend, ap_configured, features}`; off → 404 and the Studio hides. |

---

## Which diagram maps to which test
| Test | Diagram (PNG) |
|---|---|
| 1.3 CUGA worker answers · 1.6 `/invoke` | [01_now_worker_invoke](png/01_now_worker_invoke-1.png) |
| 1.4 concierge reuse-or-create · 2.2 | [02_concierge_reuse_or_create](png/02_concierge_reuse_or_create-1.png) |
| 1.5 watcher · 2.4 Flows | [03 arm](png/03_cron_poll_watcher-1.png) · [03 fire](png/03_cron_poll_watcher-2.png) |
| isolation (scope) | [04_isolation_scope](png/04_isolation_scope-1.png) |
| statefulness (fleet) | [05_statefulness_fleet](png/05_statefulness_fleet-1.png) |
| credentials | [06_credentials](png/06_credentials-1.png) |
| 1.2 endpoints · 2.x UI | [07_studio_ui](png/07_studio_ui-1.png) |

See also [HOW_TO_TEST.md](../HOW_TO_TEST.md) (terse one-by-one) and [STUDIO_UI.md](../STUDIO_UI.md).
