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
- **Events:** `EVENTS_ENABLED=1`, `EVENTS_WORKER_BACKEND=cuga`, `EVENTS_SUPERVISOR=1` (the agent
  model — one `cuga` supervising `supervisor_agents.yaml`; see below), `EVENTS_SEED_AGENTS=1` (seeds
  the demo **users** for identity/permissions — despite the name, the agent fleet it once seeded is
  retired), `EVENTS_DB=<abs path>.db`, `GATEWAY_TOKEN`,
  `HOST_CALLBACK_URL=http://host.containers.internal:7860/invoke` (podman host alias).
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

The one server command is **`cuga start demo --events`** — same style as `cuga start demo`. `make up`
is just the wrapper that provisions the infra that command needs (Activepieces + tunnels) and then runs
it (see [Starting the server](#starting-the-server--one-entry-point)).

```bash
make up                 # provisions AP (container + tunnel) + registry, then runs `cuga start demo --events`. AP before CUGA.
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
Then smoke-test: DM the Telegram bot · @mention the bot in Slack/Discord (mention gates: a plain
channel message is deliberately ignored) · open `localhost:7860/studio`. For a full from-zero
rehearsal, follow **Clean run from zero** below; for exhaustive testing see **Which test do I run**.

## Starting the server — one entry point

There is ONE command; `make up` is just the infra-provisioning wrapper around it:

```bash
cuga start demo --events          # THE server with the event-driven layer on (bare — no AP/tunnels)
make up                           # provisions Activepieces + MCP registry + tunnels, then runs the
                                  #   SAME `cuga start … --events` server (the full dev stack)
```

`--events` sets `EVENTS_ENABLED=1` and mounts channels, webhooks, standing flows, the concierge and
the Studio events tabs onto the normal CUGA server. **One port everywhere: CUGA's native 7860** —
with or without `--events`, and all events infra (tunnels, channels, AP callbacks, tests) targets
it. Override with `EVENTS_CUGA_PORT` if 7860 is taken. At startup it prints a **capability report** —
also at `GET /api/events/status` (`capability` field) — telling you exactly what's live and what
still needs infra, each with its one-line fix:

```
events layer ENABLED — capability report:
  ✓ web chat · webhooks · direct watchers (Slack/Discord/Box-direct) — no extra infra
  ✓ supervisor: ON — 27 sub-agent(s) from supervisor_agents.yaml
  ✗ Activepieces not reachable → cron/poll + AP-backed triggers unavailable (`make ap`)
  ✗ no EVENTS_PUBLIC_URL → Slack events / OAuth / Telegram unreachable (`make tunnels`)
```

The tiers are real: `--events` **alone** gives web chat, webhooks, and direct watchers with zero
extra infrastructure. AP-backed triggers (cron/poll, Gmail/GitHub/Box) need Activepieces; Slack
events / OAuth / Telegram need a public URL. `make up` provisions both.

> **`make` commands are for INFRA and TESTS only** — `ap`, `tunnels`, `channels`, `test-*`,
> `doctor`, `report`. Server startup is the CLI. There is no second server entry point to maintain.

## The agent model (one switch)

There is exactly **one addressable agent — `cuga`** ([plans/SUPERVISOR_REFACTOR.md](plans/SUPERVISOR_REFACTOR.md)):

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
EVENTS_SUPERVISOR=1 EVENTS_SUPERVISOR_ROSTER=/path/to/my_agents.yaml  cuga start demo --events
```

Nothing in the channels, triggers, flows, or NL→Flow compiler is tied to the demo agents — they
route to whatever your roster's HANDLES lines declare.

### Adding a sub-agent (builder guide)

A sub-agent is **a skill, not a deployment**: a name, a prompt, tools, and routing hints. Append a
block to your roster YAML (`supervisor_agents.yaml` by default):

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
make status        # 2. registry + cuga both 200, 3 containers Up, both tunnel URLs printed
make doctor        # 3. every live cred green — incl. a FRESH BOX_DEV_TOKEN (starts a ~60-min clock)
make test          # 4. the full offline suite (no stack or creds needed — must be all green)
```

5. **Connect Gmail + GitHub in the browser** — a nuke wipes AP's connections and only a human can
   consent. Open `https://<domain>/api/events/connect/gmail` and `…/connect/github`, approve each,
   then `curl -s localhost:7860/api/events/integrations` → `gmail` · `box` · `github` all `connected`.
   ⚠ Google shows an **"unverified app"** warning (testing-mode OAuth app): click *Advanced → Go to …
   → Allow* and continue to the success page. Abandoning mid-consent leaves NO connection — the
   server log shows the 302 out to Google but no `/connect/gmail/callback` ever arrives.

```bash
make test-live        # 6. live smoke — 4 channels + 4 flow modes (green even before step 5,
                      #    since an unconnected integration correctly reports 'connect-needed')
make test-exhaustive  # 7. THE full matrix: every agent · every registry trigger armed AND fired ·
                      #    answer QUALITY gates · REAL/SYNTH/BLOCKED marking · zero-leak cleanup
                      #    gate (~45-75 min; run right after step 5 while the Box token is fresh)
```

8. **Other testing tools** — [checklist.html](checklist.html) is the interactive **manual**
   checklist (80+ items, browser-saved statuses, *Copy report*); **`make test-report`** runs the
   harness ladder (offline · live · flows · delegation — the fleet-era now/matrix/fire rungs
   auto-skip under `EVENTS_SUPERVISOR=1`) and writes the timestamped HTML report to
   `results/index.html`.

### Scheduled flows are single-shot (cadence stripping) — and can be bounded

The AP schedule owns recurrence; the agent runs **once per tick**. At arm time the concierge
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
| `make test-exhaustive` | EVERYTHING: every agent + every trigger armed AND fired, answer-quality gated, REAL/SYNTH/BLOCKED marked, zero-leak cleanup | 45–75 min | before a demo/handoff; after big refactors (plans/EXHAUSTIVE_MATRIX.md) |

Day to day you need the first two. **`make test-report`** runs the ladder in order and writes the
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
- **The AP cloudflared tunnel is ephemeral** — when it dies, every flow RUN fails with
  `INTERNAL_ERROR` while arming still "works". `make doctor` now detects this exactly (it resolves
  the baked `AP_FRONTEND_URL` and prints the fix); recover with `make ap` then `make channels`.
  This is the #1 "everything stopped firing" cause ([GAPS.md](GAPS.md)).

## What can't be scripted (external, human)

The AP container's first-run admin; the bot/app accounts (Telegram/Discord/Slack) and OAuth apps
(Gmail/GitHub/Box); the watsonx key; and the one-click browser **consent** to connect each integration.
Everything else is `make`.
