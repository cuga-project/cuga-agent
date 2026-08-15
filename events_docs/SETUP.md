# Event-driven agents — setup

The eventing layer is **its own service**. It does not mount onto CUGA's app: that "combined"
mode was removed along with the `EVENTS_ENABLED` flag that gated it, so there is no on/off
switch any more — running the service *is* enabling it (see
[`events/__init__.py`](../src/cuga/backend/events/__init__.py)).

```
                    ┌─────────────────────────────────┐
   browser ────────▶│  CUGA  :7860                    │
      │             │  the agent · the SPA · /run     │
      │             └─────────────────────────────────┘
      │                 │  /api/ui/config       ▲
      │                 │  (tells the SPA       │  POST /run
      │                 ▼   where :8100 is)     │  (do the actual work)
      │             ┌─────────────────────────────────┐
      └────────────▶│  events service  :8100          │
   /api/events/*    │  scheduler · concierge · flows  │
   (cross-origin —  └─────────────────────────────────┘
    needs CORS)
```

Two processes, one `.env`, one shared secret between them. Note the browser talks to **both** —
that second arrow is why `EVENTS_CORS_ORIGINS` is required.

This page covers the **web-only path**: the Events Studio in the browser, plus cron/poll flows.
No Slack, Discord, Telegram, or Activepieces. For those, add a connector afterwards from
[`events_docs/setup/`](setup/).

---

## 1. Environment

All of it goes in one `.env` at the repo root. Both processes read it — CUGA via
`cuga.config`, the events service via its own `_load_env()` at startup. Real environment
variables win over the file in both (`override=False`).

### Required

| Variable | Value | Why |
|---|---|---|
| `EVENTS_API_URL` | `http://127.0.0.1:8100` | Where the SPA sends `/api/events/*`. `/api/ui/config` hands it to the browser. Without it the SPA asks CUGA instead, gets the catch-all, and the Studio tab silently never appears. Also what the `/automate` forwarder needs — it is inert when unset. |
| `CUGA_URL` | `http://127.0.0.1:7860` | The way back: the events worker calls CUGA's `POST /run`. |
| `EVENTS_CORS_ORIGINS` | `http://localhost:7860,http://127.0.0.1:7860` | **Easy to miss.** CUGA serves the SPA, but the SPA calls *this* service for `/api/events/*` — cross-origin, since the port differs and `localhost` ≠ `127.0.0.1` to a browser. CORS middleware is added [only when this is set](../src/cuga/backend/events/service.py#L186), with `allow_credentials=True`, so `*` is rejected and origins must be listed. Unset → the browser blocks `/api/events/status` → **no Studio tab, no error anywhere**. |
| `GATEWAY_TOKEN` | a generated secret | Guards `/run` **and** the events service's own `/invoke`. One value on both sides. Generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. |

Those four must be set. `EVENTS_DB` is worth setting explicitly but has a working default:

| Variable | Default | Why |
|---|---|---|
| `EVENTS_DB` | `~/.cuga/events.db` | Durable store for armed flows. Set it explicitly so you know which file you are testing against — a stale one still holds whatever you armed last time. |

### The LLM — the two services choose differently

This trips people up. CUGA core picks its models from `AGENT_SETTING_CONFIG` and the matching
`settings.*.toml`. **The events service does not read `AGENT_SETTING_CONFIG` at all** — the
concierge builds its own client in [`events/llm.py`](../src/cuga/backend/events/llm.py#L151):

```
LLM_PROVIDER  → else auto-detect → else "ollama"
LLM_MODEL     → else the provider's default
```

Auto-detection checks keys in a fixed order — `RITS_API_KEY`, `ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`, `WATSONX_APIKEY`, `LITELLM_API_KEY` — and falls back to **ollama** if none are
present. Two consequences:

- With one provider configured it just works, and neither variable is needed.
- With **two** keys exported, the concierge silently picks the earlier one in that list while
  CUGA core uses whatever `AGENT_SETTING_CONFIG` says — two different models, no warning.

| Variable | When you need it |
|---|---|
| `LLM_PROVIDER` | more than one provider key is set, or you want the concierge pinned |
| `LLM_MODEL` | you want a specific model for the concierge (it is a tool-caller — give it a strong one) |

Otherwise, just configure your provider as usual — see the README's
[Configuration Priority](../README.md#configuration-priority).

### Required for the sub-agent roster

Skip both and CUGA runs as one plain agent — flows still fire, they just have no specialists.

| Variable | Value |
|---|---|
| `CUGA_SUPERVISOR_ROSTER` | `docs/examples/events/supervisor_agents.yaml` |
| `MCP_SERVERS_FILE` | `src/cuga/backend/tools_env/registry/config/mcp_servers_cuga_apps.yaml` |

**Set both or neither.** The roster names MCP apps (`cuga-finance`, `cuga-web`, …) and the
registry only serves them in FILE mode. With `MCP_SERVERS_FILE` unset, `cuga start` forces
`"none"` — managed-config-db mode, serving the demo app alone — and every roster agent answers
*"the available toolset does not include…"*.

> `cuga start demo` is the **only** preset that respects an exported `MCP_SERVERS_FILE`
> ([`cli/main.py`](../src/cuga/cli/main.py#L1348)). `demo_crm`, `manager`, `demo_skills` and the
> rest overwrite it with `"none"` unconditionally. In particular **`demo_supervisor` is not the
> one you want** — it is a different feature (`[supervisor]` in `settings.toml`) and forces
> `"none"`, which strips the roster's tools.

Both files above are examples. To run your own specialists — including against **local** MCP
servers with no cloud dependency — see [§6 Bring your own sub-agents](#6-bring-your-own-sub-agents).

### Optional

| Variable | Default | Notes |
|---|---|---|
| `EVENTS_SERVICE_PORT` | `8100` | |
| `EVENTS_HOST` | `0.0.0.0` | Binds all interfaces. Set `127.0.0.1` to keep it off your network. |
| `EVENTS_SCHEDULER` | `native` | cron/poll run in-process. `ap` hands scheduling to Activepieces. |
| `EVENTS_WEBHOOK_KEY` | unset | **Set this before exposing the server.** `POST /api/events/hook/<name>` runs an agent. Unset, `?key=` is not merely optional — it is *ignored*, so a wrong key is accepted too ([`app.py`](../src/cuga/backend/events/app.py#L121)). Fine on localhost; a hole on a public URL. |
| `EVENTS_DB_BACKUP` | unset | SQLite only, and **must be a different file** from `EVENTS_DB` — the live DB stays on local disk, the backup goes to mounted storage. Without it `/api/events/status` reports `durable: false`: armed flows are lost when the instance is replaced. Ignored for Postgres, which is durable already. |
| `CUGA_RUN_TOKEN` | falls back to `GATEWAY_TOKEN` | Only if you want `/run` to use a different secret from the events layer. |
| `EVENTS_REGISTRY_URL` | `http://localhost:8001` | Only if the tool registry is not on its default port. |
| `ENV_FILE` | unset | Load a specific env file instead of searching for `.env` — and unlike the default search it is applied with `override=True`. Handy for keeping `.env.events` separate from `.env` ([`config.py`](../src/cuga/config.py#L78)). |
| `EVENTS_LOG_LEVEL` | `INFO` | |
| `EVENTS_LOG_HTTPX` | unset | `1` logs request URLs — which for channel APIs contain the bot token. Leave off. |

Beyond these, the events package reads a further ~30 variables that are either channel/Activepieces
credentials (see [`setup/`](setup/)) or behaviour-tuning knobs you do not need to start — LLM
timeouts, fastpath toggles, run-log directories. Find them with:

```bash
grep -rhoE "os\.(environ\.get|getenv)\(\s*[\"'][A-Z_]+[\"']" src/cuga/backend/events/ | sort -u
```

### Not needed for this path

`EVENTS_ENABLED` (removed from the codebase), `EVENTS_SUPERVISOR` (vestigial — the roster is a
CUGA-side concern and [`capability.py`](../src/cuga/backend/events/capability.py#L47) reads
CUGA's `/run/agents` instead), `EVENTS_SEED_AGENTS` (already defaulted to `1`),
`EVENTS_PUBLIC_URL` / `EVENTS_NGROK_DOMAIN` (Slack's inbound webhook and OAuth callbacks only),
and every `AP_*`, `EVENTS_OAUTH_*`, and channel bot token.

### A complete minimal `.env`

```bash
# LLM — substitute your provider
AGENT_SETTING_CONFIG=settings.watsonx.toml
WATSONX_URL=https://us-south.ml.cloud.ibm.com
WATSONX_PROJECT_ID=...
WATSONX_APIKEY=...

# The two-way link
EVENTS_API_URL=http://127.0.0.1:8100
CUGA_URL=http://127.0.0.1:7860
EVENTS_CORS_ORIGINS=http://localhost:7860,http://127.0.0.1:7860
GATEWAY_TOKEN=<generated>
EVENTS_DB=/Users/you/.cuga/events.db

# Sub-agent roster (both, or neither)
CUGA_SUPERVISOR_ROSTER=docs/examples/events/supervisor_agents.yaml
MCP_SERVERS_FILE=src/cuga/backend/tools_env/registry/config/mcp_servers_cuga_apps.yaml
```

---

## 2. Optional: Postgres instead of SQLite

SQLite is the zero-infrastructure default and is fine for local work. Postgres matches what
gets deployed, so testing against it means something:

```bash
podman run -d --name cuga-events-pg \
  -e POSTGRES_USER=cuga -e POSTGRES_PASSWORD=cuga_dev_pw -e POSTGRES_DB=cuga_events \
  -p 5433:5432 docker.io/library/postgres:16-alpine

EVENTS_DB=postgresql://cuga:cuga_dev_pw@localhost:5433/cuga_events
```

`podman stop` / `podman start` keep the data. Only `podman rm` loses it — and the volume is
anonymous, so there is no named handle to recover from. `pg_dump` first if the armed flows matter.

For a **managed** Postgres that requires TLS, put the CA in `EVENTS_DB_CA_B64` (base64) rather
than a file path — it is written to a `0600` file on first connect
([`db.py`](../src/cuga/backend/events/db.py#L414)).

---

## 3. Start

Order matters: the events service probes CUGA's `/run/agents` at startup to report which
specialists are available.

```bash
# Terminal 1 — CUGA (:7860). Also starts the tool registry on :8001.
# Kills whatever already holds those two ports, so no manual cleanup.
uv run cuga start demo

# Terminal 2 — the events service (:8100), once CUGA answers
uv run python -m cuga.backend.events.service
```

To stop: Ctrl-C each. Nothing is left running except the Postgres container, if you chose one.

---

## 4. Verify

```bash
TOK=$(grep '^GATEWAY_TOKEN=' .env | cut -d= -f2)

curl -s localhost:8100/health
# {"ok":true,"service":"events","cuga":"http://127.0.0.1:7860"}

curl -s localhost:7860/api/ui/config
# events_api_url must be non-empty — this is what reveals the Studio tab

curl -s -H "X-Gateway-Token: $TOK" localhost:7860/run/agents
# lists pricebot, weatherbot, geobot, wiki_dive, incident_triage, pr_reviewer …

curl -s -i localhost:8100/api/events/status -H "Origin: http://localhost:7860" \
  | grep -i access-control-allow-origin
# MUST echo your origin back. Missing → the browser will block it and the tab stays hidden.
```

Then open **http://localhost:7860** — a nav entry reading **Events Studio ⚗** should appear,
routing to `/studio`.

> **Not `https://`.** The Quick Start in the main README says `https://localhost:7860`; this
> build serves plain HTTP there and `https://` gets connection-refused. Whichever origin you
> actually use has to appear in `EVENTS_CORS_ORIGINS`.

| Symptom | Cause |
|---|---|
| No Studio tab, but all three curls above pass | **`EVENTS_CORS_ORIGINS` is unset or does not match the origin in your address bar.** The curls succeed because curl ignores CORS; the browser does not. Check the console for a blocked cross-origin request. |
| No Studio tab | `EVENTS_API_URL` unset or the service is down. The SPA hides the tab whenever `/api/events/status` does not return JSON. |
| `/run/agents` returns 401 | `GATEWAY_TOKEN` differs between the two processes, or is missing from one. |
| `/run/agents` lists only `cuga` | `CUGA_SUPERVISOR_ROSTER` did not load. |
| Agents say *"the available toolset does not include…"* | `MCP_SERVERS_FILE` unset, so the registry is in managed-config-db mode. |
| First tool call hangs | The cuga-apps MCP servers scale to zero on Code Engine. Warm them before a demo. |

---

## 5. Use it

In the main chat box, a leading slash verb is forwarded to the concierge rather than handled by
the agent — `automate`, `watch`, `schedule`, `cron`, `poll`, `push`, `cancel`:

```
/automate every weekday at 9am, give me the BTC price and a one-line read on the move
```

The concierge compiles the trigger, asks for anything missing, and arms a standing flow. Handing
that same sentence to a plain agent makes it try to *implement* the schedule — a loop with
sleeps — which is the silent failure this path exists to prevent.

Armed flows, run history, and the inbox live in the Studio at `/studio`.

---

## 6. Bring your own sub-agents

The roster above is an example, not a fixture. To run your own specialists you edit **two files**
and change nothing else — same `cuga start demo`, same two processes.

### The two files

**a. Register the tools** — whatever `MCP_SERVERS_FILE` points at. Keys **must use underscores**:
CUGA's code-execution agent composes `<app>_<tool>` as a Python identifier, so `acme-billing_get_invoice`
parses as subtraction and throws `NameError`.

**b. Add the agent** — whatever `CUGA_SUPERVISOR_ROSTER` points at. Hyphens are fine *here*;
[`supervisor_config.py`](../src/cuga/supervisor_utils/supervisor_config.py#L213) normalizes them
with `n.replace("-", "_")` before hitting the registry.

```yaml
# a. mcp_servers_<yours>.yaml
mcpServers:
  acme_billing:
    url: https://internal.acme.com/billing/mcp
    transport: http
    description: invoice lookup / credit notes
```

```yaml
# b. supervisor_agents_<yours>.yaml
agents:
- name: billing_bot
  special_instructions: 'You answer invoice and credit-note questions for a customer account.
    Give the invoice number, amount, and status.
    '
  mcp_servers:
  - name: acme-billing
```

### Yes — the MCP servers can be fully local

Nothing requires the hosted Code Engine servers. The registry supports **stdio**, **http**, and
**sse**, and `transport` is auto-detected when `command` is present. A local stdio server needs no
network at all:

```yaml
mcpServers:
  # stdio — a local subprocess. No URL, no port, no network.
  my_tools:
    command: python
    args: ["./my_server.py"]
    transport: stdio          # optional; inferred from `command`
    env:
      API_KEY: your_api_key
    description: my local tools

  # stdio via npx — e.g. the reference filesystem server
  filesystem:
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "./cuga_workspace"]
    description: local file operations

  # http — a server you run yourself on localhost
  local_svc:
    url: http://127.0.0.1:9000/mcp
    transport: http
    description: my local HTTP MCP server
```

`env`, `cwd`, and an `include:` allow-list of tool names are all supported per server — see the
commented examples in [`mcp_servers.yaml`](../src/cuga/backend/tools_env/registry/config/mcp_servers.yaml),
which also ships a working stdio server (the knowledge service) you can copy. The same file's
`services:` key registers plain OpenAPI endpoints if MCP isn't what you have.

So a fully local stack is: local stdio/HTTP MCP servers + your roster + SQLite. No cloud anything.

### Two other ways to define an agent

```yaml
- name: legacy_svc
  a2a_protocol: {enabled: true, ...}    # external agent over A2A

- name: flight_agent                     # a CugaAgent defined in Python — tools,
  import_from: docs.examples.travel_agent.agents.flight_agent.flight_agent
```

`import_from` imports a fully-configured `CugaAgent` and is validated by class name at load, so
you can define agents in code and merely reference them from YAML.

### Point the env at your files

```bash
CUGA_SUPERVISOR_ROSTER=path/to/supervisor_agents_<yours>.yaml
MCP_SERVERS_FILE=path/to/mcp_servers_<yours>.yaml
```

Then restart — **CUGA first, then the events service**, exactly as in §3.

### Four things that will bite

| | |
|---|---|
| **`special_instructions` is load-bearing** | Not documentation. Per [`run_routes.py`](../src/cuga/backend/server/run_routes.py#L115) these fields are *"how the events layer's concierge decides which specialist a message belongs to, and a blank one makes a sub-agent effectively unroutable."* A description-less agent loads, appears in `/run/agents`, and is never picked. |
| **Omitting `mcp_servers` grants ALL tools** | *"An agent that names NOTHING gets all tools"* — silent over-provisioning, not an error. |
| **The roster is cached per path** | `_supervisor_cache[path]` in `run_routes`. Editing the YAML does nothing until CUGA restarts. |
| **The server must be in the registry too** | Naming one in the roster that is absent from `MCP_SERVERS_FILE` loads fine, then answers *"the available toolset does not include…"*. |

### Verify

```bash
TOK=$(grep '^GATEWAY_TOKEN=' .env | cut -d= -f2)
curl -s -H "X-Gateway-Token: $TOK" localhost:7860/run/agents   # your agent should be listed
curl -s localhost:8001/applications                             # your MCP app should be listed
```

Both must show it. In `/run/agents` only → the registry never loaded the server. In
`/applications` only → the roster did not load.

---

## Adding a channel

Everything above is AP-free. To add Slack, Discord, Telegram, Gmail, GitHub, Box, RSS, YouTube,
Pinterest, Google Calendar, or a plain webhook, follow the per-connector guide in
[`events_docs/setup/`](setup/). Push triggers for the SaaS connectors need Activepieces and a
public HTTPS URL; the direct backends (Slack, Discord, Telegram, Box-token) do not.
