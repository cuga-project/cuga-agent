# Setup

Fresh machine → running events platform. The runtime is one command per service; the irreducible
manual part is external accounts (bots, OAuth apps) a human must create. Per-connector guides are in
[setup/](setup/); the ngrok / public-URL setup is in [setup/NGROK.md](setup/NGROK.md).

## Step 0 — external accounts (the real pre-req, do this FIRST)

Everything else is `make`. The one thing no command can do for you is create the bot/app accounts and
OAuth apps at the providers — each gives you a token or a client-id/secret you paste into `.env`.
**Follow the per-connector guide, then fill the matching `.env` keys.** Start only the connectors you
actually want; the platform runs fine with a subset.

| Connector | Guide | You create | `.env` keys |
|---|---|---|---|
| **Slack** | [setup/SLACK.md](setup/SLACK.md) | Slack app (bot token + signing secret; Event Subscriptions URL) | `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET` |
| **Discord** | [setup/DISCORD.md](setup/DISCORD.md) | Discord app + bot; **enable Message Content Intent** | `DISCORD_BOT_TOKEN` |
| **Telegram** | [setup/TELEGRAM.md](setup/TELEGRAM.md) | bot via @BotFather | `TELEGRAM_BOT_TOKEN`, `EVENTS_TELEGRAM_BOT_USERNAME` |
| **Gmail** | [setup/GMAIL.md](setup/GMAIL.md) | Google OAuth app (Testing mode; scopes) | `EVENTS_OAUTH_GMAIL_CLIENT_ID`/`_SECRET` |
| **GitHub** | [setup/GITHUB.md](setup/GITHUB.md) | GitHub OAuth app (scopes `repo`+`admin:repo_hook`) — **not** a PAT | `EVENTS_OAUTH_GITHUB_CLIENT_ID`/`_SECRET` |
| **Box** | [setup/BOX.md](setup/BOX.md) | Box OAuth app *or* a dev token (~60-min) | `EVENTS_OAUTH_BOX_CLIENT_ID`/`_SECRET` or `BOX_DEV_TOKEN` |
| **Google Calendar** | [setup/GOOGLE_CALENDAR.md](setup/GOOGLE_CALENDAR.md) | Google OAuth app (calendar scope) | `EVENTS_OAUTH_GOOGLE_CALENDAR_CLIENT_ID`/`_SECRET` |
| **Pinterest** | [setup/PINTEREST.md](setup/PINTEREST.md) | Pinterest OAuth app | `EVENTS_OAUTH_PINTEREST_CLIENT_ID`/`_SECRET` |
| **YouTube** · **RSS** | [setup/YOUTUBE.md](setup/YOUTUBE.md) · [setup/RSS.md](setup/RSS.md) | *nothing* — public feeds | *(none — show "ready")* |
| **Webhook** | [setup/WEBHOOK.md](setup/WEBHOOK.md) | *(inbound HTTP — just a shared key)* | `EVENTS_WEBHOOK_KEY` |
| **ngrok** *(infra, recommended)* | [setup/NGROK.md](setup/NGROK.md) | reserve a free domain | `EVENTS_NGROK_DOMAIN` |

Index: [setup/README.md](setup/README.md). The OAuth apps (Gmail/GitHub/Box/Calendar/Pinterest) all
use the same **redirect URI** `https://<domain>/api/events/connect/<app>/callback` — set it in each
provider's console, then click **Connect** in the CUGA Studio (Step 5 below) to consent.

## One-time

```bash
# 1. installs (macOS)
brew install uv node podman cloudflared ngrok
podman machine init && podman machine start          # the Linux VM podman needs on macOS

# 2. project
uv sync --python 3.12                                # CUGA deps → .venv (minutes)
scripts/frontend_build.sh                            # build the Studio UI (only if you want the web UI)
cp .env.events.example .env                          # then fill it in (see .env keys below)

# 3. verify the machine is ready (both fail LOUD with the exact fix; make up runs preflight too)
make env-check                                       # .env has the required keys
make preflight                                       # the TOOLS are installed & running
```

### Prerequisites for `make up` (all checked by `make preflight`)

`make up` **runs `make preflight` first**, so a missing tool aborts *before* anything starts — you
get a clear `✗ … — brew install …` line, never a silent or half-started failure. What it checks:

| Need | Why | If missing |
|---|---|---|
| **podman** (or docker), **VM running** | runs Activepieces (app + postgres + redis) | `✗ no container runtime …` / `✗ podman … VM isn't running — podman machine start` |
| **cloudflared** | the AP public tunnel (`AP_FRONTEND_URL`) | `✗ cloudflared missing — brew install cloudflared` |
| **uv** + `.venv` | runs CUGA | `✗ uv missing …` / `✗ no .venv — uv sync --python 3.12` |
| **ngrok** *(only if `EVENTS_NGROK_DOMAIN` is set)* | the stable CUGA public URL | `✗ ngrok missing but EVENTS_NGROK_DOMAIN=… is set — brew install ngrok …` |
| node *(optional)* | building the Studio UI only, not running | `· node missing …` (info, not a failure) |

If `EVENTS_NGROK_DOMAIN` is **unset**, preflight prints a `·` note and `make up` proceeds with an
**ephemeral cloudflared** tunnel (URL changes each run → you re-point Slack/OAuth). ngrok is
recommended precisely to avoid that — see [setup/NGROK.md](setup/NGROK.md).

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
- **Events:** `CUGA_URL` (where the eventing service finds CUGA) and `EVENTS_API_URL` (where CUGA
  finds the eventing service — unset it and CUGA runs standalone), `EVENTS_WORKER_BACKEND=http`,
  `EVENTS_SUPERVISOR=1` (the agent
  model — one `cuga` supervising `supervisor_agents.yaml`; see below), `EVENTS_SEED_AGENTS=1` (seeds
  the demo **users** for identity/permissions — despite the name, the agent fleet it once seeded is
  retired), `EVENTS_DB` (**a `postgresql://` URL** — local dev runs the same engine as the deploy;
  `make pg` starts one. A filesystem path selects SQLite, for the offline suite and a
  zero-infra quickstart only), `GATEWAY_TOKEN`,
  `HOST_CALLBACK_URL=http://host.containers.internal:8100/invoke` (podman host alias — `/invoke`
  lives on the **eventing** service, not on CUGA).
- **Public URL:** `EVENTS_NGROK_DOMAIN` (recommended, above).
- **Channels:** `TELEGRAM_BOT_TOKEN` (+ `EVENTS_TELEGRAM_BOT_USERNAME`), `DISCORD_BOT_TOKEN`, `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET`.
- **Mention gates:** `EVENTS_SLACK_CHAT=mention` / `EVENTS_DISCORD_CHAT=mention` — only @bot
  messages, DMs, and replies-to-the-bot reach chat; armed channel watchers still see everything.
- **Integrations:** `EVENTS_OAUTH_{GMAIL,GITHUB,BOX}_CLIENT_ID`/`_SECRET` (OAuth apps), or `BOX_DEV_TOKEN` for Box direct-poll.
- **Security (set before exposing publicly):** `SLACK_SIGNING_SECRET`, `EVENTS_WEBHOOK_KEY` — unset, those endpoints accept anything.

Two AP gotchas that bite hard:
- **Pick a strong `AP_PASSWORD`** (≥8 chars, mixed case + number + symbol). AP enforces it, and a weak
  one makes the first-boot admin sign-up fail *silently*. On later boots the sign-up returns `HTTP 403`
  — expected, not an error.
- **Do NOT set `AP_WORKER_TOKEN`.** AP mints it as a JWT itself; a raw value crash-loops the worker and
  every flow publish hangs. `ap_up.sh` strips a stale one.

## Bring it up

There are **two services**: CUGA on `:7860` and the eventing service on `:8100`. `make up` brings up
both, plus the Activepieces infra (see [Starting the servers](#starting-the-servers--two-processes)).

```bash
make up                 # AP (container + tunnel) + registry, then BOTH services. AP before CUGA.
make channels           # arm inbound channels for every bot token in .env (idempotent)
make public-url         # prints the public URL + the exact strings to paste into Slack/Gmail consoles
```

**Wire the external consoles once** (stable because of the pinned ngrok URL):
1. **Slack** → Event Subscriptions Request URL `https://<domain>/api/events/slack/events`, subscribe `message.channels`, invite the bot.
2. **Discord** → enable **Message Content Intent**.
3. **OAuth integrations** (Gmail · GitHub · Box · Google Calendar · Pinterest) → set each provider's redirect URI = `https://<domain>/api/events/connect/<app>/callback`, then **connect in the CUGA Studio** (`http://localhost:7860/studio` → **Integrations** or **Setup** tab → **Connect**). GitHub is OAuth (scopes `repo` + `admin:repo_hook`), **not** a pasted PAT. YouTube · RSS need no connection (public feeds — they show "ready"). *(CLI fallback to the Connect button: open `https://<domain>/api/events/connect/<app>`.)*

**Verify:**
```bash
make status      # up + tunnels + channel/integration state   make doctor   # live creds ok
make test        # the offline suite green          make tunnels  # both tunnels reachable
```
Then smoke-test: DM the Telegram bot · @mention the bot in Slack/Discord (mention gates: a plain
channel message is deliberately ignored) · open `localhost:7860/studio`. For a full from-zero
rehearsal, follow **Clean run from zero** below; for exhaustive testing see **Which test do I run**.

## Running WITHOUT Activepieces (the no-AP path)

You do **not** need AP to chat. The events layer runs in tiers — the **NOW/chat trigger works with
zero Activepieces and zero tunnel**:

```bash
make preflight-noap   # minimal tools: uv + .venv only (no podman, no tunnel)
make up-noap          # boot events with NO AP, NO tunnel, then arm channels
```

What's live in this mode: **web · Telegram · Discord** chat (a user asks → the concierge answers →
the reply goes back) **and cron/poll triggers** — those run in-process via the **native scheduler**
(`EVENTS_SCHEDULER=native`, the default), no AP required. What's off until you add AP: the **SaaS
integration push triggers** (Gmail/GitHub/Box). Slack chat additionally needs a public URL (it's the
one inbound channel). The server's boot **capability report** (also `GET /api/events/status`) says
exactly what's available.

Why chat works: three of four channels use an **outbound** transport, so they need no public URL and
no AP — Telegram (long-poll `getUpdates`), Discord (Gateway WebSocket), web (in-process). Slack is the
only **inbound** channel (it POSTs to you), so it alone needs a public URL. Cron/poll are timers/pollers
CUGA runs itself, so they too need no AP.

- **Telegram backend** is a flag, `direct` by default: `EVENTS_TELEGRAM_BACKEND=direct` (long-poll, no
  AP, no tunnel) or `=ap` (the legacy AP webhook flow). Discord/Slack have the same
  `EVENTS_{DISCORD,SLACK}_BACKEND` pair, also `direct` by default.
- **Scheduler backend** is `EVENTS_SCHEDULER`, `native` by default (cron/poll in-process, no AP); set
  `=ap` only to route recurrence through an AP schedule instead.
- The events layer is **triggers-only**: the concierge builds watch/trigger flows and delivers the
  agent's answer — it never runs connector *actions*. Anything the agent should *do* it does through
  its own tools, so no extra credentials live in the event plumbing.
- Per-channel details (tokens, wiring, the inbound-vs-outbound split): the guides in [setup/](setup/).

**On a real deployment (e.g. Code Engine) there's no tunnel at all** — the platform route *is* your
public URL, so even Slack/OAuth work by pointing at the app's route. Two caveats for the outbound
channels: don't scale to zero, and run the poller/Gateway as a single instance (they're persistent
loops; N replicas double-process). Details in the reference doc's cloud section.

## Starting the servers — two processes

The `--events` flag is **gone**, along with the "combined" mode that mounted the events layer onto
CUGA's FastAPI app. There are two services now, and `make` brings up both:

```bash
make up-noap        # BOTH services, no Activepieces, no tunnel  (the usual dev loop)
make up             # BOTH services + Activepieces + tunnels     (the full stack)

# or drive them individually:
cuga start demo     # just CUGA on :7860 — vanilla, no events code, no bot tokens
make run-events     # just the eventing service on :8100 (needs CUGA up; override CUGA_URL=…)
```

**Ports:** CUGA `:7860` (override `CUGA_PORT`), eventing `:8100` (override `EVENTS_PORT`). The
eventing service finds CUGA via `CUGA_URL`; CUGA finds the eventing service via `EVENTS_API_URL` —
and if that is unset, CUGA is exactly upstream CUGA and a slash verb is just text.

**Slack's Request URL points at the EVENTING service (`:8100`), never at CUGA.** "CUGA is the door"
describes where the *decision* happens, one hop later; the receiver did not move.

At startup the eventing service prints a **capability report** — also at `GET /api/events/status`
(`capability` field) — telling you exactly what's live and what still needs infra, each with its
one-line fix:

```
events layer ENABLED — capability report:
  ✓ web chat · webhooks (/api/events/hook/…) · direct watchers (Slack/Discord/Box-direct · Telegram-direct) — no extra infra (Telegram chat runs AP-free via long-poll)
  ✓ supervisor: ON — 27 sub-agent(s) from supervisor_agents.yaml
  ✓ native scheduler ON — cron/poll run in-process (no AP needed); AP is used only for integration (piece) triggers
  ✗ Activepieces not reachable → AP-backed integration triggers (Gmail/GitHub/Box push) unavailable  [cron/poll still work — native scheduler]  (start it: `make up`)
  ✗ no EVENTS_PUBLIC_URL → Slack events, OAuth callbacks unreachable (`make tunnels`, then `make channels`)  [Telegram chat still works — it's direct/outbound]
```

The tiers are real: the eventing service **alone** gives web chat, webhooks, direct watchers,
Telegram/Discord chat, **and cron/poll (native scheduler)** with zero extra infrastructure. Only the
AP-backed integration push triggers (Gmail/GitHub/Box) need Activepieces; Slack events / OAuth
callbacks need a public URL. `make up` provisions both.

> **`make up` / `make up-noap` are the supported way to boot both services.** `cuga start demo` and
> `make run-events` exist for driving one at a time. Everything else in the Makefile is infra and
> tests — `ap`, `tunnels`, `channels`, `test-*`, `doctor`, `report`.

## The agent model (one switch)

There is exactly **one addressable agent — `cuga`**:

- **`EVENTS_SUPERVISOR=1`** (recommended): `cuga` is a **supervisor** whose sub-agents load from
  [`supervisor_agents.yaml`](../supervisor_agents.yaml) at the repo root — CUGA-main's canonical
  schema. It picks the right specialist per wake-up; answers bubble up. **Add/edit a sub-agent =
  edit the YAML + `make reload`.** Routing quality gate: `make test-delegation`.
- **Unset**: `cuga` is the plain classic CUGA agent, exactly as main ships it (one generalist,
  no roster). Everything else (channels, triggers, flows) works identically — flows just wake a
  generalist instead of a supervised specialist.

### Bring your own agents

The `supervisor_agents.yaml` in this repo is an **example roster** (27 demo specialists) — the
platform assumes nothing about it. Point `EVENTS_SUPERVISOR_ROSTER` at *your* roster file (canonical
CUGA-main supervisor schema) and the same event-driven layer serves *your* agents:

```bash
EVENTS_SUPERVISOR=1 EVENTS_SUPERVISOR_ROSTER=rosters/ap_devops.yaml  cuga start demo
```

The roster is loaded by **CUGA**, not by the eventing service — the roster belongs to whoever
executes. CUGA publishes it at `GET /run/agents`, and the eventing service reads it from there.

Nothing in the channels, triggers, flows, or NL→Flow compiler is tied to the demo agents — they
route to whatever sub-agents your roster names.

**Ready-made rosters ship in [`rosters/`](../rosters/README.md)** — drop-in alternatives to the flat
27-agent default, so you can `EVENTS_SUPERVISOR_ROSTER=rosters/<file>.yaml` without authoring one.
Two cuts: **domain families** (`box_document_intelligence`, `repository_intelligence`,
`market_research_intelligence`, …) and an **enterprise test bed split by AP dependency** —
`no_ap_*` rosters (research desk, markets desk, IT helpdesk) run with **zero Activepieces**, `ap_*`
rosters (exec office, DevOps) need AP for their SaaS push triggers. See
[rosters/README.md](../rosters/README.md) for the full table; then `make reload`.

### Adding a sub-agent (builder guide)

A sub-agent is **a skill, not a deployment**: a name, a prompt, and tools. Append a block to your
roster YAML (`supervisor_agents.yaml` by default):

```yaml
  - name: invoice_checker
    special_instructions: |
      You verify invoices: amounts, due dates, duplicate detection. Be terse and factual.
    mcp_servers:
      - name: cuga-text
```

then `make reload`. Three rules:

1. **The NAME is what the supervisor routes on** — so make it descriptive. Its routing prompt lists
   each sub-agent as `name (INTERNAL): Internal agent: <name>`; there is no other description, so a
   vague name is a vague routing signal.

   > Rosters used to carry a `HANDLES TRIGGERS: app/event (…)` line per agent, and this guide used
   > to tell you to write one. They were removed: the supervisor never saw them (they landed in the
   > sub-agent's own prompt, read only *after* routing had picked it) while costing about half the
   > roster's prompt text. **Do not add them back** — an offline gate now fails if any roster does.
   > If a sub-agent should *own* a trigger, declare it in the structured
   > `integrations[].triggers` on its `AgentSpec` in `src/cuga/backend/events/seed.py`, which is
   > machine-readable and is what the connect gate and the tests actually consult.
2. **Channels are NOT per-sub-agent.** Channels (web/Slack/Discord/Telegram) belong to the
   platform: they all converse with the one `cuga` agent. A sub-agent never "joins" a channel;
   answers are delivered by the platform to wherever the conversation or flow originated.
3. **Credentials are NOT per-sub-agent.** Integration connections live in Activepieces at the
   platform level; the connect gate runs at ARM time keyed to the trigger's source app. A
   sub-agent gets event *content*, never a token. Its `mcp_servers` list scopes which TOOLS it
   reasons with — that's the only per-sub-agent capability boundary.

After editing, run `make test-delegation` — the routing gate over the real roster.

### How a message routes (the one rule, every surface)

Web chat, Slack, Discord and Telegram all apply the same rule, in order: a **slash command**
(`/automate` + the mode-forcing `/watch /schedule /cron /poll /push`) goes to the concierge
deterministically — it outranks even armed channel watchers; an utterance the classifier reads as
**standing intent** (cron/poll/push phrasing) takes the concierge's NL→Flow path; a thread with an
**open concierge question** stays with the concierge (slot filling); everything else — plain
conversation — goes **straight to the `cuga` agent** with no concierge LLM hop. Mention gates
(`EVENTS_SLACK_CHAT` / `EVENTS_DISCORD_CHAT` = `mention`) run before any of this; Telegram DMs
need no mention — a private chat is inherently addressed to the bot.

## Clean run from zero (the hand-off rehearsal)

The exact sequence to take a machine from wiped to fully tested — hand this to a new tester and they
follow it top to bottom, no guidance needed. Last rehearsed end-to-end 2026-07-17 (this exact list).

```bash
make fresh         # 1. = nuke (AP volumes + events.db; .env survives) → up → channels.
                   #    ap_up.sh installs the piece catalog itself; if a Connect later 404s with
                   #    piece_metadata_not_found, run `make ap-pieces` once (cold-boot race).
make status        # 2. registry + cuga both 200, 3 containers Up, both tunnel URLs +
                   #    every CHANNEL and INTEGRATION with its connected/ready/connect-needed state
make doctor        # 3. every live cred green — incl. a FRESH BOX_DEV_TOKEN (starts a ~60-min clock)
make test          # 4. the full offline suite (no stack or creds needed — must be all green)
```

5. **Connect the integrations — do this in the CUGA Studio UI.** A nuke wipes AP's connections and
   only a human can consent. **Open the Studio → `http://localhost:7860/studio` → Integrations tab**
   (or the **Setup tab**, where every id/secret, token, and connect/reconnect lives in one place),
   and click **Connect** on each — a browser tab opens the OAuth consent, approve, done.

   | Integration | Studio button | Notes |
   |---|---|---|
   | **Gmail** · **GitHub** · **Box** · **Google Calendar** · **Pinterest** | **Connect** (OAuth) | approve in the popup |
   | **YouTube** · **RSS** | *(none — shows "ready")* | public feeds, no OAuth |

   ⚠ Google shows an **"unverified app"** warning (testing-mode OAuth app): *Advanced → Go to … →
   Allow* → success page. Abandoning mid-consent leaves NO connection (the server log shows the 302
   out to Google but no `…/callback` returns). GitHub is OAuth (scopes `repo` + `admin:repo_hook`),
   **not** a pasted PAT.

   Verify all connected: **Studio → Integrations** (green), or
   `curl -s localhost:8100/api/events/integrations` → gmail · github · box · google_calendar ·
   pinterest = `connected`, youtube · rss = `ready`. *(CLI alternative to the buttons: open
   `https://<domain>/api/events/connect/<app>` directly — but the Studio is the intended path.)*

```bash
make test-ap          # 6. e2e WITH AP — the SaaS integration path (Box/GitHub/Gmail + webhook)
                      #    armed AND FIRED. Green even before step 5: an unconnected integration
                      #    is SKIPPED and named, never a false fail.
make test-report      # 7. THE whole ladder → one persistent, timestamped HTML+MD report (offline ·
                      #    live · flows · matrix · fire · delegation · new-pieces · exhaustive;
                      #    fleet-era rungs auto-skip under EVENTS_SUPERVISOR=1). ~40 min; run right
                      #    after step 5 while the Box token is fresh. Open with make report.
```

To debug a single layer instead of the whole ladder, call a granular rung directly (e.g.
`make test-delegation` for routing, `make test-suite-flows` for NL→Flow, `make test-exhaustive` for
the full agent × trigger matrix) — see [Which test do I run](#which-test-do-i-run-and-when) for the
full map.

### Scheduled flows are single-shot (cadence stripping) — and can be bounded

The scheduler owns recurrence (the native in-process scheduler by default, or an AP schedule when
`EVENTS_SCHEDULER=ap`); the agent runs **once per tick**. At arm time the concierge
rewrites the utterance into its one-run task with an **LLM** (one call per flow, never per tick) —
"watch bitcoin every 5 minutes and ping me on any move" is stored as "Check Bitcoin now and ping me
on any move", wrapped in explicit "this is ONE run — do NOT loop" framing. A regex stripper is the
guarded fallback (used when the LLM is off, unreachable, or its answer still leaks cadence words).
Knobs: `EVENTS_CADENCE_LLM=0` disables the LLM (regex only); `EVENTS_CADENCE_LLM_TIMEOUT` (s,
default 20). Without this, the agent tries to implement the schedule itself (loop + sleep) and hits
the execution timeout.

**Bounded runs**: add "… for one hour" / "for the next 2 days" / "for 30 minutes" and the flow
**ends itself** — the ARMED reply names the stop time, and the first tick past the deadline deletes
the flow and its subscription instead of running (lazy enforcement at `/invoke`: survives server
downtime, overshoots by at most one tick). Word-number cadences ("every five minutes") work too.

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

**Common day-to-day snags (each now prints its own fix):**

| Symptom | Cause | Fix |
|---|---|---|
| `make up` aborts: `ngrok could not start (ERR_NGROK_334) … already online` | a **stale ngrok** from a previous run still holds your reserved domain | `pkill -f 'ngrok http'` then `make up` — or just `make stop` before `make up` (stop already frees it) |
| Connect 404s `piece_metadata_not_found` | fresh-DB piece sync hadn't landed | `make ap-pieces` (self-heals; installs the live catalog version) |
| everything armed but flows fail `INTERNAL_ERROR` / `nodename nor servname` | the **AP cloudflared tunnel died** (it's ephemeral) | `make ap` then `make channels` |
| an integration shows `✗ connect needed` in `make status` | never consented (or a nuke wiped it) | **Connect in the Studio** → `localhost:7860/studio → Integrations` |

Habit that avoids the ngrok clash entirely: **`make stop` before a fresh `make up`** — `stop` kills the
ngrok agent, so the domain is free when `up` re-binds it. `make restart` (= stop + up) does this for you.

## Which test do I run, and when?

"Does it work?" is several questions at very different costs — one 40-minute monolith would just
mean nobody runs tests. **The four blessed targets** (exactly what `make help` surfaces):

| Target | The question it answers | Needs | Cost | Run it when |
|---|---|---|---|---|
| `make test` | **Unit gate** — every endpoint + invariant via TestClient. Code internally correct? | nothing (no stack/creds/AP) | ~15 s | **after every change** — the CI gate |
| `make test-e2e` | **e2e WITHOUT AP** — chat + arm + **FIRE** across channels & native cron/poll | `make up-noap` | ~3–6 min | after touching channels / scheduler / concierge |
| `make test-ap` | **e2e WITH AP** — the SaaS integration path (Box/GitHub/Gmail + webhook): arm + fire | `make up` + `make doctor` | ~3–5 min | after touching the AP / integration path |
| `make test-report` | **The whole ladder → one persistent, timestamped HTML+MD report** | `make up` (full stack) | ~40 min | before a demo/handoff, or a citable "all green" |

A channel or integration **missing its creds is SKIPPED and named** — never a silent pass — so both
e2e targets are safe to run with only a subset connected. `test-e2e` and `test-ap` need *different*
stacks (no-AP vs full-AP), so run each against the matching `make up-noap` / `make up`.

**Testing a deployed app (Code Engine).** Each local target has a CE parallel that points at the
deployed route instead of localhost — **`make test-e2e-ce`** (the channel + native-fire e2e against
the live app), plus ops targets **`make ce-status` · `ce-logs` · `ce-smoke` · `ce-url`**.
`test-e2e-ce` and `ce-smoke` hit the app's public route (no login); `ce-status`/`ce-logs` use the CE
control plane (`ibmcloud login` first). The full deploy + test story is in
[../deploy/ce/README.md](../deploy/ce/README.md).

**Under the hood, `make test-report`** runs the granular rungs and saves the result — `offline ·
live · flows · matrix · fire · delegation · new-pieces · exhaustive` (the fleet-era `now/matrix/fire`
rungs auto-skip under `EVENTS_SUPERVISOR=1`). Call any one directly to debug a single layer:
`make test-delegation` (routing quality — ≥90% gate, supervisor mode, after editing the roster),
`make test-suite-flows` (does an English sentence become the *right* flow?), or `make test-exhaustive`
(every agent × every registry trigger, armed **and** fired, answer-quality gated). `make help` lists
them all. The report lands in `results/runs/<ts>/` + `results/index.html` + `results/LATEST.md` (open
with `make report`). `make doctor` isn't a test — it pings each service with its real `.env` cred and
never fails, only reports.

> **Live-run gotcha:** the AP-backed harnesses (`test-ap`, `test-report`) need AP's public tunnel
> (`AP_FRONTEND_URL`) alive — it's a **cloudflared tunnel baked into the AP container** and
> trycloudflare URLs *flap*. If a run aborts at preflight with `AP_FRONTEND_URL is DEAD`, run
> **`make ap`** to re-bake a fresh tunnel (recreates the AP app container; your flows + OAuth
> connections persist in the DB), then re-run. A dead tunnel also surfaces mid-run as
> `[Errno 8] nodename nor servname` DNS errors.

## Resets — know the difference

| Command | Wipes | Keeps | You must then |
|---|---|---|---|
| `make reset-flows` | `events.db` (your armed flows) | AP connections, pieces, tunnels | nothing — **prefer this** for a clean-slate day |
| `make nuke` | **AP volumes** + `events.db` | nothing | **reconnect every integration** (below) |
| `make fresh` | = `nuke` then `up` + `channels` | nothing | reconnect every integration |

**`make nuke` loses all AP connections.** It's **guided** — when it finishes it prints exactly what to
do next (`→ NEXT: make fresh`, the nuke-safe full cycle). After the rebuild, the OAuth integrations
need a **browser re-consent** (only you can do that) — reconnect them in the **CUGA Studio → Integrations**
(Step 5 above), or hit `https://<domain>/api/events/connect/{gmail,github,box,google_calendar,pinterest}`.
Box direct-poll uses `BOX_DEV_TOKEN` from `.env` (which expires ~60 min — refresh it). Channels re-arm
from `.env` via `make channels`. So reach for `nuke` only for a true from-zero rebuild; `reset-flows`
is the everyday reset.

> The whole setup chain self-navigates: **every command ends by printing its `→ NEXT:` step** —
> `preflight → up → status → doctor → test → (connect in Studio) → test-ap → test-report`, and
> `make fresh` prints the full ordered map. You never have to guess what to run next.

## Perishable creds

- **`BOX_DEV_TOKEN`** expires **~60 min** — refresh it in the Box console before Box tests, then `make reload`.
- **Gmail** (Testing-mode OAuth) refresh token expires after **7 days** — re-Connect in the Studio if stale.
- **The AP cloudflared tunnel is ephemeral** — when it dies, every flow RUN fails with
  `INTERNAL_ERROR` while arming still "works". `make doctor` now detects this exactly (it resolves
  the baked `AP_FRONTEND_URL` and prints the fix); recover with `make ap` then `make channels`.
  This is the #1 "everything stopped firing" cause.

## What can't be scripted (external, human)

The AP container's first-run admin; the bot/app accounts (Telegram/Discord/Slack) and OAuth apps
(Gmail/GitHub/Box); the watsonx key; and the one-click browser **consent** to connect each integration.
Everything else is `make`.
