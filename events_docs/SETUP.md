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
make test        # the offline suite green          make tunnels  # both tunnels reachable
```
Then smoke-test: DM the Telegram bot · post in Slack/Discord · open `localhost:8100/studio`. For a
full from-zero rehearsal, follow **Clean run from zero** below; for exhaustive manual testing use the

## The agent model (one switch)

There is exactly **one addressable agent — `cuga`** ([plans/SUPERVISOR_REFACTOR.md](plans/SUPERVISOR_REFACTOR.md)):

- **`EVENTS_SUPERVISOR=1`** (recommended): `cuga` is a **supervisor** whose sub-agents load from
  [`supervisor_agents.yaml`](../supervisor_agents.yaml) at the repo root — CUGA-main's canonical
  schema. It picks the right specialist per wake-up; answers bubble up. **Add/edit a sub-agent =
  edit the YAML + `make reload`.** Routing quality gate: `make test-delegation`.
- **Unset**: `cuga` is the plain classic CUGA agent, exactly as main ships it (one generalist,
  no roster). Everything else (channels, triggers, flows) works identically — flows just wake a
  generalist instead of a supervised specialist.

### Adding a sub-agent (builder guide)

A sub-agent is **a skill, not a deployment**: a name, a prompt, tools, and routing hints. Append a
block to `supervisor_agents.yaml`:

```yaml
  - name: invoice_checker
    special_instructions: |
      You verify invoices: amounts, due dates, duplicate detection. Be terse and factual.
      HANDLES TRIGGERS: gmail/new_attachment (An email with an attachment)
    mcp_servers:
      - name: cuga-text
```

then `make reload`. Three rules:

1. **The HANDLES line is how the supervisor routes to it** — name the `app/event` pairs from the
   trigger registry verbatim. The offline gates fail the build if a registry trigger is claimed by
   nobody, or a HANDLES hint points at a trigger that doesn't exist.
2. **Channels are NOT per-sub-agent.** Channels (web/Slack/Discord/Telegram) belong to the
   platform: they all converse with the one `cuga` agent. A sub-agent never "joins" a channel;
   answers are delivered by the platform to wherever the conversation or flow originated.
3. **Credentials are NOT per-sub-agent.** Integration connections live in Activepieces at the
   platform level; the connect gate runs at ARM time keyed to the trigger's source app. A
   sub-agent gets event *content*, never a token. Its `mcp_servers` list scopes which TOOLS it
   reasons with — that's the only per-sub-agent capability boundary.

After editing, run `make test-delegation` — the routing gate over the real roster.

interactive [checklist.html](checklist.html); the automated report + live harnesses are in [TESTING.md](TESTING.md).

## Clean run from zero (the hand-off rehearsal)

The exact sequence to take a machine from wiped to fully tested — hand this to a new tester and they
follow it top to bottom, no guidance needed. A fresh AP boot needs `ap-pieces` (step 3) and the two
browser consents (step 8) that a nuke wipes; everything else is the commands above, in order.

```bash
make nuke          # 1. wipe AP volumes + .events.db (keeps .env, so your tokens/keys survive)
make up            # 2. AP (container + tunnel) then CUGA (registry + tunnel + server)
make ap-pieces     # 3. a fresh AP boots WITHOUT the schedule piece — install the catalog (run once;
                   #    if 'schedule' still shows missing, run it a second time — a cold-boot race)
make channels      # 4. arm inbound channels from .env bot tokens
make status        # 5. registry + cuga both 200, 3 containers Up, both tunnel URLs printed
make doctor        # 6. every live cred green — incl. a FRESH BOX_DEV_TOKEN (starts a ~60-min clock)
make test          # 7. the full offline suite (no stack or creds needed — must be all green)
```

8. **Connect Gmail + GitHub in the browser** — a nuke wipes AP's connections and only a human can
   consent. Open `https://<domain>/api/events/connect/gmail` and `…/connect/github`, approve each,
   then `curl -s localhost:8100/api/events/integrations` → `gmail` · `box` · `github` all `connected`.

```bash
make test-live     # 9. live smoke — 4 channels + 4 flow modes (green even before step 8,
                   #    since an unconnected integration correctly reports 'connect-needed')
```

10. **Then test exhaustively** — two complementary tools:
    - **[checklist.html](checklist.html)** — an interactive, hand-off-ready **manual** checklist: 80+ items
      spanning every agent (web/Telegram/Discord/Slack chat, CRON/POLL/PUSH arming, Gmail/Box/GitHub,
      webhooks), each with a pass/fail/skip status saved in the browser and a *Copy report* button. An
      editable "environment" panel templates your URL/channel/repo into every command. Open it directly,
      or serve it: `cd events_docs && python3 -m http.server 8899` → `localhost:8899/checklist.html`.
    - **`make test-report`** — the **automated** counterpart: runs every harness (offline · live · now ·
      flows · matrix · fire) and writes a timestamped HTML report to `results/index.html` (~40 min; needs
      Gmail/GitHub connected and a fresh Box token, so start it right after step 8).

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

## Which test do I run, and when?

Six targets because "does it work?" is six different questions at six different costs — one
40-minute monolith would just mean nobody runs tests. Each rung names the layer that broke.

| Target | The question it answers | Cost | Run it when |
|---|---|---|---|
| `make test` | Is the code internally correct? (no stack, no creds) | 30 s | **after every change** |
| `make test-live` | Is the running stack plumbed? one probe per channel + flow mode | 2 min | after touching the stack / `.env` / a reload |
| `make test-suite-now` | Can each agent actually do its job? | 14 min | **fleet-era** — auto-skipped under `EVENTS_SUPERVISOR=1` (asserts per-agent names; ROADMAP §not-yet-vetted) |
| `make test-suite-flows` | Does an English sentence become the *right* AP flow? | 6 min | after touching the concierge / classifier / registry |
| `make test-matrix` | Is every trigger × sink combination wired? | 6 min | **fleet-era** — auto-skipped under `EVENTS_SUPERVISOR=1` |
| `make test-fire` | Does an armed flow genuinely FIRE on a real tick? | 9 min | **fleet-era** — auto-skipped under `EVENTS_SUPERVISOR=1` (the GitHub triggers harness covers real fires there) |
| `make test-delegation` | Does the supervisor pick the right sub-agent? (≥90% gate) | 10 min | supervisor mode only; after editing `supervisor_agents.yaml` |

Day to day you need the first two. **`make test-report`** runs all six in order and writes the
timestamped HTML report (`results/index.html`) — the one command for a handoff or a citable result.
`make doctor` isn't a test — it pings each service with its real `.env` cred and never fails, only
reports. Full reference (verdict vocabulary, what each harness can and cannot prove, the live
GitHub/Slack harnesses): [TESTING.md](TESTING.md).

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
