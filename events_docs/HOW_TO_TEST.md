# How to test — Phase 1 & 2 (step by step)

Everything below was **run and verified** during the build (watsonx `gpt-oss-120b` + live
Activepieces + live `cuga-*` MCP). Two ways to run: the **focused venv** (`.venv-events`, used
during the build — fast, no full CUGA sync) or the **full CUGA server** (`uv sync`, then the
`EVENTS_ENABLED` flag mounts everything into `main.py`).

## Prerequisites
- `.env` at repo root (watsonx + `GATEWAY_TOKEN` + AP creds) — already added, gitignored.
- Activepieces running on `AP_BASE_URL` (`:8081`) for Phase 2.
- The focused venv (already created): `.venv-events`. To recreate:
  ```bash
  uv venv .venv-events --python 3.12
  .venv-events/bin/python -m pip -q install langgraph "langchain>=1.0" langchain-ibm \
     langchain-mcp-adapters "mcp[cli]" httpx python-dotenv pytest fastapi uvicorn
  ```

## Step 1 — Offline core (no deps, no network) — **14/14**
```bash
python3 tests/events/test_events_core.py
```
Verifies: envelope, MCP catalog, flow builders (cron + push/router), subscription index,
classifier + cadence parsing, the reason→build planner, StubRuntime memory, and a 12-utterance
eval oracle. Pure stdlib — runs anywhere.

## Step 2 — Live worker: NOW + per-thread memory (watsonx + cuga-geo MCP)
```bash
.venv-events/bin/python tests/events/live_react_check.py
```
Expect: `Tokyo` → population (memory carries "its") → `Lima`, then **PASS**. Proves
`AgentRuntime.run` (react backend) + real MCP tools + memory.

## Step 3 — Live concierge: reuse-or-create (NL → agent → answer)
```bash
.venv-events/bin/python tests/events/live_concierge_check.py
```
Expect: "bitcoin price" → **creates pricebot** (cuga-finance) → real price; "ethereum" →
**reuses pricebot** (no duplicate). **PASS (created + reused + numeric)**.

## Step 4 — Live Phase 2: a watcher fires via Activepieces (~3 min)
```bash
.venv-events/bin/python tests/events/live_phase2_watchers.py
```
Stands up a real `/invoke` receiver on `:8009`, arms an **arXiv CRON watcher** (every 1 min) on
your AP, waits for AP to fire → `/invoke` → worker → **delivery to a capture sink**, then cleans
up the flow. Expect a captured arXiv result + **PASS**.
(Uses `:8009` because `:8000` is often taken; override with `EVENTS_TEST_PORT`.)

## Step 4b — Isolation, statefulness & credentials (live)
```bash
.venv-events/bin/python tests/events/live_isolation_check.py       # two tenants → scope-isolated AP flows
.venv-events/bin/python tests/events/live_statefulness_check.py    # agent+memory survive a replica restart, still isolated
.venv-events/bin/python tests/events/live_credentials_check.py     # shared cred reused by all; per-user distinct; unconnected → connect prompt
```

## Clearing Activepieces (clean slate)
```bash
.venv-events/bin/python tests/events/ap_nuke.py --dry    # preview EA flows/connections/projects
.venv-events/bin/python tests/events/ap_nuke.py          # delete EA-tagged artifacts
.venv-events/bin/python tests/events/ap_nuke.py --all    # delete EVERY flow + connection (nuclear)
```

## Step 5 — Inside the full CUGA server (the real mount)
Needs `uv sync` (full CUGA deps). The one-command launcher `scripts/events_up.sh` starts the
registry + tunnels + the CUGA server on **:8100** (with `EVENTS_ENABLED=1`, seeded agents,
`EVENTS_DB=.events.db`). Or run it by hand and curl the endpoints (dev port **8100**):
```bash
scripts/events_up.sh                            # mounts /invoke, /api/concierge, /api/events/*
# dry-run the planner (no side effects):
curl -s "localhost:8100/api/concierge?dry_run=1" -H 'content-type: application/json' \
  -d '{"text":"every 2 minutes send me the time"}'
# talk to the concierge (live):
curl -s localhost:8100/api/concierge -H 'content-type: application/json' \
  -d '{"text":"what is the bitcoin price right now?","thread_id":"web:1"}'
# the Studio read endpoints (what the UI renders):
curl -s localhost:8100/api/events/status
curl -s localhost:8100/api/events/channels
curl -s localhost:8100/api/events/integrations
curl -s localhost:8100/api/events/examples
curl -s localhost:8100/api/events/agents
curl -s localhost:8100/api/events/subscriptions
```
With the flag **off**, CUGA is byte-for-byte unchanged (nothing mounts; the Studio nav hides).

---

# Testing the backend ALONE (no UI)
Everything above (Steps 1–5) is backend-only. The fastest proof the **Studio endpoints** serve,
without a browser or a build, is the FastAPI TestClient smoke (what I ran during the build):
```bash
.venv-events/bin/python - <<'PY'
import importlib.util, os, sys, tempfile
EV=os.path.join("src","cuga","backend","events")
s=importlib.util.spec_from_file_location("events",os.path.join(EV,"__init__.py"),submodule_search_locations=[EV])
p=importlib.util.module_from_spec(s); sys.modules["events"]=p; s.loader.exec_module(p)
from fastapi import FastAPI; from fastapi.testclient import TestClient
from events.app import register_events_routes
app=FastAPI(); register_events_routes(app, runtime=object(), store=None, concierge=None, engine=None)
c=TestClient(app)
for path in ["/api/events/status","/api/events/channels","/api/events/integrations","/api/events/examples"]:
    print(c.get(path).status_code, path)
PY
```
Expect four `200`s. (Against the **full** server on :8100 with `EVENTS_ENABLED=1`, just `curl` the
same paths as in Step 5 — those return live channel/integration/subscription state.)

---

# Testing Phase 1 & 2 FROM THE UI (CUGA's own UI — not a new one)
The Studio is added **into CUGA's existing frontend** (new tabs, not a new app). Build once, then
click through. Full reference: [STUDIO_UI.md](STUDIO_UI.md).

### Build the frontend (pre-built bundle — rebuild after any `.tsx` change)
The frontend FastAPI serves is a **pre-built** webpack bundle at `src/cuga/frontend/dist`; editing
`*.tsx` does nothing until you rebuild + republish and restart the server. Use the script:
```bash
scripts/frontend_build.sh    # pnpm install + build + publish → src/cuga/frontend/dist
```

### Run the server with the flag on
```bash
scripts/events_up.sh         # registry + tunnels + CUGA server on :8100 (EVENTS_ENABLED=1)
```
Open **http://localhost:8100/studio** (or the app → **/manage** → **Studio**). Because the events
layer is mounted, a **Studio** nav item + an **"Open Event Studio →"** button appear.

### Phase 1 from the UI (NOW + memory)
1. **Concierge** tab → type *"what is the bitcoin price right now?"* → **Send**. A `pricebot`
   worker is created/reused and a live price comes back. (Same reuse-or-create as Step 3.)
2. Follow up *"and ethereum?"* → it **reuses** pricebot (no duplicate).
3. **Examples** tab → click **Try it** on "Geography + follow-up memory" → it drops the utterance
   into the Concierge tab → Send → ask *"and its population?"* → per-thread memory holds.
4. Flip the **Preview** toggle → Send → you get the plan JSON (`dry_run`) with **no side effects**.

### Phase 2 from the UI (CRON/POLL watchers)
5. **Concierge** tab → *"every 1 minute send me new arXiv papers on mixture-of-experts"* →
   Send. The concierge arms an **AP schedule flow**.
6. **Flows** tab → **Refresh** → the armed subscription appears with a **CRON** badge, its agent,
   backend, and delivery target. (This is the Step-4 watcher, now visible in the UI.)
7. **Integrations** tab → shows gmail/box/github/slack with **real** AP-connection status and a
   **Connect** button (OAuth apps open the login; token apps prompt for a token). **Channels** tab
   → web = connected; telegram/discord/slack = connected only if their bot token is set.

Nothing in the UI decides any of this — each tab just renders a `GET /api/events/*` response and
the Concierge tab POSTs your text. Phase 1 & 2 keep working exactly as in Steps 1–4b; the UI is a
window onto them.

---

## Features enabled (Phase 1 & 2)
| Feature | Endpoint / entry | Status |
|---|---|---|
| **Opt-in flag** `EVENTS_ENABLED` (off → CUGA unchanged) | `main.py` mount | ✅ |
| **AgentRuntime port** + **react backend** (langgraph + watsonx), per-thread memory | `runtime.ReactRuntime` | ✅ live |
| **MCP auto-registration** (7 `cuga-*` servers by name) | `mcp_catalog` | ✅ live |
| **Concierge**: NL → **reuse-or-create** worker → **run_now** (NOW) | `Concierge`, `/api/concierge` | ✅ live |
| **`/invoke` seam** (AP callback) + `X-Gateway-Token` + `trace_id` | `POST /invoke` | ✅ live |
| **Dry-run** reason→build planner (no side effects) | `/api/concierge?dry_run=1` | ✅ |
| **AP engine client** — create **CRON/POLL** flows on live AP | `ap_engine.APEngine` | ✅ live |
| **`create_subscription`** (concierge tool) cron/poll | concierge meta-tool | ✅ |
| **Subscription index** (sqlite) | `GET /api/events/subscriptions` | ✅ |
| **Delivery** to capture sink (`EA_CAPTURE_URL`) | `/invoke` deliver | ✅ live |
| **Flow builders** (cron/poll/push/inbound/router) | `flows` | ✅ tested |
| **End-to-end tracing** (one `trace_id` across seams) | `trace` | ✅ |
| **Studio read endpoints** (status/channels/integrations/examples) | `GET /api/events/*` | ✅ (TestClient 200s) |
| **Studio UI** (Concierge/Channels/Integrations/Flows/Examples tabs, dumb) | CUGA React frontend | ✅ built ([STUDIO_UI.md](STUDIO_UI.md)) |

## Worker backend (who answers the question)
- **Concierge** (NL→flow) = **react** (lightweight tool-caller).
- **Workers** (do the hard work of answering) = **cuga** by default (`EVENTS_WORKER_BACKEND=cuga`)
  → a per-agent `DynamicAgentGraph` with CUGA's policies/knowledge/supervisor/tools. Storage +
  isolation use the shared `AgentStore`.
- **No silent react fallback.** If the CUGA stack is absent or a build/run fails, execution
  **raises** (loud) — CUGA does the reasoning, period. React fallback exists **only** with an
  explicit `EVENTS_CUGA_FALLBACK_REACT=1`. `worker_backend` is shown in `/api/events/status`.

### cuga worker — LIVE-VERIFIED (full `.venv` from `uv sync`)
```bash
# 1) execution (no tools): builds a DynamicAgentGraph, runs via CUGA's real AgentLoop
.venv/bin/python tests/events/live_cuga_worker_check.py        # PASS — CUGA executed + answered

# 2) with MCP tools: needs the registry running with the cuga-apps config (see MCP_SETUP.md)
MCP_SERVERS_FILE=src/cuga/backend/tools_env/registry/config/mcp_servers_cuga_apps.yaml \
  .venv/bin/python -m uvicorn \
  cuga.backend.tools_env.registry.registry.api_registry_server:app --port 8001 &   # registry
.venv/bin/python tests/events/live_cuga_worker_mcp_check.py    # PASS — worker called cuga_finance → real BTC price
```
The MCP test proves the full chain: `backend=cuga` worker → registry serves `cuga_finance` →
CUGA calls `get_crypto_price` → real number (`A: 61397`). All 7 event-agent-ap servers are in
[`mcp_servers_cuga_apps.yaml`](../src/cuga/backend/tools_env/registry/config/mcp_servers_cuga_apps.yaml).

### Offline coverage across dimensions
```bash
python3 tests/events/test_events_core.py            # 14/14 — envelope/flows/subs/classify/planner
python3 tests/events/test_events_dimensions.py      # 10/10 — connectors/catalog/creds/isolation/runtime
.venv-events/bin/python tests/events/test_events_studio_api.py   # 5/5 — Studio endpoints
```
Full matrix: [TEST_COVERAGE.md](TEST_COVERAGE.md).

## Now wired (post Phase 1 & 2) — Telegram live-verified only
- **Channel inbound + real channel delivery** — channel·message → `/invoke` → concierge router →
  channel·send (`ap_engine.create_inbound_flow`); **scheduled/poll flows now deliver** back to the
  caller's native channel id (`create_schedule_flow` appends a channel send step). **Only the
  Telegram round-trip is fully live-verified**; Discord/Slack are wired from AP piece metadata but
  not yet live round-trip-tested. Slack's `send_channel_message` requires a constant `sendAsBot=true`.
- **PUSH triggers** — `ap_engine.create_push_flow` + the Box resume watcher (`build_resume_watcher_flow`);
  concierge `find_or_create_flow(kind="push", ...)`. Live PUSH round-trip not yet proven end-to-end.
- **OAuth connect-in-place** — the Integrations tab now posts to `/api/events/connect/*` (token path
  live against real AP; OAuth path built, degrades cleanly).

## Not yet
- Live Discord/Slack channel round-trips; live PUSH (Box/GitHub/Gmail) e2e.
- Real Gmail/Box OAuth once the platform's OAuth app is registered.
