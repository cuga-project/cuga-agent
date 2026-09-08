# Traceloop Instrumentation — Decision Spec

Companion to [`traceloop-instrumentation-plan.md`](./traceloop-instrumentation-plan.md)
(the implementation plan, with the goal statement at its top). This file is
the decision record — each item below (DP1–DP14) captures a question,
options considered, trade-offs, what was decided, and why. Consult a specific
DP here when the plan references it; the plan itself carries enough
operational detail to execute without reading this end to end. Where a call
is genuinely still open, it's flagged as such rather than given a false
"decided" label.

---

## DP1 — Instrumentation library: Traceloop SDK vs. raw OpenTelemetry vs. bare instrumentation packages

**Question:** What library actually emits the spans?

| Option | Pros | Cons |
|---|---|---|
| **A. `traceloop-sdk`** (chosen in the prompt) | Ships `@workflow`/`@task`/`@agent`/`@tool` decorators, `set_association_properties()`, a curated `Instruments` bundle, and `Traceloop.init()` handles provider/exporter/resource-attribute wiring in one call. Indigo's own reference agent (gaia_agent) and Indigo's experiment engine both use it — following the same library means CUGA inherits any future alignment work Indigo does against Traceloop's conventions for free. Indigo's own documented instrumentation guidance (`indigo-analytic-init` skill) is written assuming `traceloop.sdk.decorators` imports. | Adds a real dependency with its own version churn and a documented compatibility pin (`wrapt<2.0`) for the LangChain instrumentor. Another package whose upgrades can silently change span shape (Traceloop has already deprecated `@atask`/`@aworkflow`/etc. once). |
| **B. Raw `opentelemetry-sdk` + manual `gen_ai.*` attribute-setting everywhere** | Zero extra dependency beyond what's already pinned via OpenLit's override block. Full control over exactly what's emitted — no risk of a Traceloop upgrade changing attribute shape underneath you. | Indigo's `span_to_task` detection is attribute/name-pattern-based, not library-based, so this *would* still work for Indigo consumption — but you'd be hand-rebuilding association-properties, entity input/output JSON-encoding, and instrument bundling that Traceloop already provides, and diverging from the one library Indigo's own tooling assumes you're using. |
| **C. Individual `opentelemetry-instrumentation-{langchain,litellm,...}` packages directly, no `traceloop-sdk` wrapper** | Middle ground — get auto-instrumentation without the `Traceloop.init()` convenience layer or its decorators. Avoids the `wrapt<2.0` pin question if you don't take the LangChain instrumentor. | Loses `@task`/`@tool` decorators and `set_association_properties()` — you'd reimplement those or do without, which undercuts the "manual instrumentation where OTEL can't reach" half of the goal. |

**Current assumption:** Option A. **Rationale:** the deciding factor is that Indigo's
own reference implementation and tooling are built around `traceloop-sdk`
specifically, not just "OTEL in general" — matching it minimizes future
divergence from whatever Indigo's plugins come to expect, at the cost of one
extra pinned dependency.

**Discuss:** is avoiding a dependency addition (Option B) worth more than staying
aligned with Indigo's reference implementation?

---

## DP2 — Relationship to the two existing options: additive vs. consolidating

**Question:** Should Traceloop sit alongside OpenLit + Langfuse, or replace one of them?

| Option | Pros | Cons |
|---|---|---|
| **A. Additive third option, all three independently gated** (chosen) | No breaking change for existing OpenLit/Langfuse users. Smallest, safest initial change. | Three instrumentation systems to maintain long-term; the TracerProvider-ownership question (DP3) becomes a three-way problem instead of two-way. Some genuine overlap (OpenLit and Traceloop both do OTEL auto-instrumentation of the same LLM providers) — running both together is mostly redundant. |
| **B. Replace OpenLit with Traceloop** | OpenLit and Traceloop cover the same ground (OTEL auto-instrumentation of OpenAI/LiteLLM/LangChain/etc.) — replacing removes duplication rather than adding a third parallel system. | Bigger blast radius: existing OpenLit users (and the docker-compose Tempo/Grafana stack tuned for it) need to be re-validated against Traceloop's span shape. Loses whatever OpenLit-specific behavior isn't equivalent (e.g. its exact FastAPI/httpx uninstrument sequencing). |
| **C. Replace Langfuse CallbackHandler tracing with Traceloop→OTLP→Langfuse** | Removes the LangChain-callback-based nested-trace bug class entirely (OTEL context propagation is automatic where the callback-list approach needed a custom `ContextVar` shim, per `docs/issues/langfuse-nested-callback-propagation.md`). One tracing mechanism instead of two. | Breaking change for any code relying on Langfuse-SDK-specific features used elsewhere in the repo (the native-SDK usage in `e2b_sandbox.py`, the REST-API-based `evaluation/get_langfuse_data.py` metric parser). Larger, riskier PR. |

**Current assumption:** Option A. **Rationale:** ships value fastest and
lets the TracerProvider-conflict behavior (DP3) be observed in practice before
committing to a consolidation. Options B/C are legitimate follow-ups once
Traceloop is proven out, not something to do in the same change.

**Discuss:** is the eventual intent to consolidate (B and/or C), with this PR as
step one — in which case the "additive, all three stay" framing in the prompt
should say so explicitly?

---

## DP3 — TracerProvider ownership arbitration — REVISED

**Original framing was wrong.** The first draft of this spec treated "who wins the
provider race" as a trade-off between silently no-op'ing (A) or no-op'ing with a
log line (B) — i.e., it accepted that enabling Traceloop might legitimately
produce zero Traceloop output if OpenLit or Langfuse got there first, and only
argued about how loudly to admit that. That's the wrong bar: **if the user turns
Traceloop on, they need Traceloop's spans flowing to Traceloop's configured
destination — not a log message explaining why they aren't.**

Checked against the actual `traceloop-sdk` source
(`traceloop/sdk/tracing/tracing.py`, `TracerWrapper.__new__` →
`init_tracer_provider()`) rather than assuming behavior from gaia_agent's wrapper
around it:

```python
def init_tracer_provider(resource, sampler=None) -> TracerProvider:
    default_provider = get_tracer_provider()
    if isinstance(default_provider, ProxyTracerProvider):
        provider = TracerProvider(resource=resource, sampler=sampler)  # nothing installed yet
        trace.set_tracer_provider(provider)
    elif not hasattr(default_provider, "add_span_processor"):
        logging.error("Cannot add span processor to the default provider...")
        return
    else:
        provider = default_provider   # a real provider already exists — REUSE it, don't replace it
    return provider
```

Traceloop's own SDK **already does the right thing**: if no provider exists yet it
installs one; if a real provider already exists (OpenLit's or Langfuse's), it
reuses that exact object and — a few lines later in `TracerWrapper.__new__` —
calls `provider.add_span_processor(...)` to attach Traceloop's own exporter as an
*additional* subscriber. OTEL's `TracerProvider` broadcasts every span to every
registered `SpanProcessor` — multiple simultaneous exporters is a supported,
first-class OTEL pattern, not a workaround. So **Traceloop's SDK never actually
needs to "win" the provider race to produce output** — attaching is enough, and
it does this automatically.

The actual bug is in the precedent I copied: gaia_agent's own wrapper module
(`telemetry_init.py`) adds an early-return guard —
`if isinstance(get_tracer_provider(), SdkTracerProvider): return` — that skips
calling `Traceloop.init()` **entirely** if a provider already exists. That guard
defeats the SDK's own attach-behavior and is exactly what produces the silent
no-op. It's a gaia_agent-specific choice (plausibly there to let an external
harness like `indigo experiment` take precedence in that one specific use case),
not a general pattern worth mirroring here.

| Option | Pros | Cons |
|---|---|---|
| **A. Copy gaia_agent's early-return guard** (what the original DP3 assumed) | None found once the SDK's actual behavior is checked — this only "protects" against a race that the SDK already handles safely on its own. | Produces exactly the silent-no-op failure mode the user correctly rejected. Actively worse than doing nothing extra. |
| **B. Always call `Traceloop.init()` unconditionally when the flag is on; do not add an existing-provider guard** — **adopted** | Guarantees Traceloop's exporter receives spans regardless of init order relative to OpenLit/Langfuse, because the SDK's own `init_tracer_provider()` already attaches to whatever provider is active. No extra coordination code needed — this is less code than option A, not more. `Traceloop.init()` is itself idempotent (`TracerWrapper.__new__` singleton-guards via `hasattr(cls, "instance")`), so calling it doesn't risk double-initialization either. | One residual caveat, not a failure mode — see below. |

**Decision:** Option B. Do not port gaia_agent's guard. Always call
`Traceloop.init()` when `[observability] traceloop = true`, letting the SDK's
native "attach to whatever provider exists" behavior do the work.

**Residual caveat worth documenting (not blocking):** `Resource` (the object
carrying `service.name`/`tenant.id`/etc.) is fixed at `TracerProvider`
construction time. In the "reuse existing provider" branch, `init_tracer_provider`
returns the *existing* provider object as-is — it does not rebuild it with
Traceloop's own `resource_attributes`. So if OpenLit (or Langfuse) initializes
first, spans exported through Traceloop's processor will carry whichever
resource attributes the first initializer set, not necessarily the exact dict
passed to `Traceloop.init(resource_attributes=...)`. This does not block spans
from flowing (the actual requirement), it only affects resource-level attribute
values. Since OpenLit already sets a comparable resource-attribute set
(`service.name=cuga`, `agent.id`, `tenant.id`, `service.instance.id`), this is
likely a non-issue in practice — confirm via the unit test below rather than
assuming either way.

One genuinely rare edge case from the source: if `get_tracer_provider()` returns
something that is neither a `ProxyTracerProvider` nor an object with
`add_span_processor` (not true for the standard OTEL SDK provider that both
OpenLit and Langfuse use, but theoretically possible with an unusual third-party
provider), `init_tracer_provider()` returns `None` and the next line
(`obj.__tracer_provider.add_span_processor(...)`) would raise `AttributeError`.
Not a practical risk here, but worth a defensive check in `init_traceloop()` if
being conservative.

---

## DP4 — Auto-instrumentation scope (`Instruments` allow-list) — RESOLVED

**Question:** Which of Traceloop's ~35 available instrumentors should be enabled?

**Decision: Option B — enable everything Traceloop supports; don't hand-maintain
an allow-list.** Pass `instruments=None` (or omit the argument — `Traceloop.init()`
defaults to `set(Instruments)`, i.e. all of them) rather than an explicit set.
Only `block_instruments` is used, for the DP5 security exclusions
(`REQUESTS`/`URLLIB3`) — everything else Traceloop knows how to patch gets
patched automatically, with zero maintenance as CUGA adds new LLM/tool providers.
Since each instrumentor only activates by patching a library that must actually
be *imported* to matter (e.g. `Instruments.PINECONE` is inert unless
`pinecone` is installed and imported), enabling the full set costs nothing at
runtime for providers CUGA doesn't use — this was the deciding factor over the
original allow-list framing, which optimized for a "risk" (unused instrumentors
misbehaving) that doesn't really exist in practice.

This includes `Instruments.MCP` — which raises the cross-process trace-context
propagation question below, now the more substantive open item for this
decision point.

---

### MCP trace-context propagation — does it happen automatically?

**Short answer: yes, but only when both sides are instrumented and speak the
official `mcp`/FastMCP Python SDK — verified from the actual
`opentelemetry-instrumentation-mcp` source, not assumed.**

Traceloop's MCP instrumentor (`opentelemetry-instrumentation-mcp`, pulled in by
`Instruments.MCP`) does real W3C trace-context propagation, at two layers:

1. **Transport/stream layer** (the layer that actually matters — this is
   unconditional, not best-effort): it wraps the raw read/write streams
   returned by `mcp.client.stdio.stdio_client`, `mcp.client.sse.sse_client`,
   `mcp.client.streamable_http.streamablehttp_client` on the client side, and
   `mcp.server.stdio.stdio_server`, `mcp.server.sse.SseServerTransport.connect_sse`,
   `mcp.server.streamable_http.StreamableHTTPServerTransport.connect` on the
   server side. On send, `InstrumentedStreamWriter.send()` does:
   ```python
   meta = request.params.setdefault("_meta", {})
   propagate.get_global_textmap().inject(meta)
   ```
   — this **creates** the `_meta` field on the outgoing JSON-RPC request if it
   doesn't already exist and injects the current OTEL context into it. On
   receive, `InstrumentedStreamReader.__aiter__()` does the inverse:
   ```python
   meta = request.params.get("_meta")
   if meta:
       ctx = propagate.extract(meta)
       context.attach(ctx)
   ```
   Because this rides inside the MCP JSON-RPC message body (`_meta`), not HTTP
   headers, it works identically across **all three transports** — stdio, SSE,
   and streamable HTTP — which matters since stdio (a local subprocess pipe,
   CUGA's likely default for local MCP servers) has no header channel to use.
2. **Session layer** (`BaseSession.send_request` patch): additionally sets a
   `traceparent` on `params.meta` if a `meta` object is *already present*, and
   separately creates local `tools/call` spans regardless of whether propagation
   happens.
3. **Server-side session plumbing** (`ServerSession.__init__` patch): swaps in
   `ContextAttachingStreamReader`/`ContextSavingStreamWriter` around the
   server's internal message queue, so context extracted at the transport layer
   survives the hop across the server's own internal reader/writer queue
   (otherwise it would be lost crossing that internal async boundary even with
   #1 correctly extracting it).

**Correction: this is not actually a Python-specific requirement — checked
against the MCP spec itself, not just this one Python package.** The `_meta`
field carrying `traceparent`/`tracestate`/`baggage` is no longer just "whatever
this one OpenLLMetry package happens to do" — it's now a documented MCP
specification enhancement (SEP-414) that locks down those key names precisely
*so that* distributed traces correlate across different SDKs/languages and
gateways. The real requirement is narrower and language-agnostic: **the other
side must (a) run its own OTEL SDK (any language — Node, Go, Java, etc. all have
a W3C `TraceContextTextMapPropagator` equivalent) and (b) implement the SEP-414
`_meta` convention for extracting incoming context.** A non-Python MCP server
that does both would link into the same trace just as well as a Python one.
"Python/FastMCP" only came up because that's what CUGA's own client and demo
servers happen to be built on (confirmed below) — not because the mechanism
itself is Python-specific. If the server side doesn't implement this convention
(regardless of language):
- CUGA's own client-side spans (`tools/call` etc.) are unaffected — those are
  local and always work regardless of what's on the other end.
- The `traceparent` still gets written into the outgoing `_meta` field
  (harmless, ignored by an uninstrumented server).
- But nothing comes back linked — the server-side work (if traced at all)
  starts as a disconnected trace, so you get two separate trees instead of one
  spanning both processes.

**What this means concretely for cuga-agent** (pending confirmation from the
in-progress research on CUGA's exact MCP client/server code — see note below):
- For MCP servers **CUGA ships and controls** (its own demo tool servers, if
  built on the official `mcp` SDK/FastMCP), full cross-process trace stitching
  is achievable by also wiring `Traceloop.init()` (or bare `McpInstrumentor`)
  into those server processes — worth doing, since it's a fully self-contained
  demonstration of the feature working end-to-end.
- For **user-configured, third-party MCP servers** (anything added via
  `mcp_servers.yaml` that CUGA doesn't control the implementation of),
  cross-process stitching is only as good as whether that external server
  happens to run compatible instrumentation — CUGA can't guarantee this, and
  the acceptance criteria/testing plan should treat "local, CUGA-owned server"
  and "arbitrary third-party server" as two different, separately-verified
  cases rather than assuming one proves the other.

**Confirmed against CUGA's actual code** (not assumed):

- **Client side, two independent paths, both patchable**:
  - `tools_env/registry/mcp_manager/mcp_manager.py` uses
    `fastmcp.Client(transport)` with `StdioTransport`/`SSETransport`/
    `StreamableHttpTransport` from `fastmcp.client.transports` — these are thin
    wrappers that call `mcp.client.stdio.stdio_client` /
    `mcp.client.sse.sse_client` / `mcp.client.streamable_http.streamable_http_client`
    directly, and `async with client:` invokes `fastmcp.client.client.Client.
    __aenter__`/`__aexit__` — all exactly the hooks Traceloop's `McpInstrumentor`
    patches.
  - `cuga_graph/nodes/chat/chat_agent/chat_agent.py` bypasses FastMCP and calls
    `mcp.client.sse.sse_client` + `mcp.ClientSession` directly (paired with
    `langchain_mcp_adapters.tools.load_mcp_tools`) — also patchable.
- **Server side, fully CUGA-owned**: every demo/generated MCP server
  (`demo_tools/docs_mcp/docs_mcp_server.py`, `demo_tools/email_mcp/mcp_server/
  server.py`, `demo_tools/crm/*/wrap_as_mcp.py`, `backend/knowledge/mcp_server.py`
  — the one actually wired into the default `mcp_servers.yaml` — and the
  dynamically-generated `saved_flows.py` template in `save_reuse_agent/utils/
  export_mcp.py`) is a `fastmcp.FastMCP` server whose `mcp.run(transport=...)`
  routes through `mcp.server.sse.SseServerTransport.connect_sse` /
  `mcp.server.stdio.stdio_server` — again, exactly the patched entry points.
- **Best fully self-contained demo candidate**: the "save & reuse" loop — CUGA
  generates `saved_flows.py` (a FastMCP/SSE server) at runtime and
  `chat_agent.py` connects back to it as a client over raw `mcp`. Both ends are
  CUGA-owned Python processes, so this is the cleanest candidate for proving
  end-to-end cross-process trace stitching works, without depending on any
  third-party server's cooperation.
- **Not part of the MCP story**: CUGA's "expose CUGA as a tool to other agents"
  claim (README) is implemented via the **A2A protocol**
  (`server/a2a/{router,agent_card,task_adapter,runner}.py`, FastAPI/
  `sse_starlette`-based) — a different protocol entirely, so Traceloop's MCP
  instrumentor doesn't apply there (A2A instrumentation, if wanted, would be a
  separate, unrelated effort). There's also an orphaned, unwired
  `server/mcp_servers/cuga.py` (official SDK, stdio) with no references
  anywhere else in the codebase — dead code, not load-bearing for this feature.
- **Nothing is wired in yet, as expected** (this is greenfield, per DP1) —
  `pyproject.toml` has `mcp[cli]>=1.23.0`, `fastmcp>=3.2.0`, and
  `langchain-mcp-adapters` today, but no `opentelemetry-instrumentation-mcp`/
  Traceloop reference anywhere in the codebase yet.

**One concrete, non-obvious implementation requirement this surfaces**:
instrumentation patches apply *per-process*. Enabling `Instruments.MCP` inside
the main CUGA backend's `Traceloop.init()` call only instruments the **client**
side (`mcp_manager.py`, `chat_agent.py`) — it does **not** reach into any demo
MCP server, because each of those runs as a **separate OS process**
(a stdio subprocess, or an independently-launched SSE server). For the
self-contained "save & reuse" demo (or any other CUGA-owned demo server) to
produce a truly unified cross-process trace, **each demo server's own `__main__`
needs its own `McpInstrumentor().instrument()` (or `Traceloop.init()`) call**,
pointed at the same exporter/endpoint as the main backend. This is a genuinely
new implementation task, not something that falls out of DP4's "enable
everything" decision alone — add it explicitly to the implementation task list
(instrument `docs_mcp_server.py`, `email_mcp/mcp_server/server.py`,
`crm/*/wrap_as_mcp.py`, `knowledge/mcp_server.py`, and the `saved_flows.py`
template in `export_mcp.py`).

---

## DP4b — A2A trace-context propagation (new, surfaced by discussion — not in the original prompt at all)

**Question:** if CUGA is called as a sub-agent/tool over A2A (or calls another
agent out over A2A via `CugaSupervisor`), does trace context flow across that
boundary the same way it can for MCP?

**Checked against CUGA's actual A2A code, not assumed:**

- **Inbound** (`server/a2a/router.py`, the FastAPI router serving `POST /a2a`):
  `_handle_jsonrpc_request()` reads `request.body()` and dispatches JSON-RPC —
  it never touches `request.headers` anywhere in the file, and there is no
  `opentelemetry.propagate.extract()` call. **Today, an incoming `traceparent`
  HTTP header from whatever called CUGA is silently dropped on the floor** —
  CUGA's own spans for that request would start a brand-new, disconnected
  trace regardless of what trace the caller was part of.
- **Outbound** (`cuga_graph/nodes/cuga_supervisor/a2a_protocol.py`, used when
  CUGA-as-supervisor delegates to an external agent over A2A): grepped for
  `traceparent`/`propagate`/`headers[` — the only header manipulation found is
  `headers["Authorization"] = f"Bearer {...}"` in three places. **No outgoing
  trace-context injection exists either.** A sub-agent CUGA calls out to has no
  way to link its own spans back into CUGA's trace.
- **Why "enable everything" (DP4) doesn't fix this for free:** A2A is plain
  HTTP/FastAPI (`from fastapi import APIRouter`), and W3C trace-context
  extraction/injection over HTTP is normally automatic **if** an ASGI/FastAPI
  OTEL instrumentor is active — but Traceloop's `Instruments` enum (checked
  directly against the SDK source) **does not include a FastAPI/ASGI member at
  all**. Enabling every instrument Traceloop ships still wouldn't touch the A2A
  HTTP boundary; this needs separate, explicit handling.
- **Relevant precedent already in the codebase — checked against the actual
  source, not just the comment:** OpenLit's init *does* instrument FastAPI
  initially, then **deliberately uninstruments it right after**, per a comment
  citing risk of capturing the `cuga_session` auth cookie
  (`openlit_init.py:257-275`). Verified against
  `opentelemetry-instrumentation-fastapi`'s actual docs and OpenLit's own
  instrumentor-invocation code (`openlit/_instrumentors.py`): header capture
  in that package is opt-in only (env var or kwarg), and OpenLit calls it with
  no such config — so this wasn't a confirmed active leak under default
  settings, more a defensive precaution against that class of risk (a future
  config change, or full-URL capture if a token were ever passed via query
  string). Doesn't change the practical conclusion: extracting only
  `traceparent`/`tracestate` from headers (Option B) is a narrower, simpler
  operation than any form of FastAPI span instrumentation regardless of
  whether the original concern was fully realized — see DP5's discussion of
  the same "propagation vs. full capture" distinction, and Option C below for
  why reusing the standard package instead doesn't actually offer a
  propagation-only mode.

| Option | Pros | Cons |
|---|---|---|
| **A. Out of scope for this change — file as a separate follow-up** | Keeps this effort scoped to MCP/LLM/tool tracing as originally framed; A2A tracing is a genuinely separate integration surface. | Doesn't provide connectivity at all — the "CUGA as a sub-agent" scenario (a first-class use case given CugaSupervisor's whole purpose) stays completely dark unless someone deliberately picks this up later. Not a real alternative to B/C on the merits — it's "don't build this now," not a different way of building it. |
| **B. Manual `propagate.extract()`/`inject()` at each A2A call site** — hand-written: extract `traceparent`/`tracestate` from `request.headers` at the top of `router.py`'s `_handle_jsonrpc_request`; inject on outgoing calls at `delegate_task_via_a2a_sdk()`, the `A2AProtocol` fallback class, and `fetch_agent_card()` in `a2a_protocol.py` | Small, precise; touches only two header keys, no HTTP span creation of any kind, no interaction with OpenLit's uninstrument step since it never touches FastAPI/httpx instrumentation. | Bespoke code for a problem the OTEL ecosystem already solves with standard, off-the-shelf libraries — not how most OTEL-instrumented HTTP services handle this. Requires enumerating every outbound call site by hand (three found here) and remembering to add propagation to any new one later — no automatic coverage. |
| **C. Standard `opentelemetry-instrumentation-fastapi` + `-httpx` + `-aiohttp-client` packages** — the conventional way any OTEL-instrumented HTTP service gets cross-service trace linking | Zero custom code; propagation is a byproduct of normal operation for every current *and future* inbound/outbound call, not a hand-maintained list. `aiohttp-client` is already in CUGA's pinned dependency graph today (pulled in transitively via OpenLit), so no new dependency conflict there. **Verified against the package's actual docs and OpenLit's own instrumentor-invocation code**: header/cookie capture is strictly opt-in (env var or kwarg) and off by default — so this doesn't reintroduce the concern that made OpenLit uninstrument these in the first place, as long as those options stay unset (which requires no code — it's the default). | No "propagation only" mode exists — every FastAPI route (not just `/a2a`) and every outbound httpx/aiohttp call gets a real request/response span as a side effect; accepted as harmless telemetry volume given the confirmed no-header-capture default, not a security cost. Requires reconciling with OpenLit's existing `FastAPIInstrumentor().uninstrument()`/`HTTPXClientInstrumentor().uninstrument()` calls (`openlit_init.py:261-267`) — those are global monkeypatch toggles, not scoped per-caller, so if OpenLit is active it would silently strip this back out unless that step is revisited. |

**Decided: Option C**, scoped the same way as MCP (DP4's save-and-reuse loop):
guarantee CUGA-to-CUGA delegation produces one linked trace; a third-party A2A
peer gets this for free only if it independently does standard header
extraction too.

**Reason:** the deciding factor was the explicit preference for standard,
ecosystem-wide instrumentation over hand-rolled code for a problem the whole
OTEL ecosystem already has a conventional answer to. Concretely, Option C wins
on two points once verified rather than assumed:
1. It automatically covers every outbound call site (`delegate_task_via_a2a_sdk`,
   `fetch_agent_card`, the `A2AProtocol` fallback class, and anything added
   later) without needing to enumerate and maintain them by hand — Option B's
   own weakness.
2. The security concern that originally made Option C look worse than Option B
   doesn't actually hold: header/cookie capture in these standard packages is
   opt-in only, off by default, and OpenLit itself already calls them with no
   such config — so choosing C doesn't reopen the risk OpenLit's uninstrument
   step was written to avoid.

**What C required — resolved in Phase 4 (docs/traceloop-instrumentation-plan.md),
not silently assumed to resolve itself:**
- Installed and enabled `opentelemetry-instrumentation-fastapi`,
  `opentelemetry-instrumentation-httpx`, and
  `opentelemetry-instrumentation-aiohttp-client`; all header-capture
  env vars/kwargs left unset (the default).
- **Reconciled with OpenLit's existing uninstrument step**: chose (ii) —
  `openlit_init.py`'s uninstrument calls are now skipped when
  `settings.observability.traceloop` is also true, leaving OpenLit's
  behavior untouched when Traceloop is off. A real subtlety found while
  implementing this: for FastAPI specifically, OpenLit's global
  `FastAPIInstrumentor().uninstrument()` turned out to never have reached
  CUGA's real server app *in either direction* — see the FastAPI
  import-order finding below. The skip is kept for symmetry with httpx (where
  it does matter) and documented as such at the uninstrument site, not
  because it changes FastAPI's real behavior.
- **A real subtlety, verified rather than assumed (same class of issue as
  DP14)**: the `message/stream` path (`_sse_stream`, an async generator
  wrapped in `EventSourceResponse`) does correctly inherit `contextvars`
  into `sse_starlette`'s generator scheduling — confirmed empirically, both
  for inbound extraction alone and for the full outbound-inject +
  inbound-extract round trip in the end-to-end tests. No special-casing
  needed for the streaming path.
- **A second real subtlety, not anticipated by this DP at all**: the plan's
  original approach for FastAPI — the same global, no-arg
  `FastAPIInstrumentor().instrument()` form used for httpx/aiohttp — turned
  out not to work. That call swaps the `fastapi.FastAPI` class reference
  (`fastapi.FastAPI = _InstrumentedFastAPI`), which only affects code that
  looks up `fastapi.FastAPI` *after* the patch runs. `server/main.py` does
  `from fastapi import FastAPI` before any observability module is
  imported, so its real app always binds the pre-patch class regardless of
  init order. Confirmed empirically (reproduction mirroring `main.py`'s
  real import order: resulting app stayed a plain `FastAPI`, inbound
  `traceparent` silently dropped). Fixed with a new
  `instrument_fastapi_app(app)` function that calls
  `FastAPIInstrumentor().instrument_app(app)` directly on the real app
  instance at its one creation site in `main.py` — this wraps the
  instance's own middleware stack in place, sidestepping the class-identity
  issue entirely.

---

## DP5 — HTTP-instrumentation security handling — RESOLVED

**Question:** `Instruments.REQUESTS`/`Instruments.URLLIB3` would span raw HTTP calls,
which risks capturing auth cookies/headers — the exact risk OpenLit's init already
defends against by uninstrumenting FastAPI/httpx post-init.

| Option | Pros | Cons |
|---|---|---|
| A. Block entirely (`block_instruments={REQUESTS, URLLIB3}`) | Simplest, directly mirrors OpenLit's existing precedent (uninstrument rather than sanitize). Zero risk of a missed header. | Loses visibility into raw HTTP calls to the tool registry / external APIs entirely — the same tool/API-call tracing gap the cuga-agent research flagged as valuable to close is left partially unaddressed for HTTP-level detail (though tool-level spans from DP9 still cover it). |
| **B. Leave enabled, no custom scrubbing** — **decided** | Consistent with DP4b's resolution: header/cookie capture confirmed opt-in-only and off by default in `opentelemetry-instrumentation-requests`/`-urllib3` (verified against source, same pattern as FastAPI), so blocking them defends against a non-default risk. No custom code — leaving them enabled is the zero-effort option, and it restores the HTTP-level visibility (URL, method, status, latency) Option A gives up. | Accepted, documented residual risk carried over from DP4b: default URL redaction only covers 4 hardcoded query-param names (`AWSAccessKeyId`, `Signature`, `sig`, `X-Goog-Signature` — signed-URL patterns), not arbitrary custom auth query params a user-configured third-party tool API might use. |

**Decided: Option B — leave `Instruments.REQUESTS`/`Instruments.URLLIB3`
enabled, no `block_instruments` entry, no custom scrubbing processor.**
Consistent with DP4b's reasoning and outcome: the specific risk this was
originally guarding against (headers/cookies in span attributes) isn't a
default-config reality, so paying for it with lost visibility isn't worth it.
The narrower residual risk (unredacted custom query-param secrets in span
URLs for third-party tool APIs CUGA doesn't control) is accepted as-is rather
than mitigated — no `url_filter`/hook-based extension planned. If this turns
out to matter in practice for a specific tool integration, it can be revisited
then rather than building speculative protection now.

---

## DP6 — Prompt/content capture default (`TRACELOOP_TRACE_CONTENT`) — RESOLVED

**Question:** Should span content include actual prompt/completion text by default?

| Option | Pros | Cons |
|---|---|---|
| A. Default off (`TRACELOOP_TRACE_CONTENT=false`), matching OpenLit's `capture_message_content=False` | Consistent privacy posture across both OTEL-based instrumentation paths already in the repo. Avoids surprising a user who enables tracing and doesn't expect full prompt/PII capture to start flowing to a collector. | Debuggability loss by default — the whole point of the `gen_ai.input.messages`/`output.messages` attributes (which Indigo's `span_to_task` reads for `Task.data.input`/`output`) requires content capture to be *on* somewhere; if it's off everywhere, Indigo/Langfuse both lose one of their most useful signals. |
| **B. Default on** (Traceloop's own default) — **decided** | Full observability out of the box — content capture is the primary value proposition of instrumenting an agentic app in the first place; a trace without prompts/completions/tool arguments shows *that* something ran, not *what* it actually did or said, which defeats the point of the effort. | Diverges from OpenLit's existing stance in the same codebase (a deliberate, documented divergence, not an inconsistency); risks logging secrets/PII to whatever OTLP endpoint is configured, especially if a user points it at a shared/less-trusted collector. |
| C. Environment-dependent default (on for local/dev exporters, off for anything pointed at a remote endpoint) | Best of both — useful during local development, safe by default in anything resembling production. | Implicit behavior based on exporter target is a subtle rule to document and can surprise users who don't read the fine print; more logic to get right. |

**Decided: Option B — default on.** Rationale: capturing what the agent actually
said and did is the crucial, load-bearing purpose of this whole effort, not an
optional debug extra — a trace tree with no message content is close to useless
for the diagnostic/analysis use cases (Indigo's failure-mechanism analysis,
manual debugging, eval trace comparison) this feature exists to serve. This is
an explicit, intentional divergence from OpenLit's `capture_message_content=False`
stance in the same codebase — document it as such in `docs/observability.md`
(implementation task 13/prompt file) so it doesn't read as an oversight or
inconsistency to a future reader comparing the two options side by side.
Users who need it off for a specific untrusted/shared deployment can still set
`TRACELOOP_TRACE_CONTENT=false` (or the equivalent `settings.toml` override) —
the default, not the ceiling, is what changed. This also resolves the earlier
open sub-question about root/tool spans needing an independent content toggle
from LLM-call content — with content on by default across the board, there's no
gap between them to reconcile.

---

## DP7 — LangGraph node-level span coverage — RESOLVED (revised)

**Question:** Should CUGA's planner/decision nodes (Task Decomposition, the
Browser/API planners, the Shortlister, etc.) get explicit manual instrumentation,
or is auto-instrumentation enough?

**First pass at this decision was too blunt.** The original framing was
"wrap every planner/decision node, unconditionally" — treat manual
instrumentation as a blanket policy applied to entire nodes regardless of
whether it adds anything. That's wrong once you account for what's already
captured automatically, confirmed on two fronts:

- **Node identity is already free.** Langfuse's `CallbackHandler` already tags
  every LLM call span with `metadata.langgraph_node` (confirmed — that's how
  `get_langfuse_data.py` groups generation timings by node today), and
  Traceloop's own LangChain auto-instrumentation captures node identity the
  same way.
- **The LLM's own output is already free.** With content capture on by default
  (DP6), the actual prompt/completion text of every LLM call is already on
  that call's span.

So for a node whose entire job is "make one LLM call and act directly on what
it says," a separate manual span adds nothing — it would just relabel
information that's already fully visible under the LLM call's own span. The
"wrap everything" version of DP7 didn't distinguish this case from the case
where it actually matters.

**The real gap is narrower and specific**: whatever CUGA's own *code* does
*with* the LLM's output after the call returns — which never appears in any
LLM call's prompt/completion text, because it happens after that call ends.
Concretely, this is worth capturing explicitly when a node does any of:

- **Parsing/validation results** — the LLM's raw output gets parsed by CUGA's
  code, and parsing can succeed, partially succeed, or fail into a fallback.
  That outcome is a code decision, invisible in the LLM span itself.
- **Post-filtering** — e.g. the Shortlister's suggested tools get validated
  against what's actually available, and invalid entries silently dropped;
  the *actually-used* list can differ from what the LLM said.
- **Retry/fallback logic** — a retry or fallback path taken by CUGA's code is
  otherwise only inferable by noticing two similar-looking LLM spans back to
  back.
- **Branching/routing decisions** — e.g. "more than N sub-tasks → use a
  hierarchical strategy instead of a flat one." The threshold check and the
  strategy it produced is CUGA's logic evaluating the output, not something
  the LLM said in its own words.
- **Aggregates across multiple LLM calls in one node** — if a node makes more
  than one LLM call (generate-then-refine), a node-level span is the natural
  place to see total calls/duration for that node, not visible from flat,
  unwrapped LLM spans.

**Decided: instrument at the point of programmatic decision-making, not the
node as a whole.** Evaluated per node (sometimes per sub-step within a node),
not as a blanket rule:
- If a node is "one LLM call, act on it directly" — no manual span needed;
  node-name metadata + captured LLM content already cover it.
- If a node's code does any of the things listed above, add an explicit
  attribute or span at *that specific point* — e.g. a
  `cuga.decomposition_strategy` attribute set right after the branching
  decision, not a generic wrapper around the whole node — so what's captured
  is exactly the information that wouldn't otherwise exist anywhere.
- This needs to be worked out per node during implementation — go through
  `task_decomposition_planning/*`, `browser/browser_planner.py`, `api/*`,
  `cuga_lite/*` and identify, for each, which parts are "LLM-output-is-the-decision"
  (skip) vs. "code decides something after the LLM call" (instrument that
  specific point).

---

## DP8 — Root span identity and naming — SUPERSEDED

**2026-08 update:** the manual `cuga.run` root span this DP designed was
implemented in Phase 1, then removed in Phase 2 after empirical verification
showed it wasn't earning its place. Driving real (non-stubbed) LangGraph
graphs — including a node that calls a second, nested graph, mirroring
`delegation.py`'s supervisor → sub-agent pattern — via both `ainvoke()` and
`astream(stream_mode="updates", subgraphs=True)` produced one coherent
`trace_id` across the outer graph, its nodes, the nested sub-graph, and the
sub-graph's own nodes, with real input/output content on every node span
(`gen_ai.task.input`/`output`), entirely from
`opentelemetry-instrumentation-langchain`'s own auto-instrumentation — no
manual span required. Comparing runs with and without the manual span showed
it added exactly one thing: a stable, graph-name-independent span name plus
the `cuga.entry_point` tag, for search convenience — nothing structural. Given
that, and given it wasn't needed anywhere, it was removed rather than kept as
unverified insurance; see `docs/traceloop-instrumentation-plan.md` Phase 2 for
the rationale and verification detail. The design discussion below is kept for
historical context, not as current behavior.

**Question:** What should the top-level per-invocation span be called and what should
it carry?

**Confirmed no existing precedent to align with** (checked via grep across
`agent_loop.py`, `graph.py`, `sdk.py`, and the whole `src/cuga/` tree — not
assumed): the only manual span name anywhere in CUGA today is
`"create-e2b-sandbox"` (`e2b_sandbox.py:205`, a differently-scoped operation).
`application_name="cuga"` (OpenLit) and `service.name=cuga` (OTEL resource
attribute) are process-level identity labels attached to every span, not a name
for the top-level invocation specifically. Both `cuga_agent.run` and any
CUGA-native alternative were equally fresh choices, not a pick between
"established" and "invented."

A follow-up question surfaced during discussion: CUGA has more than one entry
point that can trigger a top-level invocation — the Python SDK
(`CugaAgent.invoke()`), the CLI, A2A (`POST /a2a`, which already has its own
`message/send`/`message/stream` split), and upcoming `/run`/`/stream` HTTP
endpoints (on another branch). Naming the root span after the entry point
(`cuga.run` vs `cuga.stream`, etc.) would need to cover all of these
consistently, not just the two HTTP endpoints, and would trade away "one name
finds every agent invocation regardless of source" for at-a-glance readability
per entry point.

**Decided: hybrid — one canonical span name, entry point as an attribute.**
- Root span name: **`cuga.run`**, used for every entry point (SDK, CLI, A2A,
  HTTP `/run`, HTTP `/stream` once it exists) — a single name that always finds
  every top-level agent invocation with one filter, in Tempo/Grafana/Langfuse/
  Indigo alike, without needing to enumerate or remember a growing list of
  per-entry-point names as new ones are added.
- New attribute on that span capturing which entry point triggered it, e.g.
  `cuga.entry_point` with values `"http.run"` / `"http.stream"` /
  `"a2a.message_send"` / `"a2a.message_stream"` / `"sdk"` / `"cli"` — gets the
  exact same filtering/grouping power as entry-point-based naming (attribute
  filters are just as easy as name filters in every consumer), and makes
  cross-entry-point analysis (e.g. comparing streaming vs. non-streaming
  success rates for the same task type) a simple attribute group-by instead of
  a span-name join.
- `cuga.run` satisfies the "avoid the literal substring 'generation'"
  anti-pattern constraint (documented elsewhere — some consumers pattern-match
  span names containing "generation" as LLM calls regardless of attributes).
- Implementation note: this needs to be threaded through every entry point
  listed above, including the not-yet-merged `/run`/`/stream` HTTP endpoints —
  flag this as a dependency to track once that branch lands, so the new
  endpoints don't ship without the corresponding `cuga.entry_point` value.

---

## DP9 — Tool span attribute convention — RESOLVED

**Question:** gaia_agent hand-writes `tool.name`/`tool.arguments`/`tool.output` +
`gen_ai.operation.name="tool"` via raw OTEL spans. Traceloop's own built-in `@tool`
decorator instead sets a *different* attribute set (`traceloop.span.kind=tool`,
`traceloop.entity.name`, `GEN_AI_TOOL_NAME`, JSON-serialized
`traceloop.entity.input`/`traceloop.entity.output`). These are two genuinely
different conventions and the implementation prompt doesn't resolve which one to
use.

| Option | Pros | Cons |
|---|---|---|
| **A. Hand-rolled spans mirroring gaia_agent exactly** (`tool.name`/`tool.arguments`/`tool.output`/`gen_ai.operation.name`) | Guarantees byte-for-byte alignment with the one working example known to satisfy Indigo's tool-span expectations — lowest risk of an Indigo consumer failing to recognize the span as a tool call. | More manual code per call site (no decorator convenience); duplicates logic Traceloop's `@tool` decorator already provides (JSON encoding, span lifecycle, error status handling). |
| **B. Traceloop's native `@tool` decorator** | Less code — decorator handles span lifecycle, input/output JSON serialization, and error status automatically. Idiomatic "use the library as intended" approach. | `span_to_task`'s tool-span detection (per the Indigo research) is specifically described in terms of `tool.name`/`tool.arguments`/`tool.output` attributes — it's not confirmed whether it *also* recognizes `@tool`'s `traceloop.entity.input`/`output`/`GEN_AI_TOOL_NAME` shape as an equivalent. Using B without confirming this risks tool calls being invisible to Indigo despite being "correctly" instrumented per Traceloop's own docs. |
| **C. Both** — use `@tool` for the decorator convenience (span lifecycle/error handling) but explicitly also set `tool.name`/`tool.arguments`/`tool.output` inside the decorated function body — **decided** | Gets decorator ergonomics and guaranteed compatibility with the confirmed-working attribute shape. | Slight redundancy (two attribute conventions on one span) — acceptable for tool spans specifically, but shouldn't be treated as a general pattern (this is not the same "duplicate gen_ai.* attributes" anti-pattern flagged elsewhere in the prompt, since these are different attribute namespaces, not a wrapper/child duplication problem — worth being precise about that distinction when writing the code so reviewers don't conflate the two).

**Decided: Option C.** Still confirm during implementation (via a real
`indigo analyze` run against a captured trace, not just reading docs) whether
`span_to_task` also recognizes the `@tool`-decorator shape on its own — that
result doesn't change the decision (both conventions are set either way), it
just tells you whether the explicit `tool.name`/`tool.arguments`/`tool.output`
attributes are load-bearing for Indigo compatibility or redundant belt-and-suspenders.

---

## DP10 — Task/eval correlation (association properties) — RESOLVED

**Question:** Should `set_task_association_properties()` and the cuga-eval wiring
(task_id/benchmark/difficulty/session_id) ship in the same change as the
cuga-agent instrumentation, or be deferred?

**Decided: build both PRs, merge independently, no sequencing dependency
between them.** Not really a trade-off decision once traced through carefully
— the two are cleanly separable because cuga-agent's Traceloop support is
fully self-contained and non-breaking on its own:

- **What cuga-agent alone gives you, with zero cuga-eval changes**: everything
  that's just *using* CUGA works immediately once the flag is on — the demo,
  the SDK, the CLI, A2A between two CUGA instances, MCP calls to CUGA's own
  demo servers, exporting to Langfuse/a local collector/a file for Indigo, a
  coherent trace per invocation (via LangGraph's own auto-instrumentation, no
  manual root span — DP8 superseded in Phase 2), tool/node/policy/sandbox
  spans, all of it (see DP4–DP9).
  A user could even manually set `DYNACONF_OBSERVABILITY__TRACELOOP=true` for
  an eval run today without any cuga-eval code change — they'd just get
  uncorrelated traces (no `task_id`/`benchmark`/`difficulty` tagging).
- **What genuinely needs the cuga-eval PR**: per-task association-property
  tagging (the actual point of `set_task_association_properties()`), the
  `analytics/trace_comparison_rules` `OtelTraceAdapter`, bundle capture of
  Traceloop spans, and eliminating the Langfuse REST-fetch retry-loop cost —
  all additive improvements to cuga-eval's own tooling, none of which cuga-eval
  loses by not having yet if the cuga-agent PR merges first.
- **Confirmed non-breaking either direction**: cuga-agent's new module,
  settings flag (defaults `false`), and hook functions are purely additive; if
  cuga-eval's own PR merges first (unlikely but not harmful) it would just be
  calling a hook that doesn't exist yet in cuga-agent's currently-pinned
  version, caught immediately by cuga-eval's own CI/import.

**One real pre-merge check to keep, not a decision point but worth not
losing**: since cuga-eval consumes cuga-agent via an editable path dependency,
verify `uv sync` resolves cleanly in cuga-eval against the cuga-agent branch
before merging cuga-agent — specifically because adding `traceloop-sdk` means
reconciling its own transitive `opentelemetry-*` pins against the existing
`openlit` override block in `pyproject.toml`, the one place a real conflict
could actually surface.

---

## DP11 — Local trace capture mechanism — RESOLVED

**Question:** Should there be a zero-infrastructure way to capture Traceloop spans
to a local file, and if so, in what format and by what mechanism?

**How this evolved**: the original framing was a binary choice — write a
custom exporter mirroring Indigo's own bespoke format (flat JSON array of
`ReadableSpan.to_json()` objects, requiring no collector but needing new,
narrowly-scoped code), or always require the existing OTel Collector →
Tempo/Grafana docker-compose stack (no new code, but needs Docker running for
any local file capture, and doesn't natively produce Indigo's expected shape
anyway). Neither was actually the best answer:

- Traceloop/bare OTel don't ship a file exporter at all (checked directly
  against `traceloop-sdk` source — its exporter options are only
  HTTP/gRPC OTLP) — *some* custom class is unavoidable if the requirement is
  "no separate process."
- The standard OSS answer for "OTEL spans to a local file" is the official
  OpenTelemetry Collector's `fileexporter` (`opentelemetry-collector-contrib`)
  — real, widely used, zero custom code — but it fundamentally requires a
  running Collector process, which is disqualified by the explicit requirement
  for the simplest possible local run with no extra process or container.
- Given a custom in-process exporter is unavoidable, the further question was
  *what format it writes*. Mirroring Indigo's bespoke shape was the original
  plan, but Indigo itself won't consume this exporter's output anyway —
  Indigo's own experiment engine installs its own provider/exporter when it's
  the one orchestrating a run, and per DP3, Traceloop's SDK already attaches
  to whatever provider exists rather than fighting for ownership — so when
  CUGA runs *under* `indigo experiment`, Indigo's own capture mechanism wins,
  and this exporter doesn't need to (and won't) activate. This exporter is for
  **standalone CUGA usage** — a developer running CUGA directly and wanting a
  local trace file for their own inspection — not for satisfying Indigo's
  ingestion format specifically.

**Decided: a small in-process `SpanExporter` that writes standard OTLP JSON**
— not Indigo's bespoke shape, not an invented format. It reuses the *real*
shared encoding function the official OTLP exporters use internally
(`opentelemetry.exporter.otlp.proto.common.trace_encoder.encode_spans`,
confirmed present in `opentelemetry-exporter-otlp-proto-common`, already a
transitive dependency via Traceloop's own OTLP exporter usage) to build a
genuine `ExportTraceServiceRequest`, serializes it via
`google.protobuf.json_format.MessageToJson`, and appends it as one line to a
local file — no network call, no collector, no container:

```python
from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
from google.protobuf.json_format import MessageToJson
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
import threading

class LocalOtlpFileSpanExporter(SpanExporter):
    """In-process, no-network OTLP-JSON file exporter — same wire format as
    the standard OTel Collector fileexporter, written directly from this
    process instead of over the network to a running collector."""

    def __init__(self, file_path: str):
        self._path = file_path
        self._lock = threading.Lock()

    def export(self, spans) -> SpanExportResult:
        request = encode_spans(spans)
        line = MessageToJson(request, indent=None)
        with self._lock:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True
```

- **Format**: standard OTLP JSON, one `ExportTraceServiceRequest` per line —
  portable to anything that speaks OTLP, not narrowly scoped to one
  consumer's bespoke shape.
- **Behavior**: zero extra process or container — runs inside CUGA's own
  Python process.
- **No new dependency** — the encoder package is already pulled in
  transitively.
- **Default path**: under CUGA's existing `TRACES_DIR` convention
  (`cuga.config`), consistent with existing conventions rather than inventing
  a new one.
- **Scope note**: this is deliberately not trying to match Indigo's own
  ingestion format. When Indigo is the one orchestrating a run, its own
  exporter setup takes over per the DP3 mechanism described above — "Indigo
  manages its own capture in that case," this exporter serves standalone
  usage only.

**One honest caveat**: combining the real OTLP encoder with local-file writing
(rather than sending it over the network to an actual collector) is a
reasonable, sound composition of two existing, individually well-tested
pieces, but isn't itself a widely-precedented pattern the way "collector with
fileexporter" or "Indigo's bespoke array format" are — worth a basic
implementation-time sanity check (does the output actually parse as valid
OTLP JSON via a standard OTLP-JSON reader) rather than assumed correct purely
from the pieces being individually sound.

---

## DP12 — Packaging: merge into `observability` extra vs. new dedicated extra — RESOLVED

**Question:** Where does `traceloop-sdk` live in `pyproject.toml`?

| Option | Pros | Cons |
|---|---|---|
| A. Add to the existing `observability` extra (alongside `openlit`) | One install command (`pip install cuga[observability]`) turns on both OTEL-based options; simpler docs. | Forces `openlit`'s dependency footprint (and its `openai<2` pin fight) onto anyone who only wants Traceloop, and vice versa — the two libraries' transitive OTEL pins now have to be jointly reconciled in one override block rather than two independent ones, which is exactly the kind of conflict the existing `[[tool.uv.dependency-metadata]]` block was built to fight off once already. |
| **B. New dedicated extra** (e.g. `observability-traceloop`) — **decided** | Independent install/uninstall; a version conflict in one library's transitive OTEL pins doesn't block installing the other; installs only what's actually requested — no forcing OpenLit's footprint on a Traceloop-only user or vice versa. Cleaner to reason about which spans come from which system. | Another named extra to document and keep in sync in README's feature table; users who want "all tracing options" request two extras instead of one — a minor, one-time documentation cost, not a recurring one. |

**Decided: Option B.** Directly matches the stated priority — avoid
installation conflicts, and let users install only what they actually need,
not force-bundle both. Given the repo's own prior history with exactly this
class of transitive-dependency conflict (the documented `openai<2` vs
`openai>=2` fight between `openlit` and `litellm`), keeping the two OTEL
libraries' pins independently resolvable is the safer default, not just a
style preference.

---

## DP13 — Delivery scope/sequencing — RESOLVED

**Question:** One combined change across cuga-agent + cuga-eval, or phased?

This is DP10 restated at the delivery-planning level — see DP10 for the full
reasoning. **Decided: two separate PRs, both built, merged independently
whenever each is ready — no enforced order.** Not "cuga-agent first" as a rule,
just that in practice cuga-agent will likely be ready first since cuga-eval's
PR depends on the hook it exposes existing to call. The one thing to keep
regardless of order is the `uv sync` cross-repo dependency check from DP10.

---

## DP14 — Nested async-context propagation: trust-and-verify vs. proactive shim

**Question:** Langfuse needed a hand-built `ContextVar` + `TraceScopedCallbackHandler`
shim (`cuga_graph/utils/langfuse_tracing.py`) because its LangChain-callback-based
design requires the callback list to be explicitly re-threaded through every nested
`ainvoke()` call — 5 call sites were found to leak/orphan traces before the fix.
OTEL's `contextvars`-based propagation is automatic across `await` boundaries
*within the same task*, which should handle most of these for free — but not
across `asyncio.create_task()`, thread-pool, or process-boundary hops, none of
which have been confirmed absent from those 5 call sites.

| Option | Pros | Cons |
|---|---|---|
| **A. Trust native OTEL propagation, write a regression test to confirm, fix only what breaks** (chosen) | Avoids building speculative infrastructure for a problem that may not exist in OTEL's model — the whole reason Langfuse needed the shim (callback-list threading) doesn't apply to context-based propagation. Less code by default. | If any of the 5 call sites *does* cross a task/thread boundary without explicit context copying, this ships with a real orphan-span bug until the regression test (or worse, a user) catches it. |
| **B. Proactively build a defensive context-passing helper for all 5 call sites regardless of test results** | Removes the risk of a shipped regression entirely, since propagation is guaranteed by explicit code rather than assumed framework behavior. | Speculative work — likely reimplements something OTEL already does correctly, adding complexity for a problem that may not exist. Also the "5 call sites" list is a copy of Langfuse's known problem sites, not confirmed as OTEL's problem sites; the risk profile may not even be the same set of places. |

**Current assumption:** Option A. **Rationale:** don't build defensive
infrastructure for a documented Langfuse-specific failure mode without first
confirming OTEL's fundamentally different propagation model actually has the same
problem. **This is explicitly a "verify, don't assume" item — see Testing
Strategy below**, not a closed decision either way until that test exists.

---

# Testing Strategy

Testing spans several levels; each maps to specific risk from the decision points
above.

## 1. Unit tests (`tests/unit/test_traceloop_tracing.py`)

- **Init idempotency**: calling `init_traceloop()` multiple times (from server
  startup, SDK usage, and test fixtures) produces exactly one `TracerProvider`
  registration — mirrors the existing coverage pattern for OpenLit's
  `_initialized` flag.
- **TracerProvider ownership (DP3)**: with OpenLit's provider already installed,
  confirm `init_traceloop()` still results in a Traceloop span processor attached
  to that same provider (assert on `provider._active_span_processor` /
  by capturing exported spans through Traceloop's configured exporter) — this
  test should confirm the guaranteed-attach behavior DP3 now relies on, and would
  catch a regression if a future change reintroduces an early-return guard.
  Also assert exported spans carry usable resource attributes even when OpenLit
  initialized the provider first (the DP3 residual caveat).
- **Root span attributes (DP8, superseded)**: no manual root span exists —
  see the DP8 update above. Instead, assert LangGraph's own auto-instrumentation
  produces one coherent `trace_id` per invocation with real input/output
  content on node spans (the Phase 2 real-graph coherence test in
  `tests/unit/test_traceloop_tracing.py` covers this).
- **Tool span shape (DP9)**: whichever option is chosen, assert the resulting
  span(s) carry `tool.name`/`tool.arguments`/`tool.output` — this is the one
  attribute contract that must not silently regress regardless of which
  decorator/manual approach is used internally.
- **Instrument allow/block-list (DP4/DP5)**: assert `Instruments.REQUESTS`/
  `Instruments.URLLIB3` are in `block_instruments` and don't produce spans even
  if `requests`/`urllib3` are exercised during a test run — a concrete guard
  against the security regression DP5 is meant to prevent.
- **Content-capture default (DP6)**: assert `TRACELOOP_TRACE_CONTENT` (or the
  equivalent init parameter) defaults to the agreed value, with a test that
  fails loudly if a future change silently flips the default.

## 2. Nested-context propagation regression test (resolves DP14)

A dedicated test that drives each of the 5 previously-Langfuse-orphaning call
sites (`cuga_lite/adapter/sandbox_node.py`, `cuga_lite/helpers/find_tools.py`/
`prompt_utils.py`, `cuga_lite/nl_auto_continue_classifier.py`,
`policy/enactment.py`, `utils/context_management_utils.py`) with Traceloop
enabled, and asserts every span produced during that path shares the same
`trace_id` as the top-level `cuga_agent.run` span — i.e., no orphan root traces.
This test's outcome is the actual answer to DP14, not an assumption.

## 3. LangGraph node-context test (resolves DP7)

A focused test that, from inside an actual CUGA graph node callback (not a
synthetic function), calls `trace.get_current_span()` and asserts whether it
returns a real span (context propagated) or a NoOp span (Indigo's claim holds for
CUGA too). This single test result determines whether DP7's `@task` wrapping is
load-bearing or unnecessary for each node — run it before wrapping every
candidate node, not after.

## 4. Local integration test — OTel Collector round-trip

With `[observability] traceloop = true` and the exporter pointed at the existing
`deployment/docker-compose/openlit/` collector stack, run `cuga start demo` (or
an SDK-level smoke invocation) and confirm spans land in Tempo, queryable by
`service.name=cuga`. This validates the "any generic OTEL collector" acceptance
criterion using infrastructure that's already built and presumably already
trusted for the OpenLit path.

## 5. Langfuse OTLP end-to-end test

Point the exporter at a local/self-hosted Langfuse instance's `/api/public/otel`
endpoint (Basic-auth header per Langfuse's documented OTLP contract) and confirm:
- A single coherent trace tree appears per `agent.invoke()` (no orphan sub-traces
  — cross-check against #2 above).
- `gen_ai.*` attributes are recognized by Langfuse's own OTEL-ingestion span
  filtering (i.e. it correctly identifies LLM generation spans without needing
  Langfuse-SDK-specific attributes).

This can likely reuse or adapt the existing (Langfuse-SDK-based)
`tests/unit/test_langfuse_tracing.py` fixtures/mocking patterns where sensible,
though the ingestion path itself (OTLP HTTP vs. SDK calls) is different enough
that a new integration-level test is warranted rather than a unit-test mock.

## 6. Indigo contract test — the one that actually validates the whole feature's premise

Capture a real trace (via the file exporter from DP11, or exported from the local
collector) from a representative CUGA run, then run
`indigo analyze run --trace-dir <captured> -p quick` against it (using the
`indigo-platform` checkout already present as a sibling repo) and assert:
- A non-empty `Task` artifact set is produced.
- Recognizable `gen_ai.task.input`/`output` is found — **but not necessarily
  on the literal trace root** (DP8 superseded in Phase 2: there's no manual
  root span anymore, and the auto-created `invoke_agent {graph_name}` root
  doesn't itself carry these attributes; they're one level down, on the
  graph's own `{graph_name}.workflow` child span, per Phase 2's empirical
  verification). Confirm `span_to_task` actually finds them there — this is a
  real behavior change from DP8's original design, not just a naming change.
- Tool-call spans surface as tool tasks/actions (validates DP9's chosen
  attribute convention against the *actual* `span_to_task` code, not just the
  documentation describing it — this is the authoritative check for DP9,
  more reliable than reading `span_to_task`'s README).

This test is the closest thing to a genuine "does this feature work" check,
since it exercises the actual consumer rather than asserting our own
instrumentation code did what we intended.

## 6b. A2A trace-linking test (DP4b — scoped in)

- **Inbound unit test**: POST to `/a2a` with a synthetic `traceparent` header
  set, for both `message/send` and `message/stream` — assert CUGA's own spans
  for that request carry the same `trace_id` as the injected header, checked
  independently for each method (per the SSE-scheduling subtlety flagged in
  DP4b — don't assume one path's pass implies the other's).
- **Outbound unit test**: drive `delegate_task_via_a2a_sdk()` against a mock
  HTTP endpoint and assert the outgoing request's `traceparent` header matches
  the current span's trace id. Repeat for the `A2AProtocol` fallback class and
  `fetch_agent_card()`.
- **End-to-end CUGA-to-CUGA test** (the real acceptance bar per DP4b): stand up
  two CUGA instances (or a real instance + itself as the "external agent" in
  `CugaSupervisor` config), delegate a task from one to the other over A2A with
  both instrumented, and confirm the exported spans from *both processes* share
  one `trace_id` — the A2A equivalent of the MCP save-and-reuse cross-process
  test in DP4.

## 7. cuga-eval integration test (only once DP10/DP13 phase 2 is in scope)

Run one benchmark task (e.g. a single BPO task) with Traceloop enabled end-to-end
and assert the exported spans/trace carry `task_id`/`benchmark`/`difficulty`/
`session_id` as association properties — directly validates the eval-correlation
gap this feature is meant to close, without depending on Langfuse's REST API or
its ingestion lag (a live improvement over the current
`fetch_langfuse_metrics_for_trace` retry-loop approach, worth calling out
explicitly in the PR description as a concrete before/after).

## 8. Flag-combination matrix (manual or parametrized)

Since three instrumentation options can each be independently toggled, exercise
at minimum:

| openlit | langfuse_tracing | traceloop | Expected behavior |
|---|---|---|---|
| off | off | on | Traceloop installs the provider (nothing else has); spans flow to its configured exporter. |
| on | off | on | Per DP3: OpenLit installs the provider first; Traceloop must still attach its own span processor to it and spans must still reach Traceloop's exporter — this row is the direct regression test for DP3's guarantee, not an open question. |
| off | on | on | Confirm no regression in Langfuse's existing nested-trace-propagation fix; confirm Traceloop's processor attaches cleanly alongside Langfuse's own OTEL `TracerProvider` usage (`e2b_sandbox.py`'s `get_client()` path) rather than conflicting with it. |
| on | on | on | All three at once — must not crash startup, and Traceloop's exporter must still receive spans (same guarantee as row 2, compounded). |

## 9. Security regression test (resolves DP5's guarantee)

Assert that no exported span, across the full test suite run with Traceloop
enabled, contains an `http.request.header.*` attribute matching a
denylist (`authorization`, `cookie`, `set-cookie`) — a concrete, automatable
check that DP5's block-list choice is actually effective, not just configured.

---

## Summary of items needing a decision before implementation starts

- DP1: resolved — Traceloop SDK (Option A), confirmed.
- DP2: resolved — additive third option (Option A), confirmed.
- DP3: resolved — always call `Traceloop.init()` unconditionally, no
  existing-provider guard; rely on the SDK's native attach-to-existing-provider
  behavior (confirmed from source). Verify via the flag-combination matrix test,
  not a design discussion.
- DP4: resolved — enable-all (`instruments=None`), block-list only for DP5's
  security exclusions. Confirmed CUGA's MCP client code (`mcp_manager.py`,
  `chat_agent.py`) and demo/generated MCP servers (docs_mcp, email_mcp, crm,
  knowledge, saved_flows) all route through the exact functions
  `opentelemetry-instrumentation-mcp` patches — cross-process stitching is
  achievable for CUGA-owned servers, and per the SEP-414 MCP spec addition this
  isn't Python-specific, just requires the other side to run any OTEL SDK and
  implement the same `_meta` convention. New task: each demo MCP server needs
  its own `McpInstrumentor`/`Traceloop.init()` call in its own `__main__`
  (patches are per-process — the backend's own init doesn't reach
  subprocess/separate server instances).
- DP4b: resolved — in scope, Option C. Install and enable the standard
  `opentelemetry-instrumentation-{fastapi,httpx,aiohttp-client}` packages
  (no custom propagation code); leave header-capture options unset (the
  default) so no header/cookie data reaches span attributes. Scoped to
  guarantee CUGA-to-CUGA works; third-party A2A peers get it for free only if
  they also do standard header extraction. Still open: reconcile with
  OpenLit's existing `FastAPIInstrumentor().uninstrument()`/
  `HTTPXClientInstrumentor().uninstrument()` calls (not yet decided whether to
  revisit that step or only rely on this when OpenLit is off), and verify the
  `message/stream` (SSE) path separately from `message/send` — contextvar
  propagation into `sse_starlette`'s generator scheduling is unconfirmed.
- DP5: resolved — leave `Instruments.REQUESTS`/`URLLIB3` enabled (Option B), no
  custom scrubbing. Same reasoning as DP4b; accepted residual risk (narrow
  default query-param redaction) rather than mitigated.
- DP6: resolved — default on (Option B). Content capture is the core value
  of the feature, not an optional extra; explicit divergence from OpenLit's
  `capture_message_content=False`, documented as intentional. Users can still
  opt out per-deployment via `TRACELOOP_TRACE_CONTENT=false`.
- DP7: resolved (revised) — instrument at the point of programmatic
  decision-making, not the node as a whole. Node identity and LLM
  input/output are already captured automatically (Langfuse and Traceloop
  both surface `langgraph_node`; content capture is on by default per DP6),
  so manual spans/attributes are only added where CUGA's code does something
  with the LLM's output that wouldn't otherwise be visible — parsing/validation
  outcomes, post-filtering, retry/fallback paths, branching decisions,
  multi-call aggregates. Needs a per-node pass during implementation to
  identify which parts qualify. Was flagged to revisit at the end of the
  review — that revisit happened and produced this revised framing; now
  fully closed.
- DP8: superseded in Phase 2 — the manual root span was implemented, then
  removed after empirical verification showed LangGraph's own
  auto-instrumentation already gives coherent, correctly-nested traces with
  real input/output content, with nothing manual needed. Phase 2 instead
  ensures `init_traceloop()` runs before any graph call in every process
  (previously only true for `cuga.sdk`'s own call sites, not the web UI / A2A
  -simple / evaluate-CLI path, which drive the graph directly).
- DP9: resolved — Option C (both decorator and explicit attributes). Still
  worth confirming via the Indigo contract test whether the explicit
  attributes are load-bearing or redundant, but that doesn't change the
  decision either way.
- DP10/DP13: resolved — build both PRs (cuga-agent instrumentation, cuga-eval
  wiring), merge independently whenever each is ready, no enforced order.
  Confirmed non-breaking either direction. Keep the `uv sync` cross-repo
  dependency check before merging cuga-agent.
- DP11: resolved — small in-process `LocalOtlpFileSpanExporter`, writing
  standard OTLP JSON (via the real shared `encode_spans` encoder, no new
  dependency), no collector/container needed. Deliberately not matching
  Indigo's bespoke format — Indigo manages its own capture when it's
  orchestrating a run (per DP3's provider-attach behavior), this exporter is
  for standalone CUGA usage only.
- DP12: resolved — dedicated extra (Option B), to avoid installation conflicts
  and let users install only what they need.
- DP14: resolved — Option A (trust-and-verify), as originally assumed. Verify
  via the regression test, don't build a defensive shim preemptively.
