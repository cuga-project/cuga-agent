# Deploy CUGA (events, no Activepieces) to IBM Code Engine

**Two Code Engine apps, one image, one managed database:**

| App | Is | Port |
|---|---|---|
| `cuga-core` | vanilla CUGA — **the door** (`/run`, `/stream`, `/run/agents`, the Studio UI) | 7860 |
| `cuga-events-svc` | the eventing service — channel adapters, concierge, scheduler, `/invoke` | 8100 |
| `cuga-events-pg` | **IBM Cloud Databases for PostgreSQL** — where armed flows live | — |

On CE the app routes _are_ the public URLs, so there is no tunnel. The registry connects out to the
already-deployed `cuga-apps-mcp-*` tool servers; the only external dependencies are the **LLM**,
those (public, keyless) MCP routes, and the database.

The old single-app "combined" mode (`cuga start demo --events`) is **gone** — there is one topology.

Mirrors the routing team's CE conventions (same account/region/project/registry as
VAKRA). Admin-gated; nothing runs without `CUGA_CE_ADMIN=1`.

## Fresh machine → deployed (read this before the first deploy)

Three things bite a brand-new checkout, and only the first one fails loudly.

**1. Populate `.env` FIRST.** `make_env_ce.sh` builds the Code Engine secret *from your local
`.env`* — it does not prompt, and it does not warn about keys you never set. A clone with an empty
`.env` produces a secret missing the LLM credentials and every channel token, and the deploy
succeeds anyway. Fill it in from [`../docs/SETUP.md`](../docs/SETUP.md) → *`.env` keys*, then run
`./make_env_ce.sh`.

**2. Run `./4_postgres.sh` BEFORE `./2_deploy.sh` on a new project.** `2_deploy.sh` branches on
whether `EVENTS_DB` is already in the CE secret: present → managed PostgreSQL; absent → it falls
back to `EVENTS_DB=/app/.cuga/events.db`, which is **ephemeral container storage**. Every armed flow
is then lost on the next instance replace, and the deploy **still exits 0** — you get a `✗
durability` line in the post-deploy checks and nothing else. `4_postgres.sh` is idempotent: it
reuses an existing database instance rather than provisioning a second one.

> The DSN and its CA certificate live **only** in the CE secret — they are not in `.env`, not in
> `.env.ce`, and not in `make_env_ce.sh`'s key list. A normal redeploy keeps them because
> `ibmcloud ce secret update` *merges*; a `teardown.sh WIPE_SECRET=1` or a fresh project does not,
> because that takes the `secret create` path. Re-run `4_postgres.sh` to restore it.

**3. The route is stable — you never have to guess it.** Code Engine derives it from the app name
and the project subdomain, both fixed, so redeploying under the same names gives the same URL:

```
https://<app-name>.<project-subdomain>.<region>.codeengine.appdomain.cloud
```

Every `make ce-deploy` prints both routes when it finishes, and writes them to the gitignored
`.ce_urls.env`. To read them later: **`make ce-url`**. The live URLs are deliberately absent from
this repo — it is open source, and the deployment holds real channel credentials.

---

## Prerequisites
- `ibmcloud` CLI + the `code-engine` plugin (`ibmcloud plugin install code-engine`).
- Logged in to the routing account: **`ibmcloud login --sso`** → pick region `us-east`.
- A registry secret in the CE project (default `icr-secret-1`) — already present if
  the cuga-apps-mcp servers deploy there. If not, `1_build_push_image.sh` prints how.
- Your local `../../.env` with the LLM + channel creds (used to build the CE secret).

## Make targets (the easy path)

Every deploy/ops/test step has a `make` target (run from the repo root). They mirror
the scripts below and read the deployed URL from `events/deploy/.ce_urls.env`. **Local
targets are unchanged; these are the CE parallels — `[CE]` in `make help`.**

| Target | Does |
|---|---|
| `make ce-build` | cloud buildrun → ICR (`1_build_push_image.sh`) |
| `make ce-deploy` | deploy/redeploy BOTH apps (supervisor + the 8-agent `events/examples/rosters/default.yaml`; `CE_ROSTER=…` to change) |
| `make ce-smoke` | capability report + channels + a web-chat turn (`3_smoke.py`) |
| **`make test-e2e-ce`** | **the CE parallel of `make test-e2e`** — real channel + fire e2e against the deployed app |
| `make ce-status` | deploy status + the live capability report |
| `make ce-logs` | container logs — `FOLLOW=1` to stream · `GREP=telegram` to filter · `TAIL=n` |
| `make ce-url` | print the deployed URL |
| `make ce-teardown` | delete both apps (keeps image, registry secret, **and the database**) |

`test-e2e-ce` runs the **same harness** as `test-e2e`, only with `EVENTS_SERVER_URL`
pointed at the CE route; channel creds + `GATEWAY_TOKEN` come from your `.env` (they
must match the deployed secret). First-time setup still needs `make_env_ce.sh` (below).

**Login vs public-route.** `ce-smoke` and `test-e2e-ce` hit the app's **public route** — no
`ibmcloud` login needed. `ce-status` and `ce-logs` use the CE **control plane**, so they need
`ibmcloud login` first (region/group are `CE_REGION`/`CE_GROUP`, default `us-east`/`routing` — the
targets run `ibmcloud target -r … -g …` before selecting the project; override on the CLI). If you're
not logged in, `ce-status` says so plainly.

**What `test-e2e-ce` proves.** Arm **and FIRE** for real: all four channels round-trip, and native
cron/poll actually fire on the in-process scheduler and return a live agent answer (answer *content*
is not graded — the fire is what's tested). A channel with no token is SKIPPED and named; the harness
is no-AP-aware (AP-only checks skip, never fail). A synthetic-web delivery failure does not count
against a fire.

**Verify the background loops.** The direct channel loops (Telegram long-poll, Discord Gateway) and
the native scheduler only run if the events-background launcher fires at boot — confirm with
`make ce-logs GREP=launched` → `events: launched N background task(s)`. (That launcher was once
dropped in a merge, silently breaking Telegram/Discord + cron/poll; it's restored in `server/main.py`.)

## The events database (do this ONCE, before the first deploy)

Armed flows live in PostgreSQL. **This is not optional infrastructure** — without it, Code Engine
replacing the instance (a new revision, a node drain, a reschedule) silently deletes every armed
flow, with *no restart recorded*. On 2026-08-05 a cron armed from Slack at 11:12 was gone when a new
pod started at 11:24, and `ibmcloud ce app get` still read `Restarts: 0` throughout.

```bash
cd deploy/ce
YES=1 ./4_postgres.sh          # provisions the DB + credentials, writes the DSN into the CE secret
```

It creates an IBM Cloud Databases for PostgreSQL instance (**billable**; 8 GB RAM is the enforced
minimum), service credentials, and writes `EVENTS_DB` + `EVENTS_DB_CA_B64` into the existing
`cuga-events-secrets` — with `secret update`, so your bot tokens and watsonx keys are preserved.
`2_deploy.sh` then detects `EVENTS_DB` in the secret and wires it automatically.

- **Same engine as local dev** (`make pg`), which is the point: local testing now exercises the
  storage path that actually ships.
- **TLS is `verify-full`** — the CA rides in the secret as `EVENTS_DB_CA_B64` and is written to a
  0600 file at first connect. Do not "fix" a TLS error by dropping to `sslmode=require`; that keeps
  encryption but stops verifying the peer.
- **Cheaper fallback** if you don't want a managed DB: `./3_state_store.sh` keeps SQLite on local
  disk and snapshots it to a COS-backed data store. Single-writer, whole-file snapshots — it works,
  but it does not scale and local ≠ cloud again.
- Teardown commands are printed at the end of the script.

## Sequence (the scripts underneath)
```bash
cd deploy/ce
./make_env_ce.sh                        # .env.ce from ../../.env (gitignored, chmod 600)
YES=1 ./4_postgres.sh                   # ONCE: the events database (see above)

CUGA_CE_ADMIN=1 ./1_build_push_image.sh # cloud buildrun -> icr.io/.../cuga-events:latest (~10-20 min)
CUGA_CE_ADMIN=1 ./2_deploy.sh           # create BOTH apps; prints the routes + Slack step
python 3_smoke.py                       # capability report + channels + a web-chat turn
```
Redeploy after a code change: re-run 1 then 2. Change only env/roster (no rebuild): re-run 2 alone.
Tear down the apps: `CUGA_CE_ADMIN=1 ./teardown.sh` (leaves the database intact).

### The exact commands the live deploy used (copy-paste to reproduce)
```bash
cd deploy/ce
./make_env_ce.sh                                    # .env.ce from ../../.env
YES=1 ./4_postgres.sh                               # once — skips anything already there
CUGA_CE_ADMIN=1 YES=1 ./1_build_push_image.sh       # ~10-20 min
CUGA_CE_ADMIN=1 YES=1 ./2_deploy.sh                 # supervisor roster is ON by default
python 3_smoke.py
```
`YES=1` skips the interactive "Proceed? [y/N]" confirm (for automation); drop it to
be prompted. `CUGA_CE_ADMIN=1` is the required admin opt-in on every step.

### `2_deploy.sh` checks two things that fail SILENTLY

Both of these have shipped broken. Neither raises an error at runtime — they just make every answer
quietly worse, so the script asserts them while you are still watching:

```
== post-deploy checks ==
  ✓ roster: 9 agents on cuga-core
  ✓ durability: PostgreSQL — an instance replace is a non-event
```

- **roster: 1 agent** → `CUGA_SUPERVISOR_ROSTER` never reached `cuga-core`, so every fired flow runs
  the bare default agent with no sub-agents and no scoped tools. (`CE_EVENTS_SUPERVISOR` used to
  default to off, which caused exactly this; it now defaults to **on**.)
- **durability: false** → armed flows will be lost on the next instance replace. Run `./4_postgres.sh`.

## How environment variables get set (three sources)

The container's env comes from three places, applied in this order — **last wins**:

| # | Source | Set at | Holds | Change it by |
|---|---|---|---|---|
| 1 | **Dockerfile `ENV`** ([Dockerfile.events](Dockerfile.events)) | **build time** | rarely-changing non-secret defaults: `CUGA_HOST=0.0.0.0`, `DYNACONF_SERVER_PORTS__DEMO=7860`, `MCP_SERVERS_FILE`, `EVENTS_SCHEDULER=native`, the direct channel backends | edit the Dockerfile → rebuild (step 1) |
| 2 | **CE secret** `cuga-events-secrets` via `--env-from-secret` | **deploy time** (from `.env.ce`) | credentials + config-from-.env: `AGENT_SETTING_CONFIG`, `WATSONX_*`, `LLM_*`, `GATEWAY_TOKEN`, the channel tokens | edit `.env` → `./make_env_ce.sh` → step 2 |
| 3 | **Deploy-time `--env` literals** ([2_deploy.sh](2_deploy.sh)) | **deploy time** | per-deploy runtime knobs: `EVENTS_WORKER_BACKEND`, `EVENTS_SCHEDULER`, the backends, `MCP_SERVERS_FILE`, `EVENTS_SUPERVISOR`(+roster), `DEPLOY_REV`, and **`EVENTS_PUBLIC_URL`** (see below) | env vars on the `2_deploy.sh` command line, or edit the script |

Precedence: a deploy-time `--env` (source 3) **overrides** the same key baked into the
image (source 1). That's why `2_deploy.sh` re-passes `EVENTS_SCHEDULER=native`,
the backends, and `MCP_SERVERS_FILE` even though the Dockerfile already bakes them —
the deploy-time value is the source of truth, and the image `ENV` is just a sane
default if someone runs the container by hand. **Secrets are injected as env at
runtime** via `--env-from-secret` — never on a command line, never in the image,
never in git.

## The public URL — the chicken-and-egg, explained

**Your exact question:** the route only exists *after* the app is created, so how does
`EVENTS_PUBLIC_URL` get set ahead of time? It doesn't — it's set in a **second pass in
the same script run** ([2_deploy.sh](2_deploy.sh)):

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
- **State is durable — armed flows live in PostgreSQL** (`EVENTS_DB`), so an instance
  replace is a non-event. The container disk is still ephemeral, which is why nothing
  that must survive may be written to it. *(Conversation memory in the SQLite
  checkpointer is the remaining exception and is still lost on redeploy.)*
- **Remote MCP servers scale to zero** → the first tool call after idle has cold-start
  lag. Warm them before a demo.
- **`REAL` is not the same type in SQLite and Postgres.** SQLite's `REAL` is an 8-byte
  double; Postgres's is `float4`, ~7 significant digits. Every timestamp here is a Unix
  epoch (10 digits), so on Postgres they were silently rounded to a **~100-second grid**
  — a "1 minute" cron drifted onto that grid, the Runs log's times were wrong by up to
  ±50s, and the web mailbox's `since` cursor skipped messages that shared a bucket.
  `db._to_pg_types` now emits `DOUBLE PRECISION`, and `widen_real_columns()` repairs a
  database an older build already created (it logs `widened N float4 column(s)` once at
  boot). **The offline suite cannot catch this class of bug** — SQLite is unaffected —
  so schema changes want a run of `make test-pg` against a real PostgreSQL.

## Agent model (a `--env` change, no rebuild)
The agent model is pure env, so switching is just a **re-run of step 2** (the image
never changes):

```bash
# classic single generalist (script default)
CUGA_CE_ADMIN=1 ./2_deploy.sh

# supervisor over the full 27-agent roster (what the live deploy uses)
CUGA_CE_ADMIN=1 CE_EVENTS_SUPERVISOR=1 CE_ROSTER=events/examples/rosters/default.yaml ./2_deploy.sh

# supervisor over a focused, curated roster
CUGA_CE_ADMIN=1 CE_EVENTS_SUPERVISOR=1 CE_ROSTER=rosters/no_ap_research_desk.yaml ./2_deploy.sh
```
`CE_EVENTS_SUPERVISOR=1` sets `EVENTS_SUPERVISOR=1`; `CE_ROSTER` sets
`EVENTS_SUPERVISOR_ROSTER`. Every roster (`events/examples/rosters/default.yaml` + all of `rosters/`)
is baked into the image, so any of them is available without a rebuild. Any
`rosters/no_ap_*.yaml` (and the 27-agent default) runs with zero AP; the `ap_*` rosters
only light up their SaaS triggers once Activepieces is deployed.

## Files
| File | Purpose |
|---|---|
| `config.sh` | account/region/project/registry + app sizing + admin guard |
| `Dockerfile.events` | the one image both apps run; `2_deploy.sh` picks the command per app |
| `make_env_ce.sh` | build the gitignored `.env.ce` from your local `.env` |
| `1_build_push_image.sh` | cloud buildrun → ICR |
| `2_deploy.sh` | create the CE secret + app; set `EVENTS_PUBLIC_URL` |
| `3_smoke.py` | capability report + channels + a web-chat probe |
| `teardown.sh` | delete the app (optionally the secret) |
| `.env.ce.example` | placeholder template (real `.env.ce` is gitignored) |
