# Setup

Fresh machine → running events platform. The runtime is one command per service; the irreducible
manual part is external accounts (bots, OAuth apps) a human must create. Per-connector guides are in
[setup/](setup/); tests in [TESTING.md](TESTING.md); the public-URL details in [PUBLIC_URL.md](PUBLIC_URL.md).

## One-time

```bash
# 1. installs (macOS)
brew install uv node podman cloudflared ngrok
podman machine init && podman machine start          # the Linux VM podman needs on macOS

# 2. project
uv sync --python 3.12                                # CUGA deps → .venv (minutes)
scripts/frontend_build.sh                            # build the Studio UI (only if you want the web UI)
cp .env.events.example .env                          # then fill it in (see .env keys below)
make env-check                                       # .env complete → green
```

**Accounts / keys (external, unavoidable):** an LLM key (watsonx or OpenAI); an AP admin
email+password *you invent*; bot tokens (Telegram @BotFather, Discord dev portal + **Message Content
Intent**, Slack app); and for the OAuth integrations, an **OAuth App** each (Gmail, GitHub, Box) —
client id + secret. Details per connector in [setup/](setup/).

**ngrok (strongly recommended)** — reserve a free domain and set `EVENTS_NGROK_DOMAIN=<your>.ngrok-free.app`.
This pins the public URL so you configure Slack/Gmail **once** instead of re-pointing them after every
restart. Without it you get an ephemeral cloudflared tunnel that changes each run. See [setup/NGROK.md](setup/NGROK.md).

### `.env` keys

- **LLM:** `WATSONX_APIKEY` / `WATSONX_URL` / `WATSONX_PROJECT_ID` (or `OPENAI_API_KEY`) + `LLM_PROVIDER`/`LLM_MODEL`.
- **AP:** `AP_BASE_URL`, `AP_EMAIL` + `AP_PASSWORD` (**you invent** these; the admin created on AP's
  first boot and your AP-UI login), `EVENTS_AP_PROJECT_GRAIN=shared`.
- **Events:** `EVENTS_ENABLED=1`, `EVENTS_WORKER_BACKEND=cuga`, `EVENTS_SEED_AGENTS=1`,
  `EVENTS_DB=<abs path>.db`, `GATEWAY_TOKEN`,
  `HOST_CALLBACK_URL=http://host.containers.internal:8100/invoke` (podman host alias).
- **Public URL:** `EVENTS_NGROK_DOMAIN` (recommended, above).
- **Channels:** `TELEGRAM_BOT_TOKEN` (+ `EVENTS_TELEGRAM_BOT_USERNAME`), `DISCORD_BOT_TOKEN`, `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET`.
- **Integrations:** `EVENTS_OAUTH_{GMAIL,GITHUB,BOX}_CLIENT_ID`/`_SECRET` (OAuth apps), or `BOX_DEV_TOKEN` for Box direct-poll.
- **Security (set before exposing publicly):** `SLACK_SIGNING_SECRET`, `EVENTS_WEBHOOK_KEY` — unset, those endpoints accept anything.

Two AP gotchas that bite hard:
- **Pick a strong `AP_PASSWORD`** (≥8 chars, mixed case + number + symbol). AP enforces it, and a weak
  one makes the first-boot admin sign-up fail *silently*. On later boots the sign-up returns `HTTP 403`
  — expected, not an error.
- **Do NOT set `AP_WORKER_TOKEN`.** AP mints it as a JWT itself; a raw value crash-loops the worker and
  every flow publish hangs. `ap_up.sh` strips a stale one.

## Bring it up

```bash
make up                 # AP (container + tunnel) then CUGA (registry + tunnel + server). AP before CUGA.
make channels           # arm inbound channels for every bot token in .env (idempotent)
make public-url         # prints the public URL + the exact strings to paste into Slack/Gmail consoles
```

**Wire the external consoles once** (stable because of the pinned ngrok URL):
1. **Slack** → Event Subscriptions Request URL `https://<domain>/api/events/slack/events`, subscribe `message.channels`, invite the bot.
2. **Discord** → enable **Message Content Intent**.
3. **OAuth integrations** → each provider's redirect URI = `https://<domain>/api/events/connect/<app>/callback`, then **connect in the browser**: open `https://<domain>/api/events/connect/<app>` (or Studio → Integrations → Connect) and approve. GitHub is OAuth (scopes `repo` + `admin:repo_hook`), **not** a pasted PAT — `piece-github` accepts only OAuth.

**Verify:**
```bash
make status      # everything up + tunnel URLs      make doctor   # live creds ok
make test        # 154 offline checks green         make tunnels  # both tunnels reachable
```
Then smoke-test: DM the Telegram bot · post in Slack/Discord · open `localhost:8100/studio`. Full test
menu (live harnesses, the one-command report) is in [TESTING.md](TESTING.md).

## Day-to-day

**The rule: edit `.env` → `make reload`.** It bounces only CUGA (fresh process re-reads `.env`), keeps
AP + tunnels + URL, ~10s, no reconnect. `make help` lists everything; the ones you'll actually use:

| Command | Does |
|---|---|
| `make up` / `make stop` | start both (AP then CUGA) / stop both, **keep** data |
| `make reload` | bounce **only** CUGA to pick up `.env`/code — tunnels + URL unchanged |
| `make channels` | re-arm inbound channels (needed after an AP-tunnel flap; Telegram) |
| `make status` · `make logs` · `make tunnels` | what's running / tail logs / tunnel health |
| `make flows` | the Flows console (list · pause/resume · delete · inspect) |
| `make doctor` | ping each service with its real `.env` cred |

A few `.env` vars need more than `reload`: **`AP_ENCRYPTION_KEY`/`AP_JWT_SECRET`** → `make ap` (baked
into the container; ⚠ changing the encryption key invalidates stored connections); **`EVENTS_NGROK_DOMAIN`**
→ `make restart` (read when the tunnel starts); a **channel bot token or the public URL** → `make reload`
**then `make channels`** (re-register the webhook). If a Connect 404s with `piece_metadata_not_found`,
run `make ap-pieces`.

## Resets — know the difference

| Command | Wipes | Keeps | You must then |
|---|---|---|---|
| `make reset-flows` | `events.db` (your armed flows) | AP connections, pieces, tunnels | nothing — **prefer this** for a clean-slate day |
| `make nuke` | **AP volumes** + `events.db` | nothing | **reconnect every integration** (below) |
| `make fresh` | = `nuke` then `up` + `channels` | nothing | reconnect every integration |

**`make nuke` loses all AP connections.** After it, the OAuth integrations need a **browser re-consent**
(only you can do that): `https://<domain>/api/events/connect/{gmail,github}`. Box direct-poll uses
`BOX_DEV_TOKEN` from `.env` (which expires ~60 min — refresh it). Channels re-arm from `.env` via
`make channels`. So reach for `nuke` only for a true from-zero rebuild; `reset-flows` is the everyday reset.

## Perishable creds

- **`BOX_DEV_TOKEN`** expires **~60 min** — refresh it in the Box console before Box tests, then `make reload`.
- **Gmail** (Testing-mode OAuth) refresh token expires after **7 days** — re-Connect in the Studio if stale.
- **The AP cloudflared tunnel is ephemeral** — when it dies, every flow fails with `INTERNAL_ERROR`;
  fix with `make ap`. This is the #1 "everything stopped firing" cause ([GAPS.md](GAPS.md)).

## What can't be scripted (external, human)

The AP container's first-run admin; the bot/app accounts (Telegram/Discord/Slack) and OAuth apps
(Gmail/GitHub/Box); the watsonx key; and the one-click browser **consent** to connect each integration.
Everything else is `make`.
