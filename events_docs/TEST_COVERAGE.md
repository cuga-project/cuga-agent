# Phase 1 & 2 — test coverage matrix

Two tiers: **offline** (stdlib `python3`, no network — fast, runs anywhere) and **live**
(needs the CUGA venv / watsonx / AP / the registry). Every Phase-1/2 dimension has coverage in
at least one tier. Run offline on every change; run live before a release/demo.

## Dimensions × tests
| Dimension | Offline | Live |
|---|---|---|
| **Envelope / MCP catalog / trace / classifier / planner** | `test_events_core.py` (14) | — |
| **Flow builders** (cron/poll/push/runonce/router, per-mode dispatch) | `test_events_core.py` + `test_events_dimensions.py::test_flow_builders_per_mode` | `live_phase2_watchers.py` (real AP flow) |
| **Subscription index** (upsert/list/scope filter) | `test_events_core.py::test_subscription_store` | `live_phase2_watchers.py` |
| **Connectors** (channels/integrations status) | `test_events_dimensions.py` (2) | Studio `/api/events/*` |
| **Catalog** (examples/outcomes) | `test_events_dimensions.py::test_catalog_examples` | Studio Examples tab |
| **Credentials** (shared vs per-user) | `test_events_dimensions.py::test_credentials_*` | `live_credentials_check.py` |
| **Router** (answer-now / reuse-or-create-flow / decline; over PRE-BUILT agents) | (grain: `test_owner_scope_*`) | `live_server_e2e_check.py` (6/6 over HTTP) |
| **Seed** (pre-built agents carry channels/integrations) | `test_events_dimensions.py::test_seed_agents_*` | server `EVENTS_SEED_AGENTS=1` |
| **Flow dedup** (reuse by identity) | `test_events_dimensions.py::test_flow_dedup_*` | e2e "reuse" check |
| **Per-user connect** (OAuth registry + endpoints) | `test_events_dimensions.py::test_oauth_registry_*` | token path live vs real AP (`/connect/telegram/token`) |
| **Isolation** (scope, AP project grains, per-scope agents) | `test_events_dimensions.py` (principal + cuga storage isolation) | `live_isolation_check.py` |
| **Statefulness** (fleet: shared store + checkpointer) | — | `live_statefulness_check.py` |
| **Runtime selection + fallback gating** (cuga default, no silent react) | `test_events_dimensions.py` (3) | — |
| **cuga worker execution** (DynamicAgentGraph + AgentLoop) | — | `live_cuga_worker_check.py` |
| **cuga worker + MCP tools** (registry → tool call) | — | `live_cuga_worker_mcp_check.py` (+ registry) |
| **react worker** (NOW + per-thread memory) | — | `live_react_check.py` |
| **Concierge** (reuse-or-create, NOW) | `test_events_core.py` (planner/oracle) | `live_concierge_check.py` |
| **AP watcher e2e** (CRON → /invoke → deliver) | — | `live_phase2_watchers.py` |
| **Studio read endpoints** (status/channels/integrations/examples/subscriptions) | `test_events_studio_api.py`¹ | `curl` on the running server |
| **MCP registry** (cuga-apps config serves 7 servers) | — | `GET /applications` (see MCP_SETUP.md) |
| **Server-level e2e** (whole stack over HTTP: concierge → cuga worker → MCP → answer) | — | `live_server_e2e_check.py` |
| **Users** (local store, roles, auth, tenant-isolated) | `test_events_dimensions::test_user_store` | seeded (admin/alice/bob) |
| **Identity map** (channel native_id → user + link tokens) | `test_events_dimensions::test_identity_map_*` | `live_identity_check.py` (link handshake) |
| **Per-agent permissions** (access filter/deny) | `test_events_dimensions::test_perms_*` | `live_identity_check.py` (alice can't see market_briefer) |
| **Tenant-shared agents vs per-user run-state** | `test_events_dimensions::test_principal_agent_scope_*` | 2-user isolation e2e |

¹ `test_events_studio_api.py` needs a venv with fastapi (`.venv`/`.venv-events`), not plain python3.

## Run it
```bash
# PREFLIGHT — does every integration actually work, from .env alone? (run first)
python3 tests/events/preflight.py                   # watsonx·AP·Telegram·Discord·Slack·Box·MCP

# OFFLINE — run on every change (all in one: `uv run pytest tests/events/ -q` → 46 passed)
python3 tests/events/test_events_core.py            # 14
uv run pytest tests/events/test_events_dimensions.py -q    # 23 (delivery-backend + direct-delivery incl.)
uv run pytest tests/events/test_events_studio_api.py -q    # 9 (Studio endpoints + slack/box direct + poll)

# LIVE (needs the CUGA venv + creds; AP on :8081; registry for MCP)
.venv/bin/python tests/events/live_cuga_worker_check.py          # cuga executes
.venv/bin/python tests/events/live_cuga_worker_mcp_check.py      # cuga + MCP tools (registry up)
.venv-events/bin/python tests/events/live_react_check.py         # react NOW+memory
.venv-events/bin/python tests/events/live_concierge_check.py     # reuse-or-create
.venv-events/bin/python tests/events/live_phase2_watchers.py     # AP CRON e2e (~3 min)
.venv-events/bin/python tests/events/live_isolation_check.py     # isolation
.venv-events/bin/python tests/events/live_statefulness_check.py  # fleet statefulness
.venv-events/bin/python tests/events/live_credentials_check.py   # shared vs per-user
.venv-events/bin/python tests/events/ap_nuke.py --dry            # inspect/clean AP
```

## Status (verified 2026-07-02)
- **Offline:** `test_events_core` 14/14, `test_events_dimensions` **18/18**, `test_events_studio_api` 5/5.
- **Identity e2e (`live_identity_check.py`) 8/8:** alice(user) vs admin profiles; **per-agent
  permissions** (alice can't see restricted `market_briefer`, admin can); **channel linking**
  (`/start <token>` binds `telegram:12345 → alice`, shown in `/me`).
- **Live:** cuga worker executes; cuga+MCP → `A: 61397`; **router e2e 6/6**
  (`live_server_e2e_check` → answer-now `$61,444` → flow create → **reuse (dedup)** → **decline**);
  **per-user token connect vs real AP** (`/connect/telegram/token` → `ea::default::local::telegram`,
  listed by `/connections`); AP-watcher/isolation/statefulness/credentials all PASS.
- Real bugs the live tests caught + fixed: `/api/concierge` handler name-shadowing
  (`'function' object has no attribute 'run'`); hyphen-in-MCP-tool-name `NameError` (CUGA composes
  `<app>_<tool>` as a Python id → underscore app names).

## Gaps / not yet
- **Channel inbound + delivery — live round-trip verified: Telegram (AP), Discord (AP), Slack
  (direct, default)** (2026-07-06). Slack/Box default to a *direct* backend (no AP); direct-channel
  delivery closes scheduled-flow delivery to a direct channel (no AP send-step). Remaining: an
  instant *direct* Discord (gateway bot) is not built (Discord stays AP polling for now).
- **PUSH triggers** (Box/GitHub/Gmail) — Box **direct poll e2e verified**; `create_push_flow` + the
  Box resume watcher are wired;
  live PUSH e2e **not yet proven** end-to-end.
- **cuga worker inside the full server** via `/api/concierge` (end-to-end through HTTP) — the
  pieces are verified in isolation; a single scripted server-level e2e is a nice-to-add.

See [TESTING_WALKTHROUGH.md](phase_1_2/TESTING_WALKTHROUGH.md) for the narrated version.
