# Decisions — source of truth

Architecture decisions for the event-driven concierge layer on CUGA. Read these as the
canonical answers to "how does isolation / AP / credentials / the endpoints work." Each record
is Context → Decision → Consequences. Verified live (2026-07-02) unless noted.

| # | Decision | TL;DR |
|---|---|---|
| [0001](0001-ap-as-the-event-engine.md) | **AP as the one event engine + project = tenant** | AP owns triggers/connections/delivery; the AP **project** is the hard isolation boundary = **tenant**; grain configurable; CE=1 project → auto-degrade to shared; multi-project = enterprise license. |
| [0002](0002-tenancy-and-isolation.md) | **Tenancy & isolation model** | `Principal(tenant/instance/user)`; CUGA isolates per-**agent** (+ conversations per user); the events layer scopes agents/subs/threads/AP by principal; stateless replicas via shared stores. |
| [0003](0003-credentials-ownership.md) | **Credential ownership: shared vs per-user** | Each integration declares `shared` (service acct, builder-set) or `per-user` (each user connects their own); creds live in CUGA secrets and/or AP connections; just-in-time connect. |
| [0004](0004-events-endpoints.md) | **The new endpoints** | `POST /invoke` (seam), `POST /api/concierge` (NL→flow, `?dry_run`), `GET /api/events/subscriptions`; the normalized envelope; how `scope` flows; all behind `EVENTS_ENABLED`. |

**Companion docs:** [../DESIGN.md](../DESIGN.md) (the goal) · [../ISOLATION.md](../ISOLATION.md) ·
[../HOW_TO_TEST.md](../HOW_TO_TEST.md) · [../TODO.md](../TODO.md).

## The one-paragraph model
One **shared AP** engine for a fleet of **stateless CUGA FastAPI replicas**. The AP **project**
is the hard tenant boundary (needs enterprise AP for >1 project; CE self-host = 1 tenant = fine —
e.g. a bank running its own CUGA + AP CE). Inside a tenant, **users** are namespaced (own flows,
own connections). A `Principal(tenant/instance/user)` threads through everything as `scope`;
agents + memory live in shared storage so any replica serves any user's `/invoke` callback.
Credentials are **shared (service)** or **per-user** per integration, held by CUGA secrets / AP
connections. Nothing crosses tenants because the concierge's reuse/list run inside the tenant's
project (or filter by scope).
