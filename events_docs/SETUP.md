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
- **ngrok** (for a stable public URL): verify your email → reserve a domain at dashboard.ngrok.com/domains → `ngrok config add-authtoken <token>`
- **Bots/keys**: Telegram (@BotFather), Discord (dev portal + **Message Content Intent**), Slack app, watsonx key, and an AP admin email/password *(you invent it)*. Per-connector guides: [setup/](setup/).

**C. Project config (one-time)**
```bash
cp .env.events.example .env          # then fill in your creds (§3)
# the line that makes the URL stable (no more re-pointing Slack/Gmail):
#   EVENTS_NGROK_DOMAIN=<your-domain>.ngrok-free.app
make sync                            # populate .venv (re-run after dependency changes)
make env-check                       # confirm .env is complete → green
```

**D. Bring it up**
```bash
make fresh                           # clean slate: nuke → up (fresh AP + CUGA on ngrok) → channels → prints URL
#   — or a normal boot that keeps data:
make up && make channels
```

**E. Wire external consoles (one-time, because the ngrok URL is stable)** — `make public-url` prints the exact strings:
1. **Slack** → api.slack.com/apps → your app → Event Subscriptions → Request URL `https://<domain>/api/events/slack/events` → subscribe `message.channels` → invite the bot
2. **Discord** → Developer Portal → Bot → enable **Message Content Intent**
3. **Gmail** *(only if used)* → Google Cloud redirect URI `https://<domain>/api/events/connect/gmail/callback`

**F. Verify**
```bash
make status      # everything up          make tunnels   # both tunnel agents up + reachable (200)
make doctor      # live creds ok          make test      # 61 offline checks green
```
Then smoke-test: DM your Telegram bot · post in the Discord/Slack channel · open `localhost:8100/studio`.

**G. Day-to-day (after the one-time steps)**
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
| `make up` (`make start`) | start both, AP first |
| `make channels` | connect + arm every inbound chat channel with a token in `.env` (run after `up`) |
| `make stop` | stop everything (AP + CUGA + tunnels), **keep** data |
| `make nuke` | stop **and** wipe AP volumes (`ap_pgdata`/`ap_redis`) + `events.db` |
| `make fresh` | full from-scratch cycle: `env-check` → `nuke` → `up` → `channels` → print public URL |
| `make reload` | bounce **only** CUGA (pick up `.env`/code) — keeps AP + tunnels, URLs unchanged |
| `make restart` | `stop` then `up` — then `make channels`. CUGA URL is stable if `EVENTS_NGROK_DOMAIN` set; else it changes |
| `make status` / `make logs` | what's running + tunnel URLs / tail the runtime logs |
| `make public-url` | print the current public URL + the exact Slack/Gmail strings to update |
| `make tunnels` / `tunnels-up` / `tunnels-down` | status / (re)start / stop the tunnel agents (cloudflared + ngrok) |
| `make test` / `make test-all` | offline events suite (~60) / all offline tests (events + unit) |
| `make test-live` | live e2e — needs the stack up (`make up`) + creds |

The from-scratch runbook below is what those shortcuts wrap — read it once, then live in `make`.

## The whole path at a glance
Do these in order. Each row links to the section/guide with the details and the "what success looks
like" check.

| # | Step | Command | Where | If it breaks |
|---|---|---|---|---|
| **0** | **Set up base CUGA** (repo, venv, deps, an LLM key) | `uv venv --python=3.12 && uv sync` | §0 + [root README](../README.md#quick-start) | `.venv` + `.env` exist; `uv run cuga --help` works |
| **1** | Extra tools the events layer needs | install `podman`, `cloudflared`, `node`/`pnpm` | §1 | `command -v podman cloudflared node pnpm` |
| **2** | Fill `.env` (LLM + AP + events keys) | `cp .env.events.example .env`, edit, then `make env-check` / `make doctor` | §3 | `make env-check` green |
| **3** | **Start Activepieces** (the event engine) | `make ap` (`scripts/ap_up.sh`) | §2 | `curl -s localhost:8081/api/v1/flags` → 200 |
| **4** | **Start CUGA + registry + tunnels** (seeds agents) | `make cuga` (`scripts/events_up.sh`) | §4 | `make status` |
| **4½** | **Arm inbound chat channels** (Telegram/Slack/Discord you have tokens for) | `make channels` | §4 | `make channels-status` |
| **5** | Verify | `make test` (offline) | [TESTING.md](TESTING.md) | 61 green |
| **6** | Open the Studio & connect apps | `http://localhost:8100/studio` → Setup tab | [setup/](setup/) per connector | each guide ends with a Verify step |

> **Order matters:** AP (step 3) before CUGA (step 4) — `events_up.sh` expects AP reachable and
> holds the tunnels. Base CUGA (step 0) is a prerequisite for everything.
   
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
| **ngrok** *(recommended)* | a **stable** CUGA public URL via `EVENTS_NGROK_DOMAIN` (no more re-pointing Slack/Gmail). Needs a free account: verify email + reserve a domain at dashboard.ngrok.com, then `ngrok config add-authtoken <token>` | `brew install ngrok` |
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
make cuga                       # → scripts/events_up.sh: MCP registry + 2 tunnels + CUGA server
make stop                       # stop AP + CUGA + tunnels (keep data);  make nuke also wipes the DBs
# (the raw scripts still work: scripts/events_up.sh  /  scripts/events_up.sh --stop)
```
It checks prereqs, ensures `.venv`, starts the **MCP registry** (with the cuga-apps config), two
**tunnels** (AP + CUGA), and the **CUGA server** on :8100 — then prints the URLs. (It does **not**
start Activepieces — that's your long-lived container — or create external accounts.)

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
  Message-Content-Intent), Slack (app + install), Box (business acct or dev token), GitHub PAT.
- **watsonx** IBM Cloud API key + project.
- **Connecting integrations** (one click each in the Studio, or paste-token) — by design.

## Cost summary
| | |
|---|---|
| System installs | 5 tools (uv, node, podman, cloudflared, python) |
| One-time | `uv sync` (~minutes) · frontend build · AP container · `.env` |
| Per-boot | **1 command** (`events_up.sh`) → registry + 2 tunnels + CUGA; AP container already up |
| External (human) | bot/app accounts (Telegram/Discord/Slack/Box/GitHub) + watsonx + AP admin |

So: **first machine ≈ 30–45 min** (mostly `uv sync` + creating bot accounts). **Subsequent boots
≈ one command.** The irreducible manual cost is the external accounts + the AP container.
