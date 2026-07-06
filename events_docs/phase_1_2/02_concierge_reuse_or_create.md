# 02 — Concierge = runtime ROUTER over pre-built agents

Agents are **pre-built by a builder** (skill + tools + bound channels/integrations). At runtime
the concierge ([`concierge.py`](../../src/cuga/backend/events/concierge.py)) is a **router**, never
an agent factory: it lists what exists and routes the utterance to **answer-now**, **reuse/create
a flow**, or **decline**. Decision: [0005](../decisions/0005-runtime-router-over-prebuilt-agents.md).

```mermaid
sequenceDiagram
    autonumber
    participant U as End user<br/>(web / telegram)
    participant CO as Concierge (react router)
    participant ST as AgentStore<br/>(pre-built agents)
    participant W as Worker (CUGA)
    participant AP as Activepieces
    participant SUB as SubscriptionStore

    U->>CO: utterance (via /api/concierge)
    CO->>ST: list_capabilities(scope) — agents + their channels/integrations
    ST-->>CO: [pricebot, papers, mailbot, ...]
    alt existing agent answers NOW
        CO->>W: answer_now(agent, task) — run (diagram 01)
        W-->>CO: answer
        CO-->>U: reply
    else standing request (cron / poll)
        CO->>SUB: find_by_dedup_key(agent+source+cadence+sink+owner)
        SUB-->>CO: hit = reuse · miss = create
        CO->>AP: (miss) create schedule flow to /invoke
        CO->>SUB: (miss) upsert(subscription, dedup_key)
        CO-->>U: "armed" or "reused"
    else nothing fits
        CO-->>U: DECLINE — ask a builder
    end
    Note over CO,AP: per-user integration not connected → CONNECT NEEDED (diagram 08)
```

**Verified live** (`live_server_e2e_check.py`, 6/6 over HTTP): answer-now → real BTC price via the
pre-built `pricebot`; a standing request → an AP flow; the **same** request again → **reuses** it
(dedup); "book me a flight" → **declines**. The concierge never creates an agent.

**Dry-run** (`/api/concierge?dry_run=1`) still returns the deterministic plan with no side effects.
