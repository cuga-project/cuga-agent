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

Plus whatever LLM provider you already use — see the README's
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
> rest overwrite it with `"none"` unconditionally.

### Optional

| Variable | Default | Notes |
|---|---|---|
| `EVENTS_SERVICE_PORT` | `8100` | |
| `EVENTS_HOST` | `0.0.0.0` | Binds all interfaces. Set `127.0.0.1` to keep it off your network. |
| `EVENTS_SCHEDULER` | `native` | cron/poll run in-process. `ap` hands scheduling to Activepieces. |
| `EVENTS_LOG_LEVEL` | `INFO` | |
| `EVENTS_LOG_HTTPX` | unset | `1` logs request URLs — which for channel APIs contain the bot token. Leave off. |

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

## Adding a channel

Everything above is AP-free. To add Slack, Discord, Telegram, Gmail, GitHub, Box, RSS, YouTube,
Pinterest, Google Calendar, or a plain webhook, follow the per-connector guide in
[`events_docs/setup/`](setup/). Push triggers for the SaaS connectors need Activepieces and a
public HTTPS URL; the direct backends (Slack, Discord, Telegram, Box-token) do not.
