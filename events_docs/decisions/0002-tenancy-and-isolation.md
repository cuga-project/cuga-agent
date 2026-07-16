# 0002 — Tenancy & isolation model

## Context
"User isolation" in CUGA is often assumed to be per-user. It isn't, exactly — and the events
layer must add its own scoping without fighting CUGA's model.

## What CUGA actually does (verified in code)
- `tenant_id` + `instance_id` — on every storage table, but **process-global constants**
  (`DYNACONF_SERVICE__TENANT_ID/__INSTANCE_ID`), i.e. deployment-level, not per-request.
- **Agents & secrets are keyed by `agent_id`** (`agent_configs`, `secrets`) — **per-agent**, tenant-global.
  Two users of the same agent share its config + secrets.
- **Conversations are keyed by `user_id`** (the auth `sub`) with an access guard — the one place CUGA isolates per-user.
- So CUGA = **per-agent isolation + per-user conversations**, single logical tenant per deployment.

## Decision
- Introduce a **`Principal = (tenant_id, instance_id, user_id)`** → a `scope` string, threaded
  through the whole events layer. In CUGA, `user_id = current_user.sub`; `tenant_id` from a
  claim/deployment.
- **Scope everything** in the events layer by `scope`:
  - **Agents** (`AgentRuntime`) — keyed `(scope, name)`; a different scope sees nothing.
  - **Subscriptions** — `tenant` column; `/api/events/subscriptions` returns only your scope.
  - **Threads / memory** — `thread_id` namespaced `scope::thread`.
  - **AP** — project by grain (0001) + full-scope-prefixed flow/connection names.
- **Two levels of isolation:** **hard at tenant** (AP project + scoped queries), **soft per-user**
  inside (naming) — acceptable within one org where only the concierge authors flows.
- **Canonical default scope** = `DEFAULT_SCOPE = "default/default/local"` (== `Principal().scope`);
  the envelope's `scope=""` means "unset → resolve from headers", so an unset request and an unset
  agent land in the same namespace. (This bug — dual defaults `"default"` vs `"default/default/local"`
  — was caught and fixed.)

## Statefulness (the replica model)
For **one shared AP + many stateless CUGA replicas**, an AP callback can land on any replica, so:
- **Agents → shared definition** — *(amended by [0009](0009-single-agent-supervisor.md))*: sub-agents
  live in `supervisor_agents.yaml` (shipped with the code, identical on every replica); the
  `AgentStore` remains only for the `react` dev runtime. Any replica rebuilds the supervisor on
  demand from the same file.
- **Memory → persistent checkpointer** — `runtime.make_sqlite_checkpointer` (AsyncSqliteSaver;
  Postgres in prod) instead of in-process `MemorySaver`.
- Verified: a fresh runtime on the same stores sees the agent **and** continues the memory, while a
  different scope still sees nothing.

## Consequences
- Per-user isolation is real (agents/subs/threads/connections), enforced hard at the tenant and
  soft (naming) per-user.
- CUGA-native per-tenant storage for the `cuga` backend still needs `config_store`/`secrets_store`
  to accept `tenant_id` **per-call** (TODO) — they read globals today.
- Wire `current_user.sub → Principal` in the mount (TODO; header/env-based today).
