# Deploy CUGA (events, no Activepieces) to IBM Code Engine

One self-contained Code Engine app running `cuga start demo --events` — the SAME
command as `make up-noap`, minus the tunnel (**on CE the app's route _is_ the public
URL**). Registry + agent + the events layer boot as children of one process; the
registry connects out to the already-deployed `cuga-apps-mcp-*` tool servers. The
only external dependencies are the **LLM** and those (public, keyless) MCP routes.

Mirrors the routing team's CE conventions (same account/region/project/registry as
VAKRA). Admin-gated; nothing runs without `CUGA_CE_ADMIN=1`.

## Prerequisites
- `ibmcloud` CLI + the `code-engine` plugin (`ibmcloud plugin install code-engine`).
- Logged in to the routing account: **`ibmcloud login --sso`** → pick region `us-east`.
- A registry secret in the CE project (default `icr-secret-1`) — already present if
  the cuga-apps-mcp servers deploy there. If not, `1_build_push_image.sh` prints how.
- Your local `../../.env` with the LLM + channel creds (used to build the CE secret).

## Make targets (the easy path)

Every deploy/ops/test step has a `make` target (run from the repo root). They mirror
the scripts below and read the deployed URL from `deploy/ce/.ce_urls.env`. **Local
targets are unchanged; these are the CE parallels — `[CE]` in `make help`.**

| Target | Does |
|---|---|
| `make ce-build` | cloud buildrun → ICR (`1_build_push_image.sh`) |
| `make ce-deploy` | deploy/redeploy (supervisor + 27-agent roster; `CE_ROSTER=…` to change) |
| `make ce-smoke` | capability report + channels + a web-chat turn (`3_smoke.py`) |
| **`make test-e2e-ce`** | **the CE parallel of `make test-e2e`** — real channel + fire e2e against the deployed app |
| `make ce-status` | deploy status + the live capability report |
| `make ce-logs` | container logs — `FOLLOW=1` to stream · `GREP=telegram` to filter · `TAIL=n` |
| `make ce-url` | print the deployed URL |
| `make ce-teardown` | delete the app (keeps image + registry secret) |

`test-e2e-ce` runs the **same harness** as `test-e2e`, only with `EVENTS_SERVER_URL`
pointed at the CE route; channel creds + `GATEWAY_TOKEN` come from your `.env` (they
must match the deployed secret). First-time setup still needs `make_env_ce.sh` (below).

## Sequence (the scripts underneath)
```bash
cd deploy/ce
./make_env_ce.sh                        # .env.ce from ../../.env (gitignored, chmod 600)

CUGA_CE_ADMIN=1 ./1_build_push_image.sh # cloud buildrun -> icr.io/.../cuga-events:latest  (~10-20 min)
CUGA_CE_ADMIN=1 ./2_deploy_app.sh       # create the app; prints the route + Slack step
python 3_smoke.py                       # capability report + channels + a web-chat turn
```
Redeploy after a code change: re-run steps 1 then 2. Change only env/roster (no
rebuild): re-run step 2. Tear down: `CUGA_CE_ADMIN=1 ./teardown.sh`.

### The exact commands the live deploy used (copy-paste to reproduce)
```bash
cd deploy/ce
./make_env_ce.sh                                    # .env.ce from ../../.env
CUGA_CE_ADMIN=1 YES=1 ./1_build_push_image.sh       # ~10-20 min
CUGA_CE_ADMIN=1 YES=1 CE_EVENTS_SUPERVISOR=1 \
    CE_ROSTER=supervisor_agents.yaml ./2_deploy_app.sh
python 3_smoke.py
```
`YES=1` skips the interactive "Proceed? [y/N]" confirm (for automation); drop it to
be prompted. `CUGA_CE_ADMIN=1` is the required admin opt-in on every step.

## How environment variables get set (three sources)

The container's env comes from three places, applied in this order — **last wins**:

| # | Source | Set at | Holds | Change it by |
|---|---|---|---|---|
| 1 | **Dockerfile `ENV`** ([Dockerfile.events](Dockerfile.events)) | **build time** | rarely-changing non-secret defaults: `CUGA_HOST=0.0.0.0`, `DYNACONF_SERVER_PORTS__DEMO=7860`, `MCP_SERVERS_FILE`, `EVENTS_SCHEDULER=native`, the direct channel backends | edit the Dockerfile → rebuild (step 1) |
| 2 | **CE secret** `cuga-events-secrets` via `--env-from-secret` | **deploy time** (from `.env.ce`) | credentials + config-from-.env: `AGENT_SETTING_CONFIG`, `WATSONX_*`, `LLM_*`, `GATEWAY_TOKEN`, the channel tokens | edit `.env` → `./make_env_ce.sh` → step 2 |
| 3 | **Deploy-time `--env` literals** ([2_deploy_app.sh](2_deploy_app.sh)) | **deploy time** | per-deploy runtime knobs: `EVENTS_WORKER_BACKEND`, `EVENTS_SCHEDULER`, the backends, `MCP_SERVERS_FILE`, `EVENTS_SUPERVISOR`(+roster), `DEPLOY_REV`, and **`EVENTS_PUBLIC_URL`** (see below) | env vars on the `2_deploy_app.sh` command line, or edit the script |

Precedence: a deploy-time `--env` (source 3) **overrides** the same key baked into the
image (source 1). That's why `2_deploy_app.sh` re-passes `EVENTS_SCHEDULER=native`,
the backends, and `MCP_SERVERS_FILE` even though the Dockerfile already bakes them —
the deploy-time value is the source of truth, and the image `ENV` is just a sane
default if someone runs the container by hand. **Secrets are injected as env at
runtime** via `--env-from-secret` — never on a command line, never in the image,
never in git.

## The public URL — the chicken-and-egg, explained

**Your exact question:** the route only exists *after* the app is created, so how does
`EVENTS_PUBLIC_URL` get set ahead of time? It doesn't — it's set in a **second pass in
the same script run** ([2_deploy_app.sh](2_deploy_app.sh)):

```
1. ce app create   …  (NO EVENTS_PUBLIC_URL)      → CE assigns the route
2. ce app get --output url                         → read the now-known route
3. ce app update --env EVENTS_PUBLIC_URL=<route>   → CE rolls a NEW revision
```

So yes — **"update and refresh."** Step 3 changes the env var, and **Code Engine
automatically rolls out a new revision** (~30-60s brief re-boot) with the value baked
in. You never restart anything by hand.

What that means in practice:
- **For ~1-2 min between create and the update-revision going live**, the capability
  report shows `✗ no EVENTS_PUBLIC_URL`. Expected; it self-heals. Running `3_smoke.py`
  *after* the script finishes shows `✓ public URL set`.
- **The route is deterministic and stable:**
  `https://<app-name>.<project-hash>.<region>.codeengine.appdomain.cloud`. The
  `<project-hash>` is fixed per CE project and the app name never changes — so **every
  redeploy yields the identical URL** (even a delete/recreate keeps the name → keeps
  the URL). That's why Slack's Request URL, once pointed at the route, never needs
  re-pointing.
- **Is it strictly required?** For Slack *inbound* signature verification, no — only
  `SLACK_SIGNING_SECRET` matters. `EVENTS_PUBLIC_URL` drives OAuth callbacks,
  self-referential links, and an honest capability report. The two-pass is about
  correctness, not a hard requirement for chat.
- **Want to skip the second pass?** Because the URL is stable, on a *redeploy* you can
  set it up front by passing `--env EVENTS_PUBLIC_URL=https://cuga-events.<hash>.us-east.codeengine.appdomain.cloud`
  to the create. The script deliberately doesn't hardcode the hash — the
  create→read→update pattern works even on the very first deploy, when the hash is
  still unknown.

## What works on CE in the no-AP route
| Integration | On CE (no-AP) | Notes |
|---|---|---|
| **Web chat** | ✅ auto | in-process |
| **Telegram** | ✅ auto at boot | long-poll (outbound); token in the secret |
| **Discord** | ✅ auto at boot | Gateway WebSocket (outbound); token in the secret |
| **Webhook** | ✅ | CE route is public; set `EVENTS_WEBHOOK_KEY` |
| **Slack** | ✅ one manual step | point the Slack app's Request URL at `<route>/api/events/slack/events` — the CE route replaces the local tunnel |
| **cron / poll** | ✅ | native scheduler in-process (state is ephemeral — see below) |
| **Gmail · GitHub · Box · Calendar · Pinterest push** | ❌ by design | AP-only triggers; no AP = off. Deploy AP separately to enable. |

**Slack doesn't need a tunnel on CE** — locally it needs ngrok because Slack POSTs
inbound to localhost; on CE the platform route is already public.

## The load-bearing CE caveats
- **`--min-scale 1 --max-scale 1` (single, always-warm).** Telegram long-poll, the
  Discord Gateway, and the native scheduler are persistent *single-owner* loops —
  more than one instance double-processes events, and scale-to-zero kills the loops.
  This is a correctness constraint, not a cost knob.
- **Ephemeral state.** `events.db` (armed flows) and the SQLite checkpointer
  (conversation memory) live on the container's ephemeral disk → **lost on restart /
  redeploy**. Fine for a test. For durability, mount a COS persistent data store at
  the DB path (VAKRA's pattern) or move to Postgres.
- **Remote MCP servers scale to zero** → the first tool call after idle has cold-start
  lag. Warm them before a demo.

## Agent model (a `--env` change, no rebuild)
The agent model is pure env, so switching is just a **re-run of step 2** (the image
never changes):

```bash
# classic single generalist (script default)
CUGA_CE_ADMIN=1 ./2_deploy_app.sh

# supervisor over the full 27-agent roster (what the live deploy uses)
CUGA_CE_ADMIN=1 CE_EVENTS_SUPERVISOR=1 CE_ROSTER=supervisor_agents.yaml ./2_deploy_app.sh

# supervisor over a focused, curated roster
CUGA_CE_ADMIN=1 CE_EVENTS_SUPERVISOR=1 CE_ROSTER=rosters/no_ap_research_desk.yaml ./2_deploy_app.sh
```
`CE_EVENTS_SUPERVISOR=1` sets `EVENTS_SUPERVISOR=1`; `CE_ROSTER` sets
`EVENTS_SUPERVISOR_ROSTER`. Every roster (`supervisor_agents.yaml` + all of `rosters/`)
is baked into the image, so any of them is available without a rebuild. Any
`rosters/no_ap_*.yaml` (and the 27-agent default) runs with zero AP; the `ap_*` rosters
only light up their SaaS triggers once Activepieces is deployed.

## Files
| File | Purpose |
|---|---|
| `config.sh` | account/region/project/registry + app sizing + admin guard |
| `Dockerfile.events` | the image (`cuga start demo --events`) |
| `make_env_ce.sh` | build the gitignored `.env.ce` from your local `.env` |
| `1_build_push_image.sh` | cloud buildrun → ICR |
| `2_deploy_app.sh` | create the CE secret + app; set `EVENTS_PUBLIC_URL` |
| `3_smoke.py` | capability report + channels + a web-chat probe |
| `teardown.sh` | delete the app (optionally the secret) |
| `.env.ce.example` | placeholder template (real `.env.ce` is gitignored) |
