# Observability

CUGA has three independent observability integrations. All are **off by
default** and each is toggled by its own settings flag. This document is the
single place to set any of them up.

| Option | What it's for | Output | Install |
|---|---|---|---|
| **OpenLit** | Token/cost/latency **metrics**, Grafana dashboards | OTLP traces **and** metrics → collector | `pip install cuga[observability]` |
| **Langfuse** | Hosted **trace** UI, prompt inspection, eval workflows | LangChain callback → Langfuse | core dep (no extra) |
| **Traceloop** | Vendor-neutral **OpenTelemetry traces** — to a local file, any OTLP collector, or Langfuse's OTLP endpoint | OTLP traces → file or collector | `pip install cuga[observability-traceloop]` |

They can be run in any combination — see [Running more than one at
once](#running-more-than-one-at-once).

All three read the standard OpenTelemetry resource identity: `service.name`
(`OTEL_SERVICE_NAME`, defaults to `cuga`) and `OTEL_RESOURCE_ATTRIBUTES`
(CUGA adds `agent.id=CugaAgent`, `service.version`, and — from
`[service]` settings — `tenant.id` / `service.instance.id`).

---

## OpenLit

Auto-instruments LLM/agent calls (OpenAI, Groq, LiteLLM, LangChain,
LangGraph, MCP, …) and emits **both traces and metrics** over OTLP. This is
the option wired to the bundled Grafana stack.

**1. Install the extra:**

```bash
uv sync --extra observability     # or: pip install cuga[observability]
```

**2. Enable it in `settings.toml`:**

```toml
[observability]
openlit = true
```

**3. Point it at a collector** (`.env`, defaults to `http://localhost:4318`):

```env
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
# optional, default is http/protobuf:
# OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
# optional auth header:
# OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer <token>
```

**4. Local stack:** `deployment/docker-compose/openlit/` brings up OTel
Collector + Tempo + Prometheus + Grafana:

```bash
cd deployment/docker-compose/openlit && docker compose up -d
# Grafana → http://localhost:3000  (Explore → Tempo for traces,
#                                   Explore → Prometheus for metrics)
```

**Content capture:** OpenLit is initialised with
`capture_message_content=False` — prompt/completion text is **not** recorded.
This is deliberate and is not currently configurable from settings. (Traceloop
takes the opposite default — see [below](#content-capture-on-by-default).)

**Airgapped:** OpenLit fetches model pricing from GitHub by default; CUGA
always passes a local `pricing.json` instead (bundled, or
`observability.pricing_json` / `DYNACONF_OBSERVABILITY__PRICING_JSON`). LiteLLM's
remote cost map is controlled by `observability.litellm_local_model_cost_map`
(default `true`).

---

## Langfuse

Routes LangChain/LangGraph execution into a hosted (or self-hosted) Langfuse
project via its callback handler. No extra to install — the Langfuse client is
a core dependency.

**1. Enable it** (`.env`):

```env
DYNACONF_ADVANCED_FEATURES__LANGFUSE_TRACING=true
LANGFUSE_PUBLIC_KEY=pk-lf-xxx
LANGFUSE_SECRET_KEY=sk-lf-xxx
LANGFUSE_HOST=https://us.cloud.langfuse.com   # or your self-hosted URL
```

Equivalent `settings.toml` flag:

```toml
[advanced_features]
langfuse_tracing = true
```

Without credentials the Langfuse client disables itself cleanly (logs
`Langfuse client initialized without public_key. Client will be disabled.`)
— enabling the flag alone is harmless.

**Notes:**

- Nested sub-graph invocations are kept in one trace via a `ContextVar` shim
  (`backend/cuga_graph/utils/langfuse_tracing.py`) — see
  `docs/issues/langfuse-nested-callback-propagation.md`.
- `e2b_sandbox.py` also emits native Langfuse spans directly (in addition to
  the callback path).
- To send Traceloop's OTLP traces **into** Langfuse instead of using the
  callback handler, see the Traceloop `otlp` mode below — Langfuse has an OTLP
  ingestion endpoint.

---

## Traceloop

Vendor-neutral OpenTelemetry tracing. Auto-instruments the same LLM/agent
surface as OpenLit (LangChain, LangGraph, MCP, ~35 providers) plus CUGA's own
manual spans for tools, planner nodes, policy decisions and E2B sandbox
creation. Two exporter modes: a **zero-infrastructure local file**, or
**OTLP** to any collector (including Langfuse).

### Install

```bash
uv sync --extra observability-traceloop   # or: pip install cuga[observability-traceloop]
```

This is a **separate extra** from `observability` (OpenLit) — the two
dependency footprints are kept independent on purpose (spec DP12). Installing
one does not pull in the other. The extra also bundles the
`opentelemetry-instrumentation-{fastapi,httpx,aiohttp-client}` packages used
for A2A cross-process trace propagation.

### Enable

```toml
[observability]
traceloop = true
traceloop_exporter = "file"        # "file" | "otlp"
traceloop_file_path = ""           # "file" mode only; empty = default path
```

Env-var equivalents (respect the `CUGA_LOGGING_DIR`-style import-order rule —
set these before any `cuga` import):

```env
DYNACONF_OBSERVABILITY__TRACELOOP=true
DYNACONF_OBSERVABILITY__TRACELOOP_EXPORTER=file
```

On startup you should see `✅ Traceloop observability initialized
(exporter=file)` in the logs. If the extra isn't installed you get a warning
and tracing stays off — it never crashes the agent.

### Exporter mode: `file` (default)

Writes standard **OTLP-JSON**, one `ExportTraceServiceRequest` per line, to a
local file — no collector, no Docker, nothing else running. Flushes
synchronously, so the file is complete the moment a run finishes.

- Default path: `<TRACES_DIR>/traceloop_spans.jsonl`, i.e.
  `<CUGA_LOGGING_DIR>/traces/traceloop_spans.jsonl`
  (`src/cuga/logging/traces/traceloop_spans.jsonl` unless `CUGA_LOGGING_DIR`
  is set).
- Override with `traceloop_file_path` /
  `DYNACONF_OBSERVABILITY__TRACELOOP_FILE_PATH`.
- The file is **appended to** across runs. Delete it between runs if you want
  one trace per file.

This is the fastest way to see a real trace:

```bash
# settings.toml: traceloop = true, traceloop_exporter = "file"
uv run cuga start demo_crm --read-only
# ... run a task, then:
cat src/cuga/logging/traces/traceloop_spans.jsonl | python3 -m json.tool
```

### Exporter mode: `otlp`

Sends spans over OTLP/HTTP to a collector. Batched/async (unlike `file`), so
allow a few seconds after a run for spans to flush.

```env
DYNACONF_OBSERVABILITY__TRACELOOP=true
DYNACONF_OBSERVABILITY__TRACELOOP_EXPORTER=otlp
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
# Langfuse OTLP endpoint additionally needs Basic auth:
# OTEL_EXPORTER_OTLP_ENDPOINT=https://us.cloud.langfuse.com/api/public/otel
# OTEL_EXPORTER_OTLP_HEADERS=Authorization=Basic <base64(public_key:secret_key)>
```

`OTEL_EXPORTER_OTLP_ENDPOINT` is the **base URL — no `/v1/traces` suffix**.
The exporter appends the signal path itself. (Passing a full
`.../v1/traces` URL will double the suffix and silently 404.)

For a full hand-run walkthrough of `otlp` mode against the bundled OTel
Collector + Tempo + Grafana stack — and how to adapt it for Langfuse — see
**[`traceloop-otlp-collector-runbook.md`](./traceloop-otlp-collector-runbook.md)**.
This doc does not duplicate it.

### Content capture — on by default

Traceloop records prompt/completion text and tool arguments/outputs on spans
**by default**. This is a **deliberate divergence from OpenLit**
(`capture_message_content=False`), not an inconsistency: a trace without the
actual prompts, completions and tool I/O tells you *that* something ran, not
*what* it did — which is the whole point of tracing an agent (spec DP6).

To opt out (e.g. an untrusted or shared collector):

```env
TRACELOOP_TRACE_CONTENT=false
```

This also gates CUGA's manual `tool.arguments` / `tool.output` span
attributes. It is an environment variable only — there is no `settings.toml`
key for it.

(`TRACELOOP_SUPPRESS_WARNINGS=true` is set automatically so that
`@tool`-decorated call sites don't print a warning per call when tracing is
off. Override it if you're debugging Traceloop init itself.)

### What the trace looks like

A single invocation produces **one coherent trace** (one `trace_id`),
shaped:

```
invoke_agent {graph_name}              ← trace root. NO gen_ai.task.* here
  └─ {graph_name}.workflow             ← gen_ai.task.input / gen_ai.task.output
       └─ execute_task {node_name}     ← gen_ai.task.input / output, per node
            └─ ...nested LLM / tool / policy / sandbox spans...
```

**Important for anything consuming these traces programmatically:** the task
input/output attributes (`gen_ai.task.input` / `gen_ai.task.output`, and
Traceloop's native `traceloop.entity.input` / `traceloop.entity.output`) are
**not on the `invoke_agent {graph_name}` root span**. They live one level
down, on `{graph_name}.workflow` and on the per-node `execute_task {node}`
spans. A consumer that only reads the root span will not find task I/O there.
(Confirmed empirically against a real collector in Phase 11 — there is no
manual root span; the root comes from LangGraph's own auto-instrumentation.)

Tool spans carry both attribute conventions (spec DP9): `tool.name` /
`tool.arguments` / `tool.output` / `gen_ai.operation.name=tool`, plus
Traceloop's native `traceloop.entity.*`.

### Cross-process propagation

Trace context follows CUGA across process boundaries, so a delegated call
shows up in the same trace as its caller:

- **A2A** (CUGA delegating to / being called by another CUGA over A2A): W3C
  `traceparent` is injected on outbound httpx/aiohttp calls and extracted from
  inbound requests. Both `message/send` and `message/stream` are covered.
  Requires the peer to also run compatible OTel instrumentation (another
  Traceloop-enabled CUGA does).
- **MCP** (CUGA's own demo MCP servers — docs, knowledge, email, CRM):
  context rides in the JSON-RPC `_meta` field (works across stdio, SSE,
  streamable HTTP). Each MCP server process must also have Traceloop enabled —
  CUGA's shipped demo servers self-initialise it on import. Third-party MCP
  servers link only if they independently implement the SEP-414 `_meta`
  convention.

---

## Running more than one at once

The three flags are **independent and combinable**. Enabling Traceloop
alongside OpenLit and/or Langfuse is supported and does not require any
coordination:

- Traceloop's SDK **attaches** its span processor to whatever OpenTelemetry
  `TracerProvider` is already active (OpenLit's, Langfuse's, or its own) — it
  never fights for exclusive ownership. This was verified empirically across
  all 8 on/off combinations against a real collector (spec DP3 / Phase 11): no
  crash on startup in any combination, and every Traceloop-enabled row
  produced one coherent trace regardless of which library initialised the
  provider first.
- One residual, benign caveat: when OpenLit initialises the provider first,
  Traceloop's spans inherit OpenLit's resource attributes. All the meaningful
  ones (`service.name`, `service.version`, `agent.id`) are identical either
  way; only `telemetry.sdk.name` differs (`openlit` vs `opentelemetry`).
  Nothing downstream depends on it.

**Is it useful to run OpenLit + Traceloop together?** They auto-instrument
largely the same LLM surface, so it's mostly redundant. Pick OpenLit if you
want the metrics/Grafana dashboards; pick Traceloop if you want vendor-neutral
traces (local file, arbitrary collector, or Langfuse OTLP). Running both is
fine but produces parallel duplicate LLM spans.

---

## Environment variable reference

| Variable | Used by | Meaning |
|---|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OpenLit, Traceloop `otlp` | Collector **base** URL (no `/v1/traces`). Default `http://localhost:4318` |
| `OTEL_EXPORTER_OTLP_HEADERS` | OpenLit, Traceloop `otlp` | e.g. `Authorization=Basic <...>` for Langfuse OTLP |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | OpenLit | `http/protobuf` (default), `grpc`, `http/json` |
| `OTEL_SERVICE_NAME` | all | `service.name` resource attr. Default `cuga` |
| `OTEL_RESOURCE_ATTRIBUTES` | all | extra resource attrs; CUGA merges `agent.id`, `service.version`, `tenant.id`, `service.instance.id` |
| `DYNACONF_ADVANCED_FEATURES__LANGFUSE_TRACING` | Langfuse | enable flag |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` | Langfuse | project credentials |
| `DYNACONF_OBSERVABILITY__TRACELOOP` | Traceloop | enable flag |
| `DYNACONF_OBSERVABILITY__TRACELOOP_EXPORTER` | Traceloop | `file` (default) or `otlp` |
| `DYNACONF_OBSERVABILITY__TRACELOOP_FILE_PATH` | Traceloop `file` | override the `.jsonl` path |
| `TRACELOOP_TRACE_CONTENT` | Traceloop | `false` to disable prompt/completion/tool-I/O capture (default on) |
| `TRACELOOP_SUPPRESS_WARNINGS` | Traceloop | set to `true` automatically; unset to debug init |
| `DYNACONF_OBSERVABILITY__PRICING_JSON` | OpenLit | local `pricing.json` path (airgapped) |
| `DYNACONF_OBSERVABILITY__LITELLM_LOCAL_MODEL_COST_MAP` | OpenLit | use LiteLLM's bundled cost map (default `true`) |

Settings-file equivalents live under `[observability]` (`openlit`,
`traceloop`, `traceloop_exporter`, `traceloop_file_path`, …) and
`[advanced_features]` (`langfuse_tracing`) in `src/cuga/settings.toml`.

---

## Related docs

- [`traceloop-otlp-collector-runbook.md`](./traceloop-otlp-collector-runbook.md)
  — hand-run walkthrough: Traceloop `otlp` → OTel Collector → Tempo/Grafana
  (and adapting it for Langfuse).
- [`issues/langfuse-nested-callback-propagation.md`](./issues/langfuse-nested-callback-propagation.md)
  — why Langfuse needs the `ContextVar` shim.
- `deployment/docker-compose/openlit/` — the local collector + Tempo +
  Prometheus + Grafana stack (used by both OpenLit and Traceloop `otlp`).
- `src/cuga/backend/observability/{openlit_init,traceloop_init,local_otlp_file_exporter}.py`
  — module docstrings with implementation-level detail.
