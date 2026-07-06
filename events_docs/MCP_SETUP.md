# MCP setup — giving CUGA workers their tools

CUGA workers (the default `EVENTS_WORKER_BACKEND=cuga`) get tools from CUGA's **registry
service** — a separate process that reads a YAML of MCP servers and serves them to agents. This
is different from the `react` backend (which loads MCP tools in-process via `tools_bridge`). To
let a CUGA worker actually *call* a `cuga-*` tool, the registry must be running with the cuga-apps
config.

## The config (all 7 event-agent-ap servers)
[`src/cuga/backend/tools_env/registry/config/mcp_servers_cuga_apps.yaml`](../src/cuga/backend/tools_env/registry/config/mcp_servers_cuga_apps.yaml)
— `cuga-web · cuga-knowledge · cuga-geo · cuga-finance · cuga-code · cuga-local · cuga-text`
(IBM Code Engine, `transport: http`). App names match `mcp_catalog.known_names()`, so a worker
provisioned with `mcp_server_names=['cuga-finance']` loads exactly that server.

## How a CUGA worker gets tools (the chain)
```
builder/seed creates the agent (concierge NEVER creates agents — decision 0005)
   → AgentSpec(backend='cuga', mcp_servers=['cuga-finance'])           (AgentStore)
concierge.answer_now(agent) → CugaRuntime.run → _cuga_bridge.build_worker_graph
   → CombinedToolProvider(app_names=['cuga-finance'], agent_id=<scope::name>)
        → registry /applications  → serves cuga-finance (from the YAML)
        → registry loads its tools: get_crypto_price, get_stock_quote
   → DynamicAgentGraph → AgentLoop → CUGA calls the tool → real answer
```

## Run it
1. **Start the registry** with the cuga-apps config (default port 8001):
   ```bash
   MCP_SERVERS_FILE=src/cuga/backend/tools_env/registry/config/mcp_servers_cuga_apps.yaml \
     .venv/bin/python -m uvicorn \
     cuga.backend.tools_env.registry.registry.api_registry_server:app --host 127.0.0.1 --port 8001
   ```
   (or `MCP_SERVERS_FILE=… uv run registry`). **Warm** the Code Engine servers first (they scale
   to zero) by hitting one URL once.
2. **Start CUGA** (the full server auto-uses the registry at `settings.server_ports.registry`):
   ```bash
   EVENTS_ENABLED=1 EVENTS_WORKER_BACKEND=cuga .venv/bin/python -m cuga
   ```
3. Ask the concierge something tool-backed → the CUGA worker calls the `cuga-*` tool.

**Point at a non-default registry port** (e.g. when 8001 is taken):
`EVENTS_REGISTRY_URL=http://localhost:8021` for the test, or
`DYNACONF_SERVER_PORTS__REGISTRY_HOST=http://localhost:8021` for the app.

## Verified live (2026-07-02)
- The registry **loads the cuga-apps YAML and pulls real tools** from every server —
  `cuga-geo: [geocode, find_hikes, search_attractions, get_weather]`,
  `cuga-finance: [get_crypto_price, get_stock_quote]`, `cuga-code`, `cuga-local`, `cuga-text`.
  `GET /applications` returns all 7 by name.
- End-to-end: `tests/events/live_cuga_worker_mcp_check.py` — a `backend=cuga` `pricebot` with
  `mcp_servers=['cuga-finance']` builds a `DynamicAgentGraph`, loads the tool from the registry,
  and CUGA runs `await cuga_finance_get_crypto_price(symbol="bitcoin", vs_currency="usd")` →
  real result `{"price": 61397, …}` → **`A: 61397`** → PASS.

## The hyphen→underscore gotcha (found live, fixed)
CUGA's code-execution agent composes tool calls as `<app>_<tool>` **as Python source**. A
hyphenated app (`cuga-finance`) yields `cuga-finance_get_crypto_price`, which Python parses as
*subtraction* → `NameError: name 'cuga' is not defined`. So the registry app keys use
**underscores** (`cuga_finance`), and `_cuga_bridge.build_worker_graph` maps the events layer's
hyphenated names (`cuga-finance`, from `mcp_catalog`) to them (`app_names=[n.replace('-','_')]`).

## Note on the two backends
| | `react` worker | `cuga` worker (default) |
|---|---|---|
| Tools | in-process (`tools_bridge` + langchain-mcp-adapters) | CUGA **registry** (this doc) |
| Needs registry? | no | **yes** (for tools; the worker still runs without it, toolless) |
| Reasoning | thin ReAct loop | full CUGA supervisor / policies / knowledge |

See [phase_1_2/TESTING_WALKTHROUGH.md](phase_1_2/TESTING_WALKTHROUGH.md) and [TODO.md](TODO.md).
