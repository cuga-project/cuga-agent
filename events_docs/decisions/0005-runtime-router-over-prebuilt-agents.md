# 0005 — Runtime concierge = a ROUTER over pre-built agents (not an agent factory)

> **PARTIALLY SUPERSEDED by [0009](0009-single-agent-supervisor.md) (2026-07-15).** What survives:
> the concierge never creates agents, and design-time/run-time stay split by persona. What changed:
> the concierge no longer ROUTES either — routing moved into the one `cuga` supervisor
> (per wake-up), and sub-agents are defined in `supervisor_agents.yaml`, not via the builder API.

## Context
The earlier design had the concierge **reuse-or-create a worker** at runtime — inventing agents
and choosing their MCP tools on the fly. On review that conflates two personas and two phases.

## Decision
Split cleanly by persona/phase:

- **Design time — builder.** A builder creates each **agent** (skill/prompt + MCP tools +
  policies) and binds the **channels + integrations** it may use (with credential ownership).
  This is CUGA's existing manage UI + a per-agent connector binding (`AgentSpec.channels`,
  `AgentSpec.integrations`).
- **Run time — end user on a channel.** The concierge is a **router**, never an agent factory:
  ```
  utterance → understand
    • an existing agent can answer NOW        → answer_now(agent) → reply
    • a standing request (cron/poll/push)     → find_or_create_flow(agent, …)
                                                 (REUSE a matching flow, else CREATE)
    • nothing listed fits                     → DECLINE ("ask a builder")
  ```

**Meta-tools** (host-bound): `list_capabilities` (pre-built agents + their connectors) ·
`answer_now(agent, task)` · `find_or_create_flow(agent, kind, …)`. `provision_agent` is **removed**.

**Flow dedup / grain (see [0006](0006-auth-connection-model.md)):** a flow is identified by
`(agent, source, cadence, sink, owner-scope)`, where owner-scope is the **tenant** for
all-shared-connector flows and the **full user scope** when any connector is per-user — so the
grain follows the credentials. Matching key → reuse; else create.

**No match → decline.** The concierge never invents a capability; it tells the user to ask a
builder. (Chosen over "auto-create" and "suggest closest".)

## Consequences
- Agents are durable, governed artifacts (a builder owns them); the runtime is deterministic
  about what exists.
- Until the builder connector-UI lands, a demo fleet is **seeded** (`seed.py`,
  `EVENTS_SEED_AGENTS=1`).
- **Verified live** (`live_server_e2e_check.py`, 6/6 over HTTP): the router does answer-now
  (real price via a pre-built agent), create-flow, **reuse-flow (dedup)**, and **decline**.
- Supersedes DESIGN §5's "reuse-or-create a worker" phrasing.
