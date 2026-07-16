# Supervisor refactor — the plan of record (agreed with Anu, 2026-07-15)

## The decision (verbatim intent)

Retire the fleet-routing model entirely. ONE addressable agent, always: **"cuga"**.

- `EVENTS_SUPERVISOR=1` (set): cuga = a **supervisor** whose sub-agents are the 27 registered
  specs (roster read from the AgentStore). The SUPERVISOR does all routing/picking, per wake-up.
  Response bubbles up. Sub-agents are NOT independently addressable and NOT user-visible.
- Unset: cuga = the plain classic agent **as seen in main** (cuga-default; no supervisor,
  no fleet). Events plumbing still delivers everything to this one agent.
- DO NOT keep today's arm-time-binding/fleet mode as a third option. `_resolve_agent`-style
  agent picking at the concierge is DELETED, not flagged.

## Hard constraints (Anu's words)

1. **No new APIs, no new invocation paths.** `POST/PUT /api/events/agents` is REUSED as
   "add a sub-agent" (roster mutation). /invoke envelope unchanged (agent field = "cuga").
2. Adding a sub-agent should NOT require `make reload` if avoidable → hot roster rebuild on
   upsert (runtime already invalidates graphs on upsert; extend to the supervisor graph).
3. Slack, Discord, Telegram, all integrations + triggers must keep working. Mention gate,
   ask-till-legit, NL→flow pre-router (trigger compilation, NOT agent routing) all stay.
4. Test everything (offline + live suites both modes), update docs/HTML/UI, then thumbs up.

## Architecture after

- Arm: concierge compiles trigger (kind/source/event/slots/validate/connect/dedup) →
  flow targets "cuga" always. Flow names: `push-github-new-pr-cuga` etc.
- Fire: /invoke {agent:"cuga"} → runtime resolves to THE one graph
  (plain or supervisor per flag) → (flag set) SupervisorNode picks sub-agent from roster →
  sub-run → bubble up → sink (unchanged delivery).
- Chat: channels → concierge (slash + NL→flow arming + pass-through) → cuga.
- Roster: AgentStore specs → `supervisor_roster.py` adapter → delegate configs
  (prompt + MCP tools via existing tool-provider path). Delegate DESCRIPTIONS generated
  from the trigger registry (routing hints can't drift).
- Attribution: picked delegate recorded in runmeta → Runs tab still shows who worked.

## REVISION (Anu, same day) — roster source of truth

"No new APIs" clarified: **leverage CUGA-main's canonical sub-agent mechanism — the supervisor
config YAML (`supervisor.config_path` → `load_supervisor_config`) — as THE way sub-agents are
defined and added.** Do not keep the events AgentStore/agents-CRUD as a parallel creation path.

- The 27 become `supervisor_agents.yaml` in the canonical schema (one-time conversion from
  seed.py; thereafter the YAML is the truth; seed.py retires for agents).
- Add a sub-agent = edit the YAML + `make reload` (main has NO hot-add; we do NOT invent one).
- The loader likely needs ONE additive, in-schema extension: `mcp_servers:` on an agent entry
  (today it builds CugaAgent from tools/module/a2a) — extend main's loader, don't wrap it.
- `GET /api/events/agents` becomes a READ-ONLY roster view (Studio Agents tab = view sub-agents;
  the Add/Edit form is retired or becomes a "writes the YAML for you" convenience — DECIDE W/ ANU).
- POST/PUT /api/events/agents: retire (410/disabled) — creation goes through the YAML.
- Custom `supervisor_roster.py` adapter: NO LONGER a store adapter — replaced by the YAML +
  (small) loader extension. Trigger-hint descriptions still generated into the YAML entries
  from triggers.py at conversion time.

## Phases

0. **Spike (first)**: supervisor graph with 3 delegates (weatherbot, pr_reviewer, mailbot)
   from the adapter; ~20 payloads (chat + synthetic trigger events); measure pick accuracy +
   latency. Tune roster prompt before scaling to 27.
1. `events/supervisor_roster.py` (NEW): AgentSpec → delegate; descriptions from triggers.py.
2. `runtime.py`: single-agent resolution ("cuga"); flag picks plain vs supervisor graph;
   upsert → hot rebuild supervisor graph (drain in-flight runs).
3. `concierge.py`: delete agent-picking (`_resolve_agent` usage, capability routing);
   keep slash parsing, pre-router/flowspec (trigger compilation + ask-till-legit),
   find_or_create_flow → target "cuga".
4. `flows.py` / `ap_engine.py`: flow target + names → "cuga". Direct watcher subscription
   rows (slack/discord/box) target "cuga" too.
5. `tests/`: new `test_delegation_bench.py` (payload → expected sub-agent, per trigger +
   ambiguous, scored in CI when flag set). Rework suites that assert per-agent invocation
   (test-suite-now, live harnesses assert the PICK via runmeta). Offline gate green in
   BOTH flag modes.
6. Docs/UI: ARCHITECTURE.md, agent_hosting.html (3rd diagram: supervisor mode),
   nl_to_flow.html (agent-resolution step removed), slides (gen_slides), GAPS/ROADMAP,
   Studio: "supervisor mode" banner; Agents tab = roster view ("sub-agents of CUGA");
   api.html wording for /api/events/agents (= add sub-agent). Drift gates force the rest.

## NL→Flow is a keeper (Anu, explicit)

NL→Flow is new, valued work and must stay SOLID through this refactor: the pre-router
(flowspec.py), ask-till-legit, the validation gate, dedup, and the 47-case zero-wrong-at-high
benchmark all survive untouched — only the agent-PICKING step is deleted (target is always
"cuga"). The bench keeps running in CI both modes; suite-flows stays a release gate.

## Open questions to resolve during build (not blockers)

- Unset-mode events delivery: everything → plain cuga-default (generalist). Verify seed
  examples/suites degrade gracefully in that mode (they assert specialist behavior → those
  live suites run flag-set only).
- SDK follow-up (non-blocking): share the roster adapter so CugaSupervisor(agents_from=specs)
  means the same thing in scripts — filed, not built now.

## Phase 0 RESULT (2026-07-15) — GREEN LIGHT

3-delegate spike (canonical load_supervisor_config → CugaSupervisor; the spike scaffolding later
evolved into tests/events/live_delegation_bench.py and was removed):
**pick accuracy 12/12 (100%)** incl. 3 near-ambiguous
traps; **0 self-answers**; supervisor overhead floor ~3s (median 27s includes delegate work).
Design validated: fire texts carry a `[source/kind]` prefix + delegate descriptions carry
HANDLES-trigger hints. Findings for later phases:
  * CombinedToolProvider loads ALL registry tools (per-agent filter is a TODO upstream) —
    per-delegate tool scoping = our one real loader extension (or accept shared tools v1).
  * delegation.py uses a FIXED thread per delegate (supervisor_conversational_{name}) —
    per-user/thread isolation must be threaded through in Phase 2/3.
  * SDK CugaSupervisor.add_agent() exists → canonical HOT roster add (graph lazily rebuilt on
    next invoke) — no reload needed for adds; reload only for YAML edits of existing agents.

## PROGRESS LOG (update as phases land)

- 2026-07-15: Phase 0 DONE (spike 12/12). Phase 1 DONE (`supervisor_agents.yaml` at repo root:
  cuga + 27 sub-agents, generated by `scripts/gen_supervisor_roster.py`, validated via canonical
  loader). Phase 2 PARTIAL: `SupervisorRuntime` in events/runtime.py (flag=1 → one "cuga" agent,
  CugaSupervisor from the YAML, upsert refuses, list_agents = read-only roster; selection wired in
  make_runtime via EVENTS_SUPERVISOR). **LIVE SMOKE PASSED**: EVENTS_SUPERVISOR=1 set in .env,
  reloaded, /api/concierge weather question → supervisor → weatherbot → answer. 196 offline green
  (flag unset unchanged during transition). PENDING: unset→classic-agent path (Phase 4),
  concierge de-routing (Phase 3), seed block in main.py still calls seed_default_agents (harmless
  — upsert refusal caught + logged; remove in Phase 3/4 cleanup).

- 2026-07-15 (later): Phases 2–4 CORE DONE + PROVEN LIVE. runtime.py: SupervisorRuntime
  (flag=1) + ClassicRuntime (unset, plain agent, CugaRuntime subclass) — fleet selection
  RETIRED from make_runtime (react/stub kept for tests). concierge.py: THE_AGENT="cuga";
  _arm_spec + _arm_slash push/cron/poll all target "cuga"; agent-picking removed from arming;
  find_or_create_flow synthesizes the cuga spec when runtime lacks it. Selection test rewritten
  (test_runtime_selection_single_agent_world). 196 offline green.
  **LIVE PROOF (EVENTS_SUPERVISOR=1)**: ask-till-legit → "ARMED push (github/new_pr) for cuga →
  web, flow push-github-new-pr-cuga" → synth-fired via /run (needs X-Gateway-Token) →
  run SUCCEEDED → supervisor delegated to PR specialist → real review answer. Cleaned up.
  REMAINING: Phase 5 (delegation benchmark; sweep test estate for per-agent assumptions —
  live suites esp. suite-now/matrix/fire + catalog agent names; runmeta pick surfacing),
  Phase 6 (docs/HTML/Studio/slides/CONCIERGE_PROMPT polish — prompt still mentions
  list_capabilities agents; seed block in main.py + seed.py agents retire; README/ARCHITECTURE/
  agent_hosting/nl_to_flow updates; live suites test-live + suite-flows in supervisor mode;
  Studio roster view/banner; thumbs-up report).

- 2026-07-15 (final): Phases 5–6 DONE. Delegation bench over the FULL 27 roster: **14/14 (100%),
  0 self-answers** (tests/events/live_delegation_bench.py · `make test-delegation`). Offline
  roster gates (test_supervisor_roster.py): parses · every trigger claimed · no stale hints.
  Two live root-causes found+fixed: (1) canonical loader now SCOPES each delegate's tools
  (CombinedToolProvider(app_names=…) — was loading ALL tools; latency collapsed 368s→87s suite),
  (2) delegates run headless → auto_load_policies defaults False in the loader (approval
  interrupts were hanging webhook runs → 502). Routed hook surfaces agent='cuga'; live_e2e
  updated to single-agent semantics + mention-aware slack probe. Studio Agents tab = roster
  view (Add/Edit retired); agents POST/PUT → 410 (spec documents it); prompt de-routed;
  seed agents block retired. Docs: ARCHITECTURE, nl_to_flow, agent_hosting banner, TESTING,
  .env example, Makefile test-delegation. **FINAL TALLY: make test 199 · test-live 38/0/0
  (87s) · suite-flows 10/0/1 · delegation 14/14 · arm+fire live-proven for 'cuga'.**

## State at plan time

- Branch: feat/events (dirty, nothing committed — Anu commits).
- Green baselines: make test 196 · test-live 39/0/0 · suite-flows 10/0/1 ·
  live_github_triggers 14/14 · live_direct_watchers 4/4 · NL→Flow bench 47 cases.
- Stack: podman AP :8081, CUGA :8100, tunnels up. EVENTS_SLACK_CHAT=mention live.

## NEXT WORK ITEM (2026-07-15, user request) — Runs UI: IDs + grouping
Backend DONE: run_logger.py writes EVERY execution (chat + fire) to disk —
results/run_logs/<date>/<time>_<id>.json + index.jsonl (kind, agent, channel, thread_id,
event_kind, text_in/answer_out, trace_id, ms). _log_now threaded with thread_id/kind/db.
REMAINING (UI): Studio RunsTab — show subscription_id / thread_id chip per row; click a chip →
filter all runs for that id (grouping); NOW rows group by thread. Then frontend rebuild + reload.

## WORK ITEM (2026-07-15 evening) — ENTRY-POINT UNIFICATION (user directive)
Goal: `cuga start demo --events` = THE entry point; `make up` provisions infra (AP container,
tunnels) then runs THE SAME command — no duplicated server-start logic. Requirements:
 (1) BYO agents: roster path configurable (EVENTS_SUPERVISOR_ROSTER, default ./supervisor_agents.yaml)
     — do NOT overfit to our 27; docs show "bring your own YAML".
 (2) Startup capability report (doctor-style): what's live (webhooks/direct watchers/chat) vs
     what needs AP/tunnels, each with its one-line fix. Reuse tests/events/preflight.py checks.
 (3) make commands POLICY: make stays for INFRA + TESTS only (ap, tunnels, channels, test-*);
     server start = the CLI. events_up.sh's server-launch block calls the CLI path.
DONE so far: telegram outage root-caused (stale quick-tunnel webhook) + fixed (make channels) +
doctor now checks the inbound edge (getWebhookInfo last_error/pending → "run make channels").
UNIFICATION PROGRESS: `cuga start <svc> --events` flag SHIPPED in src/cuga/cli/main.py
(sets EVENTS_ENABLED=1 + EVENTS_DB default; help verified; 199 green). BYO roster SHIPPED:
EVENTS_SUPERVISOR_ROSTER env → SupervisorRuntime roster_path (default ./supervisor_agents.yaml).
REMAINING: (
## ENTRY-POINT UNIFICATION — DONE (2026-07-15 evening)
- `cuga start demo --events` = THE entry point (cli/main.py: --events flag → EVENTS_ENABLED=1,
  logs guidance). BYO agents: EVENTS_SUPERVISOR_ROSTER (default ./supervisor_agents.yaml) —
  runtime.SupervisorRuntime honors it; docs "Bring your own agents".
- Capability report (events/capability.py): tiered honest report at startup (WARNING level, so it
  shows past uvicorn's warning filter) AND queryable at GET /api/events/status `capability` field.
- make up unchanged behaviorally but re-documented: infra provisioner that boots the SAME app;
  events_server.py doc'd as the make-up wrapper equivalent to --events. Makefile help updated.
  POLICY: make = infra+tests only; server start = the CLI.
- Telegram outage fixed (stale AP quick-tunnel webhook → make channels); doctor now checks the
  inbound edge (getWebhookInfo last_error/pending).
- 199 offline green throughout; capability queried live (4/4 ✓).
REMAINING (small): Studio banner could surface status.capability; folding events_up.sh's server
launch to literally shell out to `cuga start` (currently runs the same app via events_server.py —
same code path, so cosmetic).
