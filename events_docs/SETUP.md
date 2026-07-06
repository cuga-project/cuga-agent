# Setup & deployment — fresh machine → running events platform

The honest, complete cost (beyond `.env`) and how to bring it up. There is **no single button**
for *everything* — some pieces are external accounts a human must create — but there **is** one
command for the runtime services: `scripts/events_up.sh`.

## 1. System prerequisites (one-time installs)
| Tool | Why | Install (macOS) |
|---|---|---|
| **Python 3.12** | CUGA runtime (`>=3.10,<3.14`) | `uv python install 3.12` |
| **uv** | Python deps / venv | `brew install uv` |
| **Node + pnpm** | build the Studio UI (pnpm monorepo; `frontend_build.sh` enables pnpm via corepack) | `brew install node` |
| **Docker or Podman** | run Activepieces | `brew install podman` (or Docker Desktop) |
| **cloudflared** | public tunnels (channel webhooks + OAuth callbacks) | `brew install cloudflared` |
| *(dev only)* **mermaid-cli** | regenerate diagrams | `npm i -g @mermaid-js/mermaid-cli` |

## 2. One-time project setup
```bash
uv sync --python 3.12                       # CUGA deps → .venv  (big, minutes)
# Studio UI (only if you want the web UI) — pre-built webpack bundle; rebuild after any .tsx change:
scripts/frontend_build.sh                    # pnpm install + build + publish → src/cuga/frontend/dist
cp .env.example .env                         # then fill it in (see below)
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
Set `AP_EMAIL`/`AP_PASSWORD` in `.env` first (the script signs that admin up). Confirm the pieces
(Telegram/Discord/Slack/HTTP/Schedule/Box/GitHub) are installed (CE ships them). **Why the tunnel
is mandatory for channels:** Telegram/Slack reject non-HTTPS webhooks, so AP must advertise the
tunnel as `AP_FRONTEND_URL`. NOW/CRON/POLL don't need it. The quick-tunnel URL is ephemeral —
re-run `ap_up.sh` if cloudflared restarts.

> ⚠️ **Do NOT set `AP_WORKER_TOKEN`.** AP 0.82's entrypoint mints it as a JWT signed with
> `AP_JWT_SECRET` when unset; a raw random string crash-loops the worker on Socket.IO
> "Authentication error" and every channel/schedule flow publish hangs ~30s. `.ap.env` (gitignored,
> auto-generated) should contain **only** `AP_ENCRYPTION_KEY` + `AP_JWT_SECRET`. `ap_up.sh` strips
> any stale `AP_WORKER_TOKEN` and gates on worker health.

## 3. The `.env` (the credential surface)
See [CHANNELS_SETUP.md](CHANNELS_SETUP.md) for how to get each. Keys:
- **LLM:** `WATSONX_APIKEY` / `WATSONX_URL` / `WATSONX_PROJECT_ID` (+ `LLM_PROVIDER`/`LLM_MODEL`).
- **AP:** `AP_BASE_URL`, `AP_EMAIL`, `AP_PASSWORD`, `EVENTS_AP_PROJECT_GRAIN=shared` (CE).
- **Events:** `EVENTS_ENABLED=1`, `EVENTS_WORKER_BACKEND=cuga`, `EVENTS_SEED_AGENTS=1`,
  `EVENTS_DB=<abs path>.db` (persist subs/identity; default `:memory:` is wiped on restart),
  `GATEWAY_TOKEN`, `HOST_CALLBACK_URL=http://host.containers.internal:8100/invoke` (podman host
  alias; Docker: `host.docker.internal`), `EVENTS_PUBLIC_URL=<cuga tunnel or http://localhost:8100>`.
  `events_up.sh` also sets `EVENTS_USER_ID=admin` (web Studio browses as admin, matching the telegram
  identity) and `DYNACONF_ADVANCED_FEATURES__SANDBOX_EXECUTION_TIMEOUT=120` (arXiv/Semantic Scholar
  are ~5.5s/call; the 30s default times out the papers agent).
- **Channels:** `TELEGRAM_BOT_TOKEN` + `EVENTS_TELEGRAM_BOT_USERNAME`, `DISCORD_BOT_TOKEN`, `SLACK_BOT_TOKEN`.
- **Integrations:** `BOX_DEV_TOKEN` (or OAuth via Admin UI), GitHub PAT (paste in UI).

Verify it all at once: `python3 tests/events/preflight.py`.

## 4. Bring it up — the one command
```bash
scripts/events_up.sh            # starts: MCP registry + 2 cloudflared tunnels + CUGA server
scripts/events_up.sh --stop     # stops them
```
It checks prereqs, ensures `.venv`, starts the **MCP registry** (with the cuga-apps config), two
**tunnels** (AP + CUGA), and the **CUGA server** on :8100 — then prints the URLs. (It does **not**
start Activepieces — that's your long-lived container — or create external accounts.)

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
