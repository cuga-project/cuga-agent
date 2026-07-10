# Setup & deployment — fresh machine → running events platform

The **single end-to-end runbook**: from setting up base CUGA, through Activepieces, to the
event-driven platform running with agents seeded. There's no single button for *everything* (some
pieces are external accounts a human must create), but the runtime services are one command each.

## Quick setup — the whole sequence
Top to bottom. **A–C are one-time**; **D onward is every boot**. Detailed sections (§0–§5) are below.

**A. Machine installs (one-time)**
```bash
brew install uv node podman cloudflared ngrok
podman machine init && podman machine start          # the Linux VM Podman needs on macOS
```

**B. Accounts / tokens (one-time, external)** — the irreducible manual part:
- **ngrok** (for a stable public URL — do this before wiring Slack/Gmail/Telegram/GitHub): verify your
  email → `ngrok config add-authtoken <token>` → reserve a domain at dashboard.ngrok.com/domains.
  Full step-by-step: **[setup/NGROK.md](setup/NGROK.md)**.
- **Bots/keys**: Telegram (@BotFather), Discord (dev portal + **Message Content Intent**), Slack app, watsonx key, and an AP admin email/password *(you invent it)*. Per-connector guides: [setup/](setup/).

**C. Project config (one-time)**
```bash
cp .env.events.example .env          # then fill in your creds (§3)
# the line that makes the URL stable (no more re-pointing Slack/Gmail):
#   EVENTS_NGROK_DOMAIN=<your-domain>.ngrok-free.app
make sync                            # populate .venv (re-run after dependency changes)
make env-check                       # confirm .env is complete → green
```

**D. Before a clean start — refresh the two *perishable* creds** (everything else in `.env` is stable):
- **Box** (`EVENTS_BOX_BACKEND=direct`) → `BOX_DEV_TOKEN` **expires ~60 min**. Regenerate it in the Box
  dev console right before Box tests, paste into `.env`, then `make reload` (the server caches `.env`
  at startup). Skip if `EVENTS_BOX_BACKEND` is unset/`ap` (OAuth push — no dev token).
- **Gmail** (Testing-mode OAuth) → the stored refresh token **expires after 7 days**; re-Connect in
  Studio → Integrations if stale.
- **`make nuke`/`make fresh` wipes AP volumes**, so **all** integration *connections*
  (Gmail/GitHub/Telegram) are lost and must be reconnected. `make stop && make up` keeps them.
- **To reset just your armed flows** (not connections/pieces), use **`make reset-flows`** — it wipes
  only `events.db` and bounces CUGA, leaving Activepieces (connections, pieces, tunnel) untouched, so
  there's **no reconnect and no `make channels`**. This is the everyday reset; save `nuke` for a true
  from-zero rebuild.
- **A fresh AP must re-sync its piece catalog from `cloud.activepieces.com`** — `make up` now
  force-installs the needed pieces after boot. If a Connect still 404s with `piece_metadata_not_found`
  (network blipped during boot), run **`make ap-pieces`** (idempotent). `make doctor` shows piece status.
- **Stable — no action across restarts:** the ngrok static URL (`EVENTS_NGROK_DOMAIN`/`EVENTS_PUBLIC_URL`),
  `HOST_CALLBACK_URL` (podman internal DNS), all bot tokens, OAuth *app* client id/secret,
  `GATEWAY_TOKEN`, WatsonX.

**E. Bring it up**
```bash
make fresh                           # clean slate: nuke → up (fresh AP + CUGA on ngrok) → channels → prints URL
#   — or a normal boot that keeps data + connections (recommended for iterating):
make up && make channels
```

**F. Wire external consoles (one-time, because the ngrok URL is stable)** — `make public-url` prints the exact strings:
1. **Slack** → api.slack.com/apps → your app → Event Subscriptions → Request URL `https://<domain>/api/events/slack/events` → subscribe `message.channels` → invite the bot
2. **Discord** → Developer Portal → Bot → enable **Message Content Intent**
3. **Gmail** *(only if used)* → Google Cloud redirect URI `https://<domain>/api/events/connect/gmail/callback`

**G. Verify**
```bash
make status      # everything up          make tunnels   # both tunnel agents up + reachable (200)
make doctor      # live creds ok          make test      # 61 offline checks green
```
Smoke-test the channels: DM your Telegram bot · post in the Discord/Slack channel · open `localhost:8100/studio`.

---

## Which test do I run, and when?

**The one command.** After any reset, or before a demo, or when you want something you can paste into
a PR:

```bash
GITHUB_TEST_REPO=owner/repo make test-report        # ~30 min; drop the var to skip the github row
```

It runs every harness in order, writes each one's raw output to `results/runs/<UTC-timestamp>/`, and
emits **two renderings of the same run**, both stamped with the UTC time, the commit, the branch, and
whether the tree was dirty — a dirty tree means the commit id does not describe the code that ran, so
commit first if you want a citable result:

| Output | Also copied to | Read it when |
|---|---|---|
| `report.md` | `results/LATEST.md` | pasting into a PR, an issue, or a terminal |
| `report.html` | `results/index.html` | reading it yourself — open it in a browser |

Both contain the **end-to-end walkthrough**: a row per step, in the second person ("you post *…* in the
Slack channel"), with an *Expected* column and an *Actually got* column. The HTML adds what the markdown
leaves out — every agent's actual answer and the MCP servers it reached, a hoverable trigger × sink grid,
and a link straight to each harness's raw log. Skip a phase with `ARGS="--skip now"`.

Both are **generated**. Never hand-edit `results/index.html`: the next `make test-report` overwrites it.

**The individual steps**, when you know what you changed and don't want to wait 30 minutes:

| You changed… | Run | Why | Time |
|---|---|---|---|
| anything at all, before pushing | `make test` | pure-python invariants; no stack, no creds | 1 s |
| you just restarted the stack | `make test-live` | is the plumbing alive — 4 channels + 4 flow modes, one probe each | 2 min |
| `seed.py`, an agent prompt, an MCP server | `make test-suite-now` | can each of the 18 agents still do its job? asserts on `meta.mcp`, so an agent cannot pass by answering from the model's memory instead of calling its tools | 14 min |
| `concierge.py`, `classify.py`, `ap_engine.py` | `make test-suite-flows` | does an English sentence still become the right Activepieces flow? | 6 min |
| added a channel or an integration | `make test-matrix` | is every trigger × sink combination wired, or only the ones we happened to try? | 6 min |
| `ap_engine.py`, a trigger field map, a watcher | `make test-fire` | **does an armed flow actually FIRE and answer?** arms a real 1-minute schedule, waits for a tick, reads the answer out of the run log | 9 min |
| a specific watcher on real data | the three `live_*_e2e.py` below | fire a real event through one integration | varies |

**What the arming harnesses prove, and what they don't.** `test`, `test-live`, `test-suite-*` and
`test-matrix` arm a flow and verify it exists in Activepieces — they never wait for a real event.
`test-fire` is the one that closes that gap: it fires a real schedule tick and reads the agent's
answer back. It fires cron/poll (real 1-minute schedules) and GitHub push (a webhook trigger, fed a
synthetic PR); it **cannot** fire a Gmail or Box watcher, because those are app-polling triggers that
Activepieces will not run out of band. For those, only a real inbound event proves the loop:

```bash
.venv/bin/python tests/events/live_github_e2e.py  # real open PR → pr_reviewer
.venv/bin/python tests/events/live_box_e2e.py     # real upload → resume_judge  (needs a FRESH BOX_DEV_TOKEN)
.venv/bin/python tests/events/live_gmail_e2e.py   # Gmail OAuth connection + arm inbox watcher
```

**Reading the result.** `FAIL` is the only thing to act on. `XFAIL` is a known gap with its reason
printed. `XPASS` means a known gap started passing — **re-sample before believing it**, because
`support_digest` fabricates on roughly 5 of 7 runs. `SKIP` means a surface isn't configured and never
counts as a pass. A `CRASH` row means the harness died before reporting; silence is not success.
Expected results per harness live in [TESTING.md](TESTING.md).

**Before you run anything live:** the connections must be there. After `make nuke`/`make fresh`, Gmail
needs a browser re-consent (`localhost:8100/api/events/connect/gmail`); GitHub and Telegram
auto-connect from `.env`. `make reset-flows` keeps all of them — prefer it. And `make sync` is **not**
required: every live harness is pure stdlib. Run `uv sync` only after a real dependency change.
And the **`/automate`** create-path — in the Studio Concierge (or any channel) type: `/automate summarize
new emails` (→PUSH) · `/automate the market brief every weekday 8am` (→CRON) · `/automate check bitcoin
every 5 min on a move` (→POLL). Manage them with `make flows`.

**H. Day-to-day (after the one-time steps)**
```bash
make up          # boot   ·   make reload  if you only changed .env/code (no tunnel churn)
make channels    # re-arm — needed after an AP-tunnel flap (Telegram); Slack/Gmail stay put
```
When a channel goes quiet: `make tunnels` (which agent died?) · `make public-url` (current URL) · `make logs`.

---

## TL;DR — the `make` shortcuts
Once base CUGA + `.env` are in place (§0–§3), the day-to-day loop is just `make` (a root
[`Makefile`](../Makefile) wraps the scripts below; run `make` with no target to list everything):

| Command | Does |
|---|---|
| `make sync` | `uv sync` the venv |
| `make env-check` | verify `.env` has the required keys — offline, no network |
| `make doctor` | live credential doctor (`preflight.py`) — pings each service |
| `make ap` / `make cuga` | start Activepieces / CUGA + registry + tunnels |
| `make ap-pieces` | ensure AP has the integration pieces installed (fixes fresh-DB `piece_metadata_not_found`) |
| `make up` (`make start`) | start both, AP first |
| `make channels` | connect + arm every inbound chat channel with a token in `.env` (run after `up`) |
| `make stop` | stop everything (AP + CUGA + tunnels), **keep** data |
| `make reset-flows` | wipe **only** `events.db` (your armed flows) + bounce CUGA — keeps AP connections/pieces/tunnels, **no reconnect** |
| `make nuke` | stop **and** wipe AP volumes (`ap_pgdata`/`ap_redis`) + `events.db` (full reset) |
| `make fresh` | full from-scratch cycle: `env-check` → `nuke` → `up` → `channels` → print public URL |
| `make reload` | bounce **only** CUGA (pick up `.env`/code) — keeps AP + tunnels, URLs unchanged |
| `make restart` | `stop` then `up` — then `make channels`. CUGA URL is stable if `EVENTS_NGROK_DOMAIN` set; else it changes |
| `make status` / `make logs` | what's running + tunnel URLs / tail the runtime logs |
| `make public-url` | print the current public URL + the exact Slack/Gmail strings to update |
| `make flows` | open the Flows console (list · pause/resume · delete · rich flow view) |
| `make tunnels` / `tunnels-up` / `tunnels-down` | status / (re)start / stop the tunnel agents (cloudflared + ngrok) |
| `make test` / `make test-all` | offline events suite / all offline tests (events + unit) |
| `make test-live` | live e2e — 4 channels + 4 flow modes. Needs the stack up (`make up`) + creds |
| `make test-suite-now` | every seeded agent, invoked directly (~13 min) |
| `make test-suite-flows` | cron + poll + push (~5 min) |
| `make test-matrix` | every trigger mode × every channel sink (~5 min) |
| `make reset-flows` | wipe ONLY `events.db` + bounce CUGA — keeps AP connections/pieces/tunnels |

The from-scratch runbook below is what those shortcuts wrap — read it once, then live in `make`.

## Cheat sheet — "I changed X, how do I make it take effect?"
CUGA caches `.env` at startup, so a change needs a restart. **Which** restart depends on *which
process reads the variable* — but for ~95% of `.env` the answer is just `make reload`.

| I changed… | Run | Why / notes |
|---|---|---|
| **Almost anything** — LLM/WatsonX keys, `GATEWAY_TOKEN`, any `EVENTS_*`, `AP_BASE_URL`/`AP_EMAIL`/`AP_PASSWORD`, `EVENTS_OAUTH_*`, `EVENTS_BOX_BACKEND`, **`BOX_DEV_TOKEN`**, `GITHUB_TOKEN` | **`make reload`** | bounces **only** CUGA (fresh process re-reads `.env`); keeps AP + tunnels + URL. ~10s, no reconnect. Then `make doctor` to confirm. |
| a **channel bot token** or the **public URL** webhooks point at (Telegram/Slack/Discord) | `make reload` **then `make channels`** | CUGA gets the value, but the webhook **registration** is stale — re-arm it. |
| **`AP_ENCRYPTION_KEY`** or **`AP_JWT_SECRET`** | **`make ap`** | baked into the AP **container** at run time (volumes kept → connections/pieces survive). ⚠️ changing the encryption key invalidates stored connections. |
| **`EVENTS_NGROK_DOMAIN`** (the CUGA tunnel domain) | **`make restart`** | read when the **tunnel** starts; `make reload` deliberately keeps the tunnel. |
| just want to **clear armed flows** (no `.env` change) | **`make reset-flows`** | wipes `events.db` only; AP connections/pieces/tunnels untouched. |
| a Connect 404s with `piece_metadata_not_found` | **`make ap-pieces`** | (re)installs the integration pieces a fresh AP needs. |

**Rule of thumb:** *edit `.env` → `make reload`.* Only reach for `make ap` / `make restart` for the
handful of AP-container / tunnel-domain vars above.

## The whole path at a glance
Do these in order. Each row links to the section/guide with the details and the "what success looks
like" check.

| # | Step | Command | Where | If it breaks |
|---|---|---|---|---|
| **0** | **Set up base CUGA** (repo, venv, deps, an LLM key) | `uv venv --python=3.12 && uv sync` | §0 + [root README](../README.md#quick-start) | `.venv` + `.env` exist; `uv run cuga --help` works |
| **1** | Extra tools the events layer needs | install `podman`, `cloudflared`, `ngrok`, `node`/`pnpm` | §1 | `command -v podman cloudflared ngrok node pnpm` |
| **1½** | **Set up ngrok** (stable public URL — before wiring Slack/Gmail/Telegram/GitHub) | `ngrok config add-authtoken …` + reserve a domain | [setup/NGROK.md](setup/NGROK.md) | `EVENTS_NGROK_DOMAIN` set; `make public-url` shows it |
| **2** | Fill `.env` (LLM + AP + events keys + `EVENTS_NGROK_DOMAIN`) | `cp .env.events.example .env`, edit, then `make env-check` / `make doctor` | §3 | `make env-check` green |
| **3** | **Start Activepieces** (the event engine) | `make ap` (`scripts/ap_up.sh`) | §2 | `curl -s localhost:8081/api/v1/flags` → 200 |
| **4** | **Start CUGA + registry + tunnels** (seeds agents) | `make cuga` (`scripts/events_up.sh`) | §4 | `make status` |
| **4½** | **Arm inbound chat channels** (Telegram/Slack/Discord you have tokens for) | `make channels` | §4 | `make channels-status` |
| **5** | Verify | `make test` (offline) | [TESTING.md](TESTING.md) | 61 green |
| **6** | Open the Studio & connect apps (browser OAuth consent happens here) | `http://localhost:8100/studio` → Setup tab | [setup/](setup/) per connector | each guide ends with a Verify step |

> **Order matters:** install + set up ngrok (steps 1–1½) and register bot/OAuth apps *before* bring-up,
> so the URL is stable when you paste it into Slack/Gmail. AP (step 3) before CUGA (step 4) —
> `events_up.sh` expects AP reachable and holds the tunnels. Base CUGA (step 0) is a prerequisite for
> everything. The one thing that *must* wait until after `make up` is the browser **OAuth consent**
> (step 6) — it needs the callback endpoint live.
   
## 0. Set up base CUGA first
If you've never run CUGA in this repo, do the base install — the [root README **Quick Start**](../README.md#quick-start)
is authoritative. The essence:
```bash
git clone https://github.com/cuga-project/cuga-agent.git && cd cuga-agent
uv venv --python=3.12 && source .venv/bin/activate
uv sync                                  # CUGA deps → .venv (minutes)
# an LLM key in .env — OpenAI is simplest; WatsonX is what the events demos use:
#   OPENAI_API_KEY=...   OR   WATSONX_APIKEY / WATSONX_URL / WATSONX_PROJECT_ID
```
LLM provider options (OpenAI / WatsonX / Azure / Groq / RITS / OpenRouter) and the full `.env`
surface are in the [root README → LLM Configuration](../README.md#quick-start) and
[`.env.example`](../.env.example). Confirm base CUGA works before layering events on top. Then
continue below — §1–§5 add the events platform.

## 1. System prerequisites (one-time installs)
| Tool | Why | Install (macOS) |
|---|---|---|
| **Python 3.12** | CUGA runtime (`>=3.10,<3.14`) | `uv python install 3.12` |
| **uv** | Python deps / venv | `brew install uv` |
| **Node + pnpm** | build the Studio UI (pnpm monorepo; `frontend_build.sh` enables pnpm via corepack) | `brew install node` |
| **Docker or Podman** | run Activepieces | `brew install podman` (or Docker Desktop) |
| **cloudflared** | public tunnels — the **default** (AP tunnel always; CUGA tunnel when no ngrok) | `brew install cloudflared` |
| **ngrok** *(strongly recommended)* | a **stable** CUGA public URL via `EVENTS_NGROK_DOMAIN` (no more re-pointing Slack/Gmail). Needs a free account: verify email + reserve a domain at dashboard.ngrok.com, then `ngrok config add-authtoken <token>`. **Full guide: [setup/NGROK.md](setup/NGROK.md)** | `brew install ngrok` |
| *(dev only)* **mermaid-cli** | regenerate diagrams | `npm i -g @mermaid-js/mermaid-cli` |

> **Tunnels are local agent processes** — `cloudflared`/`ngrok` must stay running to hold their URL
> (if one dies its URL 502s). `events_up.sh`/`ap_up.sh` start them; check with **`make tunnels`**,
> (re)start a dead one with **`make tunnels-up`**, stop with **`make tunnels-down`**.

## 2. One-time project setup
```bash
uv sync --python 3.12                       # CUGA deps → .venv  (big, minutes)
# Studio UI (only if you want the web UI) — pre-built webpack bundle; rebuild after any .tsx change:
scripts/frontend_build.sh                    # pnpm install + build + publish → src/cuga/frontend/dist
cp .env.events.example .env                  # events template: TENANT/USER layout + a pointer to each
                                             # credential's setup guide. Fill it in (see §3), then `make env-check`.
```
**Activepieces** (the event engine) — use the script. It starts a **public tunnel** (channel
webhooks require an HTTPS URL, which AP builds from `AP_FRONTEND_URL`), (re)creates AP with a
**persistent volume**, and **signs up the admin** from `.env` (`AP_EMAIL`/`AP_PASSWORD`):
```bash
scripts/ap_up.sh            # cloudflared tunnel + AP (volume) + admin sign-up
scripts/ap_up.sh --stop     # stop AP + tunnel
# reuse an existing tunnel URL instead of starting one:
AP_TUNNEL_URL=https://<your-tunnel> scripts/ap_up.sh
```
> **Set the AP admin login before `make ap`.** `AP_EMAIL` / `AP_PASSWORD` in `.env` are credentials
> **you invent** — on the AP container's *first* boot the script signs this admin up, and you use the
> same pair to log into the AP UI at `http://localhost:8081`. AP enforces a strong-password rule, so
> use something like `AP_EMAIL=you@example.com` / `AP_PASSWORD=Sup3rSecret!` (≥8 chars, mixed
> case + a number + a symbol) — a weak value makes the first-boot sign-up fail silently. On later
> boots the admin already exists, so the sign-up returns `HTTP 403` — that's expected, not an error.
> `make env-check` flags these if unset.

Confirm the pieces (Telegram/Discord/Slack/HTTP/Schedule/Box/GitHub) are installed (CE ships them). **Why the tunnel
is mandatory for channels:** Telegram/Slack reject non-HTTPS webhooks, so AP must advertise the
tunnel as `AP_FRONTEND_URL`. NOW/CRON/POLL don't need it. The quick-tunnel URL is ephemeral —
re-run `ap_up.sh` if cloudflared restarts.

> ⚠️ **Do NOT set `AP_WORKER_TOKEN`.** AP 0.82's entrypoint mints it as a JWT signed with
> `AP_JWT_SECRET` when unset; a raw random string crash-loops the worker on Socket.IO
> "Authentication error" and every channel/schedule flow publish hangs ~30s. `.ap.env` (gitignored,
> auto-generated) should contain **only** `AP_ENCRYPTION_KEY` + `AP_JWT_SECRET`. `ap_up.sh` strips
> any stale `AP_WORKER_TOKEN` and gates on worker health.

## 3. The `.env` (the credential surface)
See the per-connector guides in [setup/](setup/) for how to get each. Keys:
- **LLM:** `WATSONX_APIKEY` / `WATSONX_URL` / `WATSONX_PROJECT_ID` (+ `LLM_PROVIDER`/`LLM_MODEL`).
- **AP:** `AP_BASE_URL`, `AP_EMAIL` + `AP_PASSWORD` (**you invent these** — the admin the script
  creates on AP's first boot, and your AP-UI login; see §2), `EVENTS_AP_PROJECT_GRAIN=shared` (CE).
- **Events:** `EVENTS_ENABLED=1`, `EVENTS_WORKER_BACKEND=cuga`, `EVENTS_SEED_AGENTS=1`,
  `EVENTS_DB=<abs path>.db` (persist subs/identity; default `:memory:` is wiped on restart),
  `GATEWAY_TOKEN`, `HOST_CALLBACK_URL=http://host.containers.internal:8100/invoke` (podman host
  alias; Docker: `host.docker.internal`).
  `events_up.sh` also sets `EVENTS_USER_ID=admin` (web Studio browses as admin, matching the telegram
  identity) and `DYNACONF_ADVANCED_FEATURES__SANDBOX_EXECUTION_TIMEOUT=120` (arXiv/Semantic Scholar
  are ~5.5s/call; the 30s default times out the papers agent).
- **Public URL (recommended: stable):** set **`EVENTS_NGROK_DOMAIN`** to a free reserved ngrok domain
  (verify email → reserve at dashboard.ngrok.com/domains). `events_up.sh` then serves `:8100` on it and
  pins `EVENTS_PUBLIC_URL`, so **Slack/Gmail get configured once and never break on restart**. Without
  it, `EVENTS_PUBLIC_URL` falls back to an ephemeral cloudflared quick-tunnel that changes every run.
  Full explainer: **[PUBLIC_URL.md](PUBLIC_URL.md)**.
- **Channels:** `TELEGRAM_BOT_TOKEN` + `EVENTS_TELEGRAM_BOT_USERNAME`, `DISCORD_BOT_TOKEN`, `SLACK_BOT_TOKEN`.
- **Integrations:** `BOX_DEV_TOKEN` (or OAuth via Admin UI), GitHub PAT (paste in UI).

Verify it all at once: `python3 tests/events/preflight.py`.

## 4. Bring it up — the one command
```bash
make cuga                       # → scripts/events_up.sh: MCP registry + CUGA tunnel + CUGA server (AP tunnel is ap_up.sh's)
make stop                       # stop AP + CUGA + tunnels (keep data);  make nuke also wipes the DBs
# (the raw scripts still work: scripts/events_up.sh  /  scripts/events_up.sh --stop)
```
It checks prereqs, ensures `.venv`, starts the **MCP registry** (with the cuga-apps config), the
**CUGA tunnel** (ngrok if `EVENTS_NGROK_DOMAIN` is set, else a cloudflared quick-tunnel), and the
**CUGA server** on :8100 — then prints the URLs. (The **AP tunnel** is owned by `ap_up.sh`, and it does
**not** start Activepieces — that's your long-lived container — or create external accounts.)

## 4½. Arm the inbound chat channels
Starting the server makes channels *available*, but each inbound channel still has to be **armed**
once (and re-armed whenever the tunnel URL changes). One command does every channel you have a token
for in `.env`:
```bash
make channels           # connect + arm Telegram/Slack/Discord (whichever tokens are set)
make channels-status    # show inbound-channel state without changing anything
```
What it does per channel (idempotent — safe to re-run):
- **Telegram** (AP backend) — ensures the bot **connection** exists in AP, then arms the webhook flow
  (`telegram webhook → /invoke → send`). The connection is also auto-created on server startup from
  `TELEGRAM_BOT_TOKEN`; this makes it explicit and re-runnable.
- **Slack** (direct) — prints the **Event Subscriptions Request URL** to paste into your Slack app
  (`<EVENTS_PUBLIC_URL>/api/events/slack/events`). No AP flow.
- **Discord** (direct) — the Gateway bot connects on server boot; arm just confirms. No AP flow.
- **Web** — built-in, always on.

> **Why you only see Telegram in the AP UI:** only AP-backed channels create AP flows. Slack and
> Discord run as **direct backends** (no AP flow), and integrations (GitHub/Box/Gmail) only create a
> flow when you *arm a trigger* from chat. An AP flow list showing just `inbound-telegram` is correct.

After `make channels`, run **`make public-url`** to get the public URL + the exact Slack/Gmail strings
to paste into those consoles. Full explainer: **[PUBLIC_URL.md](PUBLIC_URL.md)**.

## 5. What can NOT be scripted (external, human)
- **Activepieces container** first-run + admin account (your data lives there).
- **Bot/app accounts:** Telegram (@BotFather), Discord (dev portal + server invite +
  Message-Content-Intent), Slack (app + install), Box (business acct or dev token), GitHub **OAuth App**
  (client id + secret — *not* a PAT; `piece-github` accepts only OAuth. See setup/GITHUB.md).
- **watsonx** IBM Cloud API key + project.
- **Connecting integrations** (one click each in the Studio, or paste-token) — by design.

## Cost summary
| | |
|---|---|
| System installs | 5 tools (uv, node, podman, cloudflared, python) |
| One-time | `uv sync` (~minutes) · frontend build · AP container · `.env` |
| Per-boot | **1 command** (`events_up.sh`) → registry + CUGA tunnel + CUGA; AP container already up |
| External (human) | bot/app accounts (Telegram/Discord/Slack/Box/GitHub) + watsonx + AP admin |

So: **first machine ≈ 30–45 min** (mostly `uv sync` + creating bot accounts). **Subsequent boots
≈ one command.** The irreducible manual cost is the external accounts + the AP container.
