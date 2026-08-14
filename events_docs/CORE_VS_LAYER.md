# How much are we changing CUGA?

Measured against `main`, on the merged `events-followup-tooling-docs` (PR #603, which contains
#602). Reproduce with `git diff main --numstat`.

## The headline

| Area | Files | Added | Deleted |
|---|---:|---:|---:|
| **CUGA core** (code) | **6** | **+763** | **−18** |
| Events layer (new subsystem) | 47 | +16,331 | −0 |
| Tests | 59 | +16,686 | −0 |
| Docs | 44 | +8,067 | −0 |
| Deploy / scripts / Make | 27 | +4,899 | −0 |
| Frontend | 10 | +2,296 | −6 |
| Other (rosters, config, examples) | 24 | +1,759 | −0 |

**18 lines of CUGA core removed in total**, across six files. Everything else is new code sitting
beside CUGA, not inside it.

### What those six core files are

| File | Δ | What it is |
|---|---:|---|
| `server/run_routes.py` | +505 | **new** — `POST /run`, `GET /run/agents`, the roster, the auth gate |
| `server/events_bridge.py` | +142 | **new** — the slash forwarder; the only core file that knows the events service exists, and it knows only a URL |
| `server/main.py` | +76 / −1 | five seams: the router mount, `events_api_url` on `/api/ui/config`, slash dispatch in `/stream`, and two comments |
| `supervisor_utils/supervisor_config.py` | +32 / −16 | the one place existing CUGA *behaviour* changes — see the asterisk below |
| `cli/main.py` | +4 / −1 | stop clobbering an exported `MCP_SERVERS_FILE` |
| `.dockerignore` | +4 | re-admit `docs/examples/events/**`, which `docs/**` was excluding — without it the deployed roster is missing and every fire runs as one bare agent |

The two new files were **extracted out of `main.py`**, not grown inside it (review #6). `main.py`
peaked at 4,949 lines and is back to 4,433 — 75 over `main`.

## The only 2 lines removed from `main.py` / `cli/main.py`

Both are one-line *replacements*, not removals:

```diff
- return JSONResponse({"hide_cuga_logo": hide_logo, "brand_name": brand_name})
+ return JSONResponse({…, "events_api_url": …})     # the UI needs to find the events service

- os.environ["MCP_SERVERS_FILE"] = "none"
+ if not (os.environ.get("MCP_SERVERS_FILE", "") or "").strip():   # was clobbering an exported value
+     os.environ["MCP_SERVERS_FILE"] = "none"
```

What core *gains* is additive: `POST /run`, `GET /run/agents`, an optional preloaded supervisor, one
extra field on a config response, and the HTTP forwarder (`SLASH_VERB_NAMES`, `forwards_to_events`,
`forward_slash_to_events` — now in `events_bridge`, not `main.py`).

`/run` is **mounted only when configured** (a token, a roster, or the explicit dev flag). Vanilla
CUGA answers 404 there, not 401, and gains no new attack surface. With a token set, `/run` fails
closed: no token on the request, no execution.

## The honest asterisk — `supervisor_config.py`

This is the one place we touched **existing CUGA behaviour** rather than only adding to it. Two
changes live here; **only the second one is a behaviour change**, and this doc was wrong about that
for a while — see below.

1. **Policy auto-loading — NOT a behaviour change (this doc used to say otherwise).**
   ```python
   async def load_supervisor_config(yaml_path: str, *, auto_load_policies: bool | None = None)
   ```
   Precedence: a per-agent `auto_load_policies:` key in the YAML wins; else the caller's default;
   else `None`, which lets `CugaAgent` fall back to `settings.policy.auto_load_policies` exactly as
   it always has. `CugaSupervisor.from_yaml()` and `cuga_graph/graph.py` pass nothing and are
   untouched. Only the headless caller opts out, at its own call site — `server/run_routes.py` passes
   `auto_load_policies=False` because everything that supervisor runs is a scheduled tick, a webhook
   or a channel event, and an approval interrupt with nobody present hangs the run until the caller
   times out.

   An earlier cut hardcoded `False` *inside* `load_supervisor_config`, silently disabling policy
   auto-loading for every downstream supervisor user regardless of their settings. That is fixed;
   `src/cuga/sdk_core/tests/test_supervisor_policy_default.py` pins the contract, including that the
   parameter stays keyword-only with a `None` default.

2. **`CombinedToolProvider` is now actually scoped** to the named apps/servers, and hyphenated names
   are mapped to underscores (`cuga-finance` → `cuga_finance`; the hyphenated form composed invalid
   Python identifiers downstream — `cuga-finance_get_price` parses as subtraction). Previously the
   filter was a no-op that logged intent and loaded everything. An agent naming nothing still gets
   all tools, so that path is unchanged.

## The coupling, in one direction

- CUGA imports **nothing** from `cuga.backend.events`. A test fails if that regresses.
- The events package imports exactly **one** thing from CUGA core: `resolve_secret`.
- The only link out of core is an HTTP POST guarded by `EVENTS_API_URL`. Unset it and core is
  upstream CUGA.
- The dependency runs the **other** way: the eventing service needs CUGA, because CUGA executes
  every agent call.

Verdict: **this is a layer on top, not a fork of core** — with `supervisor_config.py` as the one
genuine in-core behaviour change.

---

# What works without Activepieces

**15 of the 42 registry triggers are `backend=direct`** — zero AP, zero OAuth — plus cron, poll and
inbound webhooks, which are scheduler modes rather than registry rows.

| App | Direct triggers | |
|---|---:|---|
| **slack** | 8 | `new_channel_message` · `new_reaction` · `reaction_removed` · `new_slack_mention` · `channel_created` · `new_slack_user` · `new_emoji` · `saved_message` |
| **discord** | 3 | `new_member` · `new_channel_message` · `new_reaction` |
| **box** | 2 | `new_folder` · `new_box_comment` (token-poll, no OAuth app) |
| **telegram** | 1 | `new_channel_message` |
| **webhook** | 1 | `inbound` |

Plus all four **chat channels** (web · slack · discord · telegram) and the native scheduler
(cron · poll with 4 delta tiers).

## Test coverage — what is actually proven

You are right to be suspicious: **chat is proven far better than the watchers.**

| Capability | Status | Evidence |
|---|---|---|
| Chat — all 4 channels, on Code Engine | **PROVEN** | `live_e2e.py --only channels` — 16 passed · 0 failed, real `chat.postMessage` / `sendMessage` / Discord REST |
| Slack inbound from **Slack itself** | **PROVEN** | real `@mention` typed by a human → in-thread reply (2026-08-05) |
| Arming + HITL gate over a real channel | **PROVEN** | real Slack `/automate` → card → `yes` → `ARMED` (2026-08-05) |
| cron + poll fire | **PROVEN** | `live_fire.py` — 2/2 on Code Engine (2026-08-13), real tool answers, native scheduler, no AP |
| Inbound webhook | **PROVEN** | `live_e2e.py` webhook × 3 modes |
| Offline suite | **PROVEN** | 411 passed · 27 skipped, hermetic (SQLite); the 27 are the Postgres store tests, run via `make test-pg` |
| **The 15 direct watchers** | **PARTIAL** | `live_direct_watchers.py` fires each with a *correctly signed* Slack callback — proves signature → routing → match → dispatch → delivery. It does **not** prove Slack/Discord actually deliver that event type; that depends on per-event app subscriptions. The harness's `--scopes` flag reports which are in place. |
| Box direct | **UNPROVEN on CE** | `live_box_direct_check.py` exists and needs a fresh 60-min Box developer token; not run in this cycle |
| Durable state | **FIXED — Postgres** | `EVENTS_DB` now takes a Postgres URL; local dev and the deployment run the same engine. the store tests run against real Postgres (`make test-pg`) and the rest on SQLite. Proven locally: arm a flow, kill the process, cold-boot — the flow is still there with zero snapshot machinery. |

So: **the transports are well tested; the 15 watcher event-types are only half tested** — our side
is verified byte-for-byte, the provider's delivery side is not, except for Slack `app_mention` which
a human confirmed.

---

# Re-enabling Activepieces — how big is it?

**Small in code, moderate in infrastructure, and gated on human OAuth clicks.** Nothing was deleted
when AP was switched off.

## Already done

| | |
|---|---|
| AP engine code | ~1,842 LOC intact (`ap_engine.py` 807 · `catalog.py` 641 · `oauth.py` 266 · `connectors.py` 128) |
| 27 AP triggers | registered and validated — GitHub 14 · Gmail 4 · Calendar 3 · Pinterest 3 · Box 1 · RSS 1 · YouTube 1 |
| Agents ready | `pr_reviewer` and `incident_triage` already declare the GitHub/Box triggers in their `integrations` (`events/seed.py`). **No agent work needed.** |
| Local orchestration | `make ap` · `make ap-pieces` · `make up` · `make test-ap` |
| Harnesses | `live_github_e2e` · `live_github_real_pr` · `live_github_triggers` · `live_gmail_e2e` · `live_integrations_e2e` · `live_new_pieces` |
| **A latent crash — fixed** | `reachable()` had been pasted into the middle of `__init__`, so `_auth_lock` and friends were never assigned; the first AP call would raise `'APEngine' object has no attribute '_auth_lock'`. Fixed, with `test_ap_engine_construction.py` (5 tests) guarding it. **This would have been the first thing you hit.** |
| Degradation | With AP down the only failures are the two assertions that check AP is down; integrations report `ap_not_configured` cleanly |

## What re-enabling actually costs

| Step | Effort | Note |
|---|---|---|
| Local: `make ap` (podman: app + postgres + redis) + `make ap-pieces` | **~30 min** | Mostly waiting on containers |
| Local: a public tunnel so AP can reach the callback | **~15 min** | `make tunnels` |
| Per-connector OAuth consent | **~20 min each, and only you can do it** | Gmail, GitHub, Box, Calendar, Pinterest. Gmail tokens expire after 7 days in test mode. |
| Run `make test-ap` | **~15 min** | Harnesses already exist |
| **Code Engine: deploy AP itself** | **the real work — 1–2 days** | AP is not deployed to CE at all. Needs an AP container plus **Postgres and Redis** (managed services or containers), a persistent volume, secrets, and network reachability from `cuga-events-svc`. This is a new piece of infrastructure, not a config flag. |

**Verdict:** getting AP working **locally** is roughly half a day, most of it OAuth consent screens
that need your hands. Getting AP running **on Code Engine** is the genuinely large item — call it
1–2 days — because it means standing up a stateful three-container service with its own database,
and the ephemeral-storage problem we already have for `events.db` applies to AP's Postgres with far
worse consequences.

**The sensible order:** durable state is **done** — `EVENTS_DB` is a managed PostgreSQL on Code
Engine and `make pg` runs the same engine locally. So: AP locally, then AP on CE.

---

## Sources

`git diff main --numstat` · `src/cuga/backend/events/triggers.py`
(registry) · `docs/examples/events/supervisor_agents.yaml` · `Makefile` (AP targets) ·
`deploy/ce/2_deploy.sh` · `tests/events/live_*.py`
