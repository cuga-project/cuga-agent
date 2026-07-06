# 05 — Statefulness: one shared AP, many stateless CUGA replicas

An AP callback can land on **any** CUGA replica. So nothing agent-related may live only in one
process: agents go to a shared **`AgentStore`** ([`agent_store.py`](../../src/cuga/backend/events/agent_store.py),
`EVENTS_DB`) and memory to a persistent **checkpointer**
([`runtime.py:205`](../../src/cuga/backend/events/runtime.py) `make_sqlite_checkpointer` →
`AsyncSqliteSaver`; Postgres in prod). Any replica can rebuild any `(scope, agent)` on demand.

```mermaid
sequenceDiagram
    autonumber
    participant U as User
    participant R1 as CUGA replica #1
    participant R2 as CUGA replica #2
    participant AS as AgentStore<br/>(shared sqlite/Postgres)
    participant CK as Checkpointer<br/>(shared, per thread_id)
    participant AP as Activepieces

    U->>R1: /api/concierge — provision "papers" + arm CRON
    R1->>AS: upsert(scope, spec)      %% agent now shared
    R1->>AP: create schedule flow (→ /invoke)
    Note over R1,CK: worker memory written under thread_id

    AP-->>R2: minutes later, tick lands on replica #2
    R2->>AS: get_agent(scope, "papers")   %% not in R2's process — read shared
    AS-->>R2: spec
    R2->>R2: rebuild LangGraph graph on demand
    R2->>CK: load thread state (continues the same memory)
    R2-->>AP: {ok, answer}   %% same result as if R1 had handled it
```

**Net:** the CUGA side is **stateless** — scale replicas horizontally behind one AP. With
`EVENTS_DB=:memory:` (dev) it's single-process; point it at a file/Postgres for the fleet.

**Verified by:** `live_statefulness_check.py` — a **fresh** runtime on the same stores sees the
agent *and* continues its memory, while a different scope still sees nothing (isolation holds
across the restart).
