# Architecture — how this relates to core CUGA, and why it's safe to merge

This doc answers two questions: **what does the event layer reuse from core CUGA vs. add
vs. delegate to Activepieces (AP)?** and **why is merging it non-breaking?**

Related reading: [README.md](README.md) (the model in one breath) · [DESIGN.md](DESIGN.md)
(channels / integrations / triggers) · [decisions/](decisions/) (the ADRs cited throughout).

Grounded in a read of `cuga-agent-july`. The whole layer is opt-in behind `EVENTS_ENABLED`;
with the flag off, CUGA is byte-for-byte unchanged. The events code is self-contained under
`src/cuga/backend/events/` (verified: the package is one flat directory of ~30 modules — no
edits scattered across core except the two guarded touch points in §2).

Guiding principle: **do not duplicate CUGA APIs; do not build a connection/OAuth framework —
AP owns that.**

---

## 1. Reuse map — reuse ♻️ vs. add ➕ vs. AP ⚡

### Legend
- ♻️ **REUSE** an existing CUGA API / subsystem (cited)
- ➕ **ADD** — genuinely new; lives in `src/cuga/backend/events/`
- ⚡ **AP** — Activepieces owns it; CUGA at most reads / proxies

### The map
| Capability | Verdict | Where |
|---|---|---|
| Create / update / list an **agent** | ♻️ REUSE | `POST /api/manage/config/draft` + publish → `config_store.save_config()` (`agent_configs` table, versioned, multi-`agent_id`) |
| Attach **MCP servers / tools** to an agent | ♻️ REUSE | `tools[]` in the config → `managed_mcp.tools_to_registry_yaml()` → `.cuga/managed_mcp_servers.yaml` |
| **Run** an agent (produce an answer) | ♻️ REUSE | the `/stream` runtime / `event_stream()` (LangGraph); `/invoke` collects it into one response |
| **The agent graph** (reasoning / supervisor / sub-agents) | ♻️ REUSE | `DynamicAgentGraph`, `CugaSupervisor` — reused per-agent behind the `cuga` runtime backend |
| **Memory / conversation context** | ♻️ REUSE | `X-Thread-ID` + conversation-thread store + LangGraph `MemorySaver`; the `/invoke` envelope carries `thread_id` straight through |
| **Config store** (agent definitions) | ♻️ REUSE | CUGA's multi-`agent_id` `agent_configs` store — the source of truth for both worker backends |
| **Secrets / credentials** (CUGA-side) | ♻️ REUSE | `/api/secrets` → `secrets_store.py` (**Fernet**-encrypted at rest, or Vault); refs `db://` / `vault://` |
| **The frontend** | ♻️ REUSE | existing chat/manage UI + SSE; events layer adds Studio views, no fork of the app shell |
| **Auth** on new endpoints | ♻️ REUSE | `require_auth` / `require_chat_access` / `require_manage_access` |
| **`POST /invoke`** (AP / channel callback seam; run agent on a normalized envelope, return / deliver) | ➕ ADD | new router; `X-Gateway-Token` (machine-to-machine) |
| **`POST /api/concierge`** (NL → reuse/create worker + arm trigger) | ➕ ADD | new router; reuses the `/stream` SSE machinery |
| **the events package** itself | ➕ ADD | `src/cuga/backend/events/` — envelope, concierge, runtime port, subscriptions, direct backends, AP client |
| **concierge router + meta-tools** (`list_capabilities`, `answer_now`, `find_or_create_flow`) | ➕ ADD | router over PRE-BUILT agents (decision [0005](decisions/0005-runtime-router-over-prebuilt-agents.md) — no agent creation); `find_or_create_flow` reuses/creates an AP flow |
| **direct backends** for chat channels (web / Telegram / Slack / Discord) | ➕ ADD | `slack_direct.py`, `discord_direct.py`, `box_direct.py`, … (decision [0008](decisions/0008-direct-backends-for-channels.md)) |
| **subscription index** (which AP flows we built → agent, deliver-to; for listing / reuse) | ➕ ADD | one thin table (`subscriptions.py`), not CUGA's config store |
| **Connections** to apps (Gmail/Box/GitHub/Slack/Telegram) | ⚡ AP | AP connection store; OAuth authorized in AP's connect UI (can't be minted headlessly) |
| **Integration credentials** (OAuth tokens, refresh) | ⚡ AP | AP encrypts + refreshes connections — the thing CUGA lacks |
| **Triggers** (cron / webhook / poll / app-event / run-once) | ⚡ AP | AP trigger pieces → call back `POST /invoke` |
| **Delivery** (send Telegram/Slack/email) | ⚡ AP | AP connector send-steps (chat channels use direct backends) |
| **Run history / observability** | ⚡ AP | AP run history is the one pane |

### Net new surface on CUGA
Essentially **two endpoints** (`/invoke`, `/api/concierge`) + the concierge router + a
subscription index + direct channel backends — all inside the events package. Everything
else is reuse or AP.

### What CUGA is MISSING that AP supplies
CUGA has **no** Activepieces / OAuth / connection concept today (confirmed by grep). That gap
is intentional — AP fills it (see decision [0001](decisions/0001-ap-as-the-event-engine.md),
[0003](decisions/0003-credentials-ownership.md)). CUGA's job stays "define + run agents,
securely."

---

## 2. Blast radius / compatibility — additive and non-breaking

### Blast radius on core CUGA
| Change | Kind | Touches core? |
|---|---|---|
| New routers `POST /invoke`, `POST /api/concierge` | `register_events_routes(app, ...)` | **No** — additive, existing routes untouched |
| Provision a worker = `config_store.save_config(agent_id=…)` | *calls* an existing API | **No** — uses CUGA's multi-agent store as-is |
| Read/write CUGA-side secrets | *calls* `/api/secrets` | **No** |
| Subscription index | **new** table (own DB, `EVENTS_DB`) | **No** — no change to existing tables/migrations |
| Concierge init + AP engine client | added to the **lifespan** hook (`main.py`) | **Light** — appends to an existing hook, gated by the flag |
| Run an arbitrary `agent_id` at request time | **extends** the runtime | one core-adjacent item, isolated to the `/invoke` path (see §4) |

Everything except the last row is a new file or a call into an existing API. The two genuine
touches on `main.py` are (a) `register_events_routes` and (b) a few lines in the lifespan
startup — both **guarded by `EVENTS_ENABLED`** (`main.py:1757` onward:
`if _events_enabled(): …`).

### Is it a breaking change? — No, because it's opt-in
The whole layer is behind `EVENTS_ENABLED` (default **off**):

- **Flag off** → CUGA behaves **byte-for-byte as today**. No new routes mounted, no lifespan
  additions run, no new tables opened. The events import is even wrapped in `try/except` so a
  missing dep can't affect boot. Existing users see zero difference.
- **Flag on** → the two endpoints mount, the concierge + AP client start, the subscription
  index DB opens.

This is the contract that makes it non-breaking: **vanilla CUGA is unchanged when the flag is
off.** No existing endpoint, schema, or behavior is modified — only *added to*.

### Self-containment (verified)
`src/cuga/backend/events/` is one flat package (`envelope.py`, `concierge.py`, `runtime.py`,
`subscriptions.py`, `agent_store.py`, `ap_engine.py`, the `*_direct.py` channel backends,
`app.py` for the routes, etc.). Core CUGA files are not edited except the two guarded touch
points above.

---

## 3. The framework-swap story — the `AgentRuntime` port

The whole event plane — AP, the concierge's decision logic, channels, integrations, triggers,
subscriptions, the `/invoke` HTTP seam — is **framework-agnostic**. AP never mentions CUGA;
`/invoke` is plain HTTP. Only **two legs** bind to an agent framework:

1. **Defining an agent** (provision → save config)
2. **Running an agent** (execute → answer, with per-thread memory)

Both go behind **one interface — the `AgentRuntime` port** (`runtime.py`), so swapping the
worker framework is *writing one adapter*, not a rewrite:

```
class AgentRuntime:            # runtime.py
    upsert_agent(spec)   -> agent_id
    get_agent(agent_id)  -> spec | None
    list_agents()        -> [spec]
    run(agent_id, thread_id, text, *, deliver?) -> answer   # memory keyed by thread_id
```

Implemented backends (`spec.backend` = `react | cuga`):

- **`CugaRuntime`** (`backend="cuga"`) — the **default worker backend**. Builds a CUGA
  `DynamicAgentGraph` via `get_or_build_agent_graph` / `_cuga_bridge.build_worker_graph`;
  workers get CUGA's policies / knowledge / supervisor / tools. There is **no silent react
  fallback** — if the CUGA stack is unavailable, it fails loud (fallback only when the
  explicit `EVENTS_CUGA_FALLBACK_REACT` opt-in is set, for dev / partial envs).
- **`ReactRuntime`** (`backend="react"`) — LangGraph `create_react_agent` + `MemorySaver`.
  The concierge itself runs on react; it's also the reference "some other framework" adapter.
- **`StubRuntime`** — for tests.

Both real adapters import langgraph/cuga **lazily inside methods**, so the events layer
imports cleanly without either installed. The concierge and event plane depend on
`AgentRuntime`, never on CUGA internals directly — that's the discipline that keeps a
framework swap to a single new adapter. See decision
[0005](decisions/0005-runtime-router-over-prebuilt-agents.md).

Trade-off: the port must express whatever the agent needs (tools, secrets refs, memory). Keep
it small and capability-based; map CUGA-specific config shapes *inside* `CugaRuntime`, don't
leak them through the port.

---

## 4. Notable findings

### Multi-agent + thread_id — preserved by *using* CUGA, not bypassing it
- The **concierge is itself an agent**; each **worker is a distinct `agent_id`** created via
  `save_config`. Reuse-or-create maps onto the `agent_id` lifecycle; workers show up in the
  manage UI like any agent. We *use* CUGA's multi-agent store, not shadow it.
- The normalized `/invoke` envelope **carries `thread_id`** end-to-end:
  `channel message → envelope.thread_id → X-Thread-ID → CUGA thread → memory`. A chat's
  `thread_id` = the chat id (follow-ups keep context); a standing trigger (cron / push) gets a
  **stable per-subscription `thread_id`**, so each watcher accrues its own context.
- **Invariant:** every run goes through a CUGA `agent_id` + `thread_id`; the event plane never
  invents a parallel memory. CUGA's `/api/conversation-*` endpoints keep working for these
  threads.

### CUGA's runtime is single-agent today — but this stays additive (verified)
CUGA's request runtime runs only `cuga-default` (plus an optional draft variant): two graphs
pre-built at startup, toggled by `X-Use-Draft`. The **config store** is multi-`agent_id`, but
`/stream` hardcodes the id (`agent_id = "cuga-default"  # TODO: get from request …`). CUGA's
"multi-agent" means supervisor + sub-agents *within one config* (`CugaSupervisor`), not
multiple independently-runnable top-level `agent_id`s.

The good news: `event_stream()` already accepts a **pre-built graph**
(`run_agent = agent if agent is not None else app_state.agent`). So `/invoke` builds the graph
per-agent and runs it — **without touching `/stream`**. `DynamicAgentGraph` is reusable
per-agent (parameterless `__init__`, idempotent `build_graph`). This is exactly what
`CugaRuntime.run` does. The result: multi-agent execution is confined to the new `/invoke`
path — `/stream` and `cuga-default` are untouched, so it stays non-breaking.

### Secrets — two tiers, both encrypted, nothing plaintext in config
- **Integration app creds** (Gmail/Box OAuth, GitHub PAT) → **AP** connection store
  (encrypted, OAuth-refreshed).
- **CUGA-side creds** (LLM keys, MCP tokens, `X-Gateway-Token`) → CUGA **secrets subsystem**
  (**Fernet** at rest, or Vault). Configs reference them by `db://` / `vault://`.

---

## Bottom line
- **Core CUGA:** near-zero blast radius; additive; **opt-in `EVENTS_ENABLED` = non-breaking**.
- **Swapping frameworks:** one new `AgentRuntime` adapter, not a rewrite (CUGA is the default;
  react is the reference alternate).
- **Multi-agent + thread_id:** preserved by construction — workers are `agent_id`s, and
  `thread_id` flows through the envelope into CUGA's memory.
