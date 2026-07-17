# 0004 — The new endpoints & how scope flows

> **Amended by [0009](0009-single-agent-supervisor.md) (2026-07-15).** The concierge no longer
> *creates a worker* — it compiles NL → a **flow** that targets the single `cuga` agent. Read
> "worker" below as "flow (targeting `cuga`)"; the scope mechanics are unchanged.

## Context
The events layer adds a small, additive HTTP surface to CUGA's FastAPI server, mounted only when
`EVENTS_ENABLED` is set (off → CUGA byte-for-byte unchanged). Everything reuses CUGA's agent CRUD,
secrets, and runtime where possible.

## The endpoints
| Method · path | Purpose | Auth | Isolation |
|---|---|---|---|
| `POST /invoke` | the **seam** every AP trigger calls back through; runs the target agent, optionally delivers | `X-Gateway-Token` (machine) | `scope` from the body (set when the flow was armed) |
| `POST /api/concierge` | **NL → decide** (reuse/create a flow targeting `cuga` + arm trigger); `?dry_run=1` = reason→build, no side effects | **⚠ none at this layer** — see note | `Principal` from `X-Tenant-Id`/`X-User-Id` headers |
| `GET /api/events/subscriptions` | list armed triggers for the caller | read | filtered by the caller's scope |

> **⚠ Auth gap (as shipped).** The events-layer `POST /api/concierge` handler
> ([`app.py`](../../src/cuga/backend/events/app.py) `api_concierge`) has **no** `require_chat_access`
> dependency — it only resolves a `Principal` from headers. Routes are attached to the main app via
> `register_events_routes(app, …)`, not a sub-app mount, so CUGA's native gate does not carry over.
> Since this endpoint arms flows and runs the agent, that is a **security decision to make**, not just
> a doc note: either front it (gateway/network) or add the dependency. Tracked in
> [GAPS.md](../GAPS.md).

## The normalized `/invoke` envelope
```json
{ "source": {"type":"channel|integration|time","name":"telegram|gmail|cron|…","thread_id":"…"},
  "event":  {"kind":"message|new_email|new_pr|tick|runonce","payload":{…}},
  "text":   "<utterance if a channel>",
  "agent":  "<target agent_id>",
  "deliver": true,
  "scope":  "<tenant/instance/user>  ('' = unset → resolve from headers)" }
```

## How `scope` flows (isolation end-to-end)
1. A user hits `POST /api/concierge` → `Principal` resolved from headers (or `current_user.sub`).
2. The concierge creates/reuses the worker **at that scope** and arms an AP flow; the flow's
   `/invoke` HTTP step **embeds `scope`** in its body (and lands in the tenant's project).
3. When AP fires, it POSTs to `/invoke` with `scope` → the handler runs the worker **in that
   scope** (its own agent + memory), then delivers.
4. Direct `/invoke` calls without a body `scope` resolve from `X-Tenant-Id`/`X-User-Id`; fully
   unset → `DEFAULT_SCOPE = "default/default/local"`.

## What's reused vs new
- **Reused:** CUGA agent CRUD (`config_store`/`/api/manage`), secrets, the `/stream` runtime.
- **New:** `/invoke`, `/api/concierge`, `/api/events/subscriptions`, the `AgentRuntime` port
  (react + cuga backends), the AP engine client, the subscription index.

## Consequences
- `dry_run` makes NL→Flow inspectable with zero side effects (great for tests + the eval harness).
- One seam (`/invoke`) means AP shards by project and CUGA shards by replica independently.
- The endpoints are stateless w.r.t. agents/memory once the shared stores (0002) are on.
