# Phase 1 & 2 — sequence diagrams + testing (what's been tackled)

Mermaid sequence diagrams (with **[PNG renders in `png/`](png/)**) for everything built + verified
in Phase 1 (the agent seam, NOW) and Phase 2 (timer watchers via Activepieces), plus the
isolation/statefulness/credentials rewires and the Studio UI. Grounded in the actual code under
`src/cuga/backend/events/`.

> **➡️ Start here to test:** [TESTING_WALKTHROUGH.md](TESTING_WALKTHROUGH.md) — a narrated
> "backend first, then UI" guide: run this → what you'll see → why → which diagram. It's the
> answer to "I want to know exactly what is going on as I test."

**PNGs:** each `.md` diagram is rendered to `png/<name>-1.png` (diagram 03 has `-1` arm + `-2`
fire). Open them for a visual read of the sequence of operations.

## Index
| # | Diagram | What it shows | Status |
|---|---|---|---|
| [01](01_now_worker_invoke.md) | **NOW worker via `/invoke`** | the AP seam → `CugaRuntime.run` → **CUGA** worker → deliver | ✅ live |
| [02](02_concierge_reuse_or_create.md) | **Concierge = router** | `/api/concierge` → route over PRE-BUILT agents: answer-now / reuse-or-create-flow / decline | ✅ live |
| [08](08_connect_oauth.md) | **Per-user connect (OAuth)** | builder enables · user logs in · CUGA hosts OAuth, AP holds token | ✅ token live; OAuth built |
| [09](09_identity_permissions.md) | **Identity, profiles & permissions** | channel native_id → principal via linking; per-agent access; tenant-shared agents, per-user run-state | ✅ live 8/8 |
| [03](03_cron_poll_watcher.md) | **CRON/POLL watcher** | arm an AP schedule flow, then AP fires → `/invoke` → worker → deliver | ✅ live |
| [04](04_isolation_scope.md) | **Isolation (scope)** | Principal → scope threaded through agents/subs/AP project + flow names | ✅ live |
| [05](05_statefulness_fleet.md) | **Statefulness (fleet)** | shared `AgentStore` + checkpointer → any replica handles any fire | ✅ live |
| [06](06_credentials.md) | **Credentials** | shared (service acct) vs per-user AP connection resolution | ✅ live |
| [07](07_studio_ui.md) | **Studio UI (dumb)** | the CUGA React tabs → read endpoints; concierge chat POST | ✅ built |

## Which engine runs where (the two planes)
- **Concierge** (control plane — NL → flow) = a lightweight **LangGraph** react agent
  (`create_react_agent`, `concierge.py:169`) bound to host meta-tools. Fast tool-caller; stays react.
- **Workers** (data plane — *doing the hard work of answering*) = **CUGA** by default
  (`CugaRuntime`, `runtime.py`): a per-agent `DynamicAgentGraph` so workers get CUGA's
  policies/knowledge/supervisor/tools. Storage + isolation delegate to the shared react
  `AgentStore`; if the full CUGA stack isn't present, execution **falls back to react**
  (`create_react_agent`, `runtime.py:126`). Set `EVENTS_WORKER_BACKEND=react` to force react.

## Legend
```
Caller      a browser (web chat), curl, or Activepieces' HTTP step
/invoke     the one seam every trigger calls back through (X-Gateway-Token)
/api/concierge   NL → decide (reuse/create + arm); ?dry_run=1 = plan only
Runtime     CugaRuntime (per-agent CUGA graph) by default; ReactRuntime opt-in (EVENTS_WORKER_BACKEND)
Store       SubscriptionStore (sqlite index) + AgentStore (shared, fleet)
AP          Activepieces — owns triggers, connections, delivery
scope       tenant/instance/user string from Principal (isolation key)
```
Cross-cutting in every diagram: a single **`trace_id`** stamped at each seam (DESIGN §12).
</content>
