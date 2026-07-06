# Impact & compatibility

Answers the four hard questions: **how much does this touch core CUGA · is it breaking ·
what if I swap the agent framework · how are multi-agent + thread_id preserved.** Plus
secrets security and the one thing to verify.

---

## 1. Blast radius on core CUGA — small, and mostly additive
| Change | Kind | Touches core? |
|---|---|---|
| New routers `POST /invoke`, `POST /api/concierge` | `app.include_router(...)` | **No** — additive, existing routes untouched |
| Provision a worker = `config_store.save_config(agent_id=…)` | *calls* an existing API | **No** — uses CUGA's multi-agent store as-is |
| Read/write CUGA-side secrets | *calls* `/api/secrets` | **No** |
| Subscription index | **new** table (own DB) | **No** — no change to existing tables/migrations |
| Concierge init + AP engine client | added to the **lifespan** hook (`main.py`) | **Light** — appends to an existing hook; gate behind a flag |
| Run an arbitrary `agent_id` at request time | may **extend** the runtime | **Maybe** — the one real core-adjacent item (see §5) |

Everything except the last row is either a new file or a call into an existing API. The two
genuine touches on `main.py` are (a) `include_router` (trivial) and (b) a few lines in the
lifespan startup — both **guarded by a feature flag**.

## 2. Is it a breaking change? — No, if it's opt-in
Gate the whole layer behind a config flag (e.g. `settings.events.enabled`, default **off**):
- **Flag off** → CUGA behaves **byte-for-byte as today**. No new routes mounted, no lifespan
  additions run, no new tables opened. Existing users see zero difference.
- **Flag on** → the two endpoints mount, the concierge + AP client start, the subscription
  index DB opens.

This is the contract that makes it non-breaking: **vanilla CUGA is unchanged when the flag is
off.** No existing endpoint, schema, or behavior is modified — only *added to*.

## 3. Portability — "if I move off the CUGA agent, am I in a mess?" → No, behind one port
The whole event plane — **AP, the concierge's decision logic, channels, integrations,
triggers, subscriptions, the `/invoke` HTTP seam** — is already **framework-agnostic**. AP
never mentions CUGA; `/invoke` is plain HTTP. Only **two legs** bind to CUGA:

1. **Defining an agent** (provision → save config)
2. **Running an agent** (execute → answer, with per-thread memory)

Put those behind **one interface — the `AgentRuntime` port** — and swapping frameworks
becomes *writing one adapter*, not a rewrite:

```
interface AgentRuntime:
    upsert_agent(spec)              -> agent_id        # CUGA: config_store.save_config
    get_agent(agent_id)            -> spec | None      # CUGA: config_store.load_config
    list_agents()                  -> [spec]           # CUGA: config store query
    run(agent_id, thread_id, input, *, deliver?) -> answer   # CUGA: event_stream over X-Thread-ID
    # secrets + tools are referenced by the spec; memory is keyed by thread_id
```

- The concierge and event plane depend on **`AgentRuntime`**, never on CUGA internals directly.
- Default implementation = **`CugaAgentRuntime`** (thin — it just calls `/api/manage/*`,
  `/api/secrets`, and the `/stream` runtime). This satisfies "reuse CUGA, don't duplicate":
  the reuse is *confined behind the port*.
- Swap to another framework = write `FooAgentRuntime`. AP, channels, triggers, subscriptions,
  `/invoke` all keep working unchanged.

**Where you'd get into a mess:** letting the concierge call CUGA internals scattered
everywhere. The port is the discipline that prevents it. **Recommendation: define the
`AgentRuntime` port on day one**, even though CUGA is the only implementation for now.

> Trade-off to note: the port must express whatever the agent needs (tools, secrets refs,
> memory). Keep it small and capability-based; don't leak CUGA-specific config shapes through
> it (map them inside `CugaAgentRuntime`).

## 4. Preserving CUGA's multi-agent + thread_id — by *using* them, not bypassing
This is a hard requirement, and the design **leans on** these rather than reinventing:

**Multi-agent** — CUGA keys configs by `agent_id` and is multi-agent-ready.
- The **concierge is itself a CUGA agent**; each **worker is a distinct `agent_id`**
  (`resume_judge`, `market_briefer`, …) created via `save_config`.
- Reuse-or-create maps directly onto the `agent_id` lifecycle. We are *using* CUGA's
  multi-agent store, not shadowing it. Workers show up in the manage UI like any agent.

**thread_id / context** — CUGA tracks context by `thread_id` (`X-Thread-ID`, the
conversation-thread store, LangGraph memory).
- The normalized `/invoke` envelope **carries `thread_id`** end-to-end:
  `channel message → envelope.thread_id → X-Thread-ID → CUGA thread → memory`.
- **Per surface:** a chat's `thread_id` = the chat id (follow-ups keep context). A standing
  trigger (CRON/PUSH) gets a **stable per-subscription `thread_id`**, so each watcher accrues
  its own context over time.
- We do **not** invent a parallel memory — CUGA's conversation history + memory remain the
  source of truth, and its `/api/conversation-*` endpoints keep working for these threads.

**Invariant:** every agent run in this system goes through a CUGA `agent_id` + `thread_id`.
The event plane never sidesteps them.

## 5. Multi-agent execution — VERIFIED (2026-07-01)
**Verdict: NO — CUGA's runtime is single-agent today.** It runs only `cuga-default` (plus an
optional draft variant), as two graphs pre-built at startup and toggled by the `X-Use-Draft`
header. The config store *is* multi-agent-ready, but the runtime hardcodes the id. Evidence:
- `POST /stream` reads only `X-Use-Draft` — **no `agent_id`** param/header (`main.py:2093-2141`).
- Both graphs are built **once at startup** in the lifespan hook (`main.py:814-831, 890-918`),
  stored as `app_state.agent` / `draft_app_state.agent`.
- Explicit marker: `agent_id = "cuga-default"  # TODO: get from request if multi-agent support needed` (`main.py:2849`).
- **CUGA's "multi-agent" = supervisor + sub-agents WITHIN one config** (`CugaSupervisor`,
  `graph.py:279-397`) — meaning (b), *not* multiple independently-runnable top-level `agent_id`s.
- `thread_id` → LangGraph `MemorySaver` checkpointer keyed per thread (`graph.py:122-124`,
  `main.py:1312-1314`).

**The good news — it stays additive.** `event_stream()` already accepts a **pre-built graph**:
`run_agent = agent if agent is not None else app_state.agent` (`main.py:1286`). So our new
`POST /invoke` can do: `get_or_build_agent_graph(agent_id) → event_stream(agent=that_graph,
thread_id=…)` — **without touching `/stream`**. The only new runtime code is one helper:

```
get_or_build_agent_graph(agent_id) -> DynamicAgentGraph   # LRU-cached
    cfg = config_store.load_config(agent_id)              # already supports agent_id
    g = DynamicAgentGraph(...); await g.build_graph()     # __init__ parameterless, build idempotent → safe per-agent
    return g
```
`DynamicAgentGraph` is reusable per-agent (parameterless `__init__`, idempotent `build_graph`).
**Effort ≈ 150–200 LOC, low risk, all in the new `/invoke` path — `/stream` and `cuga-default`
untouched → still non-breaking.** This is the CUGA adapter of the `AgentRuntime.run` port.

## 6. Secrets security (recap)
Two tiers, both encrypted, nothing plaintext in config:
- **Integration app creds** (Gmail/Box OAuth, GitHub PAT) → **AP** connection store (encrypted,
  OAuth-refreshed).
- **CUGA-side creds** (LLM keys, MCP tokens, `X-Gateway-Token`) → CUGA **secrets subsystem**
  (**Fernet** at rest, or Vault). Configs reference them by `db://` / `vault://`.

## Bottom line
- **Core CUGA:** near-zero blast radius; additive; **opt-in flag = non-breaking**.
- **Swapping agents:** not a mess **if** everything agent-related goes through the
  `AgentRuntime` port (CUGA is just the first adapter).
- **Multi-agent + thread_id:** preserved by construction (workers = `agent_id`s;
  `thread_id` flows through the envelope into CUGA's memory).
- **Verify:** runtime execution of an arbitrary `agent_id` — the one possible core touch.
