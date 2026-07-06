# events layer — TODO

## Production hardening (before any real deployment)
- [ ] **Turn on persistent stores.** Set `EVENTS_DB=<path or Postgres>` so **agents +
      subscriptions survive restarts and are shared across CUGA replicas** (the fleet model).
      Default `:memory:` is dev-only — a pod restart loses agents, and a second replica won't
      know a user's agent when an AP callback lands on it.
- [ ] **Persistent conversation memory.** Wire `runtime.make_sqlite_checkpointer(<path>)` (or a
      Postgres checkpointer in prod) into the runtime so memory survives restarts / is shared
      across replicas. (In-process `MemorySaver` is the default until then.)
- [ ] **Run Activepieces CE on Postgres**, NOT the single-container sqlite/pglite build — that
      one silently wipes its own project mid-run (observed). Postgres + a volume = durable.
- [ ] Wire `current_user.sub` → `Principal` in the `main.py` mount (currently header/env-based).

## Credentials / integrations (see the design discussion)
- [ ] **Per-integration credential ownership flag: `shared` (service account) vs `per-user`.**
      - `shared` → the builder connects it once; stored as a CUGA secret (`vault://`); all users share.
      - `per-user` → each user authorizes their own via OAuth → AP connection `ea::<tenant>::<user>::<app>`.
- [ ] **Just-in-time connect** for `per-user`: when a chat needs an app the user hasn't
      connected, the concierge returns "connect your <app>" + the AP connect URL, then proceeds.
- [ ] Runtime resolution of the connection `externalId` by ownership model + principal.

## Backends & scale
- [x] **`cuga` backend is the DEFAULT worker backend** (`EVENTS_WORKER_BACKEND=cuga`; concierge
      stays react). `_cuga_bridge.build_worker_graph` (per-agent `DynamicAgentGraph`, faithful to
      `main.py:814`) + `run_graph` (drives CUGA's real `AgentLoop` — the `/stream` engine — to a
      final answer). `CugaRuntime` delegates storage/isolation to the react `AgentStore`; falls
      back to react only when the CUGA stack is absent. **LIVE-VERIFIED** (`uv sync` + full
      `.venv`): `tests/events/live_cuga_worker_check.py` provisions a `backend=cuga` worker → a
      `DynamicAgentGraph` builds on demand → runs via `AgentLoop` → returns an answer
      (`executed via: CUGA DynamicAgentGraph`, not the fallback).
- [x] **Attach a worker's MCP servers to its CUGA graph** — DONE. `build_worker_graph` passes
      `app_names=[hyphen→underscore(spec.mcp_servers)]` into `CombinedToolProvider`; the CUGA
      **registry** serves the servers from `config/mcp_servers_cuga_apps.yaml` (all 7
      event-agent-ap servers). **LIVE-VERIFIED**: registry loads real tools
      (`cuga_finance: [get_crypto_price, get_stock_quote]`, geo/code/local/text) and
      `GET /applications` lists all 7; a `backend=cuga` worker loads + calls them
      (`live_cuga_worker_mcp_check.py`). **Fixed a real bug found live**: hyphenated app names
      (`cuga-finance_get_crypto_price`) → `NameError` in CUGA's code agent (parsed as subtraction);
      registry keys are now underscore names, mapped in `_cuga_bridge`. Needs the registry running
      (see [MCP_SETUP.md](MCP_SETUP.md)).
- [x] **No silent react fallback** — `EVENTS_WORKER_BACKEND=cuga` executes on CUGA; if the stack
      is absent or a build/run fails it **raises** (loud), unless `EVENTS_CUGA_FALLBACK_REACT=1`
      is explicitly set. CUGA does the reasoning, period.
- [x] **Runtime = ROUTER over pre-built agents** (decision 0005) — concierge rewritten
      (`list_capabilities`/`answer_now`/`find_or_create_flow`; `provision_agent` removed; declines
      when nothing fits). Agents seeded via `seed.py` (`EVENTS_SEED_AGENTS=1`). Flow **dedup**
      (`(agent,source,cadence,sink,owner)`; grain follows creds). **LIVE e2e 6/6**.
- [x] **Per-user connect: CUGA hosts OAuth, AP holds token** (decision 0006) — `oauth.py` +
      `/api/events/connect/*` + `AgentSpec.integrations`. Token path **live vs real AP**
      (Telegram). OAuth path built; degrades cleanly.
- [x] **Identity, profiles & permissions** (decision 0007) — local `UserStore` (admin/alice/bob),
      `IdentityMap` (`(tenant,channel,native_id)→user` + profile-issued link tokens),
      `principal.resolve_channel`, per-agent `access` + router filter/deny, tenant-shared agents
      (`agent_scope`) vs per-user run-state. Endpoints `/api/events/{me,link/*,admin/users}` +
      `/invoke` channel resolution + `/start` link handshake. Studio **Profile** + **Admin** tabs.
      **LIVE e2e 8/8** (`live_identity_check.py`); offline `test_events_dimensions` 18/18.
- [~] **Channel inbound flows via AP + Box watcher (Stage 2)** — CODE DONE (credential-independent):
      declarative `flows.CHANNELS` descriptors (Telegram/Discord/Slack, no channel code in CUGA);
      generalized `build_inbound_flow` + `build_resume_watcher_flow` (Box·new_file → resume_judge →
      Router MATCH→gmail); `ap_engine.create_inbound_flow` + `create_push_flow` (live arming);
      concierge `find_or_create_flow` handles PUSH; admin `POST /api/events/admin/channels/{ch}/arm`.
      Offline 19/19. **LIVE PENDING your creds+tunnel** (see [CHANNELS_SETUP.md](CHANNELS_SETUP.md)):
      run `tests/events/live_stage2_channels.py` → arms flows + Box watcher; a human sends the real
      inbound message for the round-trip. ⚠️ VERIFY the AP piece trigger/action op shapes on first arm.
- [ ] **OAuth provider config from AP metadata** — the connect UX stays **CUGA-hosted for every
      connector** (no hopping to AP's UI; CUGA passes the credential to AP). The only cleanup:
      source the OAuth auth/token URLs from **AP piece metadata** so `oauth.py` hardcodes no
      provider specifics (the `PROVIDERS` table → fallback). Noted in oauth.py.
- [ ] **Builder connector-config UI** — enable channels/integrations + ownership per agent from
      the Studio (today: seeded config + read-only tabs + a user Connect/Link action).
- [ ] **Real Gmail/Box OAuth** — register the platform OAuth app
      (`EVENTS_OAUTH_<APP>_CLIENT_ID/_SECRET`) + VERIFY AP's OAUTH2 connection schema in
      `ensure_oauth_connection`. Then per-user Gmail/Box login works end to end.
- [ ] **Worker-side per-user token** — a CUGA worker reading *your* Gmail via MCP should use your
      per-user connection (Phase 3).
- [ ] `config_store` / `secrets_store` to accept `tenant_id`/`instance_id` **per-call** (global
      today) → first-class CUGA-native tenant isolation for the cuga backend.
- [ ] AP **project = tenant** requires **AP Enterprise** (CE = 1 project). Multi-tenant SaaS
      hosting one shared AP → budget for the enterprise license; else `grain=shared`.

## Infra / ops
- [x] **AP persistent volume — DONE via `scripts/ap_up.sh`.** The script runs AP against an external
      **postgres** (`-v ap_pgdata:/var/lib/postgresql/data`) + **redis** (`-v ap_redis:/data`) on a
      shared `ap-net`, with stable secrets in `.ap.env` (so the volume's encrypted connections keep
      decrypting across recreates). The `activepieces:latest` image is NOT all-in-one; it requires
      external pg+redis. (Note: do NOT set `AP_WORKER_TOKEN` — see SETUP.md; the entrypoint mints a
      valid JWT and `ap_up.sh` health-gates the worker.)
- [ ] Tunnel is a Cloudflare **quick-tunnel** (ephemeral URL, dies on `cloudflared` restart) —
      re-point bot webhooks after any restart. Fine for testing; use a named tunnel for anything durable.

## Integrations
- [x] **Box — direct poll backend (free-account friendly)** — `box_direct.py` +
      `POST /api/events/box/poll` list a folder with `BOX_DEV_TOKEN` (Box REST API) and fire the
      watcher per new file; no OAuth app, no paid account. **Live e2e verified 2026-07-06**
      (`live_box_direct_check.py`). The AP OAuth path (needs a Business account) remains as a
      secondary option. **Open follow-up:** the watcher passes the file *name* to `resume_judge`, not
      its *content* (agent can't read the bytes yet — needs a Box download in `box_direct` or a
      Box-read MCP tool).

## Phase 3+ (from DESIGN §11)
- [~] PUSH triggers (Box / GitHub / Gmail) + branching flows — **Box direct poll e2e verified**
      (2026-07-06); AP `create_push_flow` / `build_resume_watcher_flow` / router branches wired;
      GitHub/Gmail AP PUSH live e2e not yet proven.
- [x] Real channel delivery (Telegram / Discord / Slack) + inbound — **live round-trip verified:
      Telegram (AP), Discord (AP), Slack (direct, default)** (2026-07-06). Slack/Box direct backends
      bypass AP; `delivery.channel_backend` picks direct-vs-AP per channel and **direct-channel
      delivery** lets a scheduled flow deliver to a direct channel with no AP send-step.
- [x] **Studio UI: Concierge · Channels · Integrations · Flows · Examples** — built into CUGA's
      existing React frontend (dumb; reads `GET /api/events/*`, posts to `/api/concierge`). See
      [STUDIO_UI.md](STUDIO_UI.md). Now also an **Agents** tab (`GET /api/events/agents`). Connect
      is **CUGA-hosted** (`/api/events/connect/*`, no hop to AP's UI): token path live vs real AP,
      OAuth built + degrades. Remaining UI: a Runs/history pane + the builder connector-config UI.
- [ ] `e2e_live` tier with Box/Gmail/GitHub/Telegram/Discord creds.
