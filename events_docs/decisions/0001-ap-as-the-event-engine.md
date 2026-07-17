# 0001 — Activepieces as the one event engine; AP project = tenant

## Context
The events layer needs to fire triggers (cron/webhook/poll), hold app credentials (OAuth/secret),
and deliver results. CUGA has none of this. Activepieces (AP) does — and AP's **project** is the
only *hard* isolation lever below the platform: flows **and** connections are project-scoped;
anything finer (flow names, connection externalIds) is soft, code-enforced naming.

We serve possibly many **tenants**, each with many **agents**, each chatted with by many **users**.
Which CUGA dimension should an AP project represent?

## Decision
- **AP is the single event engine.** Every automation is an AP flow: `trigger → POST /invoke → deliver`.
  *(Amended by [0008](0008-direct-backends-for-channels.md): the Slack/Discord/Box channel + Box-poll
  paths run **direct** in CUGA, not as AP flows. AP remains the one engine for integration triggers
  and the clock.)*
- **AP project = tenant** — the trust boundary. Different tenants are mutually untrusted; the
  project is what AP *enforces*. The concierge's cross-cutting ops (**reuse-before-create**,
  list) run **inside the tenant's project**, so they can never see or reuse another tenant's flows.
- **Grain is configurable** via `EVENTS_AP_PROJECT_GRAIN`:
  `tenant` (default) · `user` (strict, per-user project) · `shared` (one project, naming only).
- **Flow names + connection externalIds are ALWAYS full-scope-prefixed**
  (`<tenant>::<user>::…`, `ea::<tenant>::<user>::<app>`), so **per-user** isolation holds
  regardless of the project grain.
- **Auto-degrade:** if the AP plan caps projects, the engine falls back to `shared` behavior
  (logged) — never breaks, just softens.

## The licensing reality (verified)
Activepieces **Community Edition** (self-hosted, incl. Code Engine) = **one project**.
**Multiple projects is an Enterprise/commercial-licensed feature.** So:
- **Client self-hosts, single tenant** (e.g. a bank runs its own CUGA + AP CE) → one project → **CE is enough.** ✅
- **You host one shared AP across many client-tenants** → needs **AP Enterprise** (many projects).

Sources: activepieces.com/pricing · activepieces.com/docs/about/license.

## Consequences
- Single-tenant self-host is the CE sweet spot; multi-tenant SaaS budgets for enterprise AP (or runs `shared`).
- The reuse-check is tenant-safe by construction (scoped query), not by "remember to filter."
- `shared` mode is safe for **trusted multi-user within one org** (only the concierge authors flows).
- Run AP CE on **Postgres** (the single-container sqlite build wipes its own project — observed).
