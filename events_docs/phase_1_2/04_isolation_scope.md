# 04 — Isolation: how `scope` threads end to end

Every request carries a **`Principal = (tenant_id, instance_id, user_id)`** →
[`principal.py`](../../src/cuga/backend/events/principal.py) → a `scope` string. That scope keys
agents, subscriptions, memory threads, and AP flow/connection names, so two tenants (or two users)
never see or touch each other's automations. Decision: [0002](../decisions/0002-tenancy-and-isolation.md).

```mermaid
sequenceDiagram
    autonumber
    participant A as User A<br/>(X-Tenant-Id: acme, X-User-Id: alice)
    participant B as User B<br/>(X-Tenant-Id: acme, X-User-Id: bob)
    participant CE as /api/concierge
    participant RT as CugaRuntime / AgentStore
    participant AP as Activepieces
    participant DB as SubscriptionStore

    A->>CE: "watch BTC" (headers → scope = acme/·/alice)
    CE->>RT: upsert_agent(spec, scope="acme/·/alice")
    CE->>AP: create flow · project = ea::acme · name = "acme/·/alice::pricebot"
    CE->>DB: upsert(Subscription{tenant="acme/·/alice"})

    B->>CE: "watch ETH" (headers → scope = acme/·/bob)
    CE->>RT: upsert_agent(spec, scope="acme/·/bob")  %% distinct namespace
    CE->>AP: flow name = "acme/·/bob::ethbot" · connection ea::acme::bob::…

    Note over RT,DB: reads are scope-filtered:<br/>list_agents(scope) · subscriptions.list(scope) · by_agent(agent,scope)
    B->>CE: GET /api/events/subscriptions
    CE->>DB: as_dicts(scope="acme/·/bob")
    DB-->>B: only bob's flows (never alice's)
```

**Two levels:** **hard at the tenant** (AP project per tenant when the plan allows, else
scope-prefixed flow names in the shared project) + **soft per-user** inside (naming +
scope-filtered queries). `EVENTS_AP_PROJECT_GRAIN=tenant|user|shared`; auto-degrades to shared if
the AP plan caps projects. Unset everything → canonical `DEFAULT_SCOPE = "default/default/local"`.

**Verified by:** `live_isolation_check.py` (alice/bob flows scope-isolated; cross-tenant read → `[]`).
