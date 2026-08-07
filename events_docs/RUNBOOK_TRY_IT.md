# Try it — local and deployed

Everything below is what actually shipped in this branch: HITL arming (nothing arms until you
approve the exact prompt), CUGA preloaded with a supervisor roster, and the eventing layer as its own
service. Full design record: `events_docs/plans/SPLIT_AND_HITL_ARMING_SPEC.md`.

**Want the picture first?** Open **[`DECK.html`](DECK.html)** in a browser — architecture diagram,
the NL→Flow lifecycle, why Activepieces is in but off, the blast-radius numbers, and the roadmap.

---

## The topology

**There is exactly one topology: two services, and CUGA is the front door.**

Every channel utterance — Slack, Discord, Telegram, web — lands on **CUGA's `POST /run`**. CUGA
applies one rule and calls the eventing service only when the message is actually about eventing:

```
slash verb (/automate /watch /schedule /cron /poll /push /cancel)   → eventing
this thread already has an arming dialogue open ("yes", "change …") → eventing
everything else                                                     → the agent
```

The channel adapters live in the eventing service because they own the sockets and the bot tokens,
but they make no routing decision — they normalise a message, hand it to `/run`, and post the answer
back. The eventing service also owns triggers, the scheduler and the concierge, and executes every
fire by calling `/run`.

The old "combined" mode (events mounted onto CUGA's FastAPI app) is removed: CUGA carries no events
code, no scheduler, no channel loops and no bot tokens.

| | CUGA (agent + UI) | eventing service (the front door for events) |
|---|---|---|
| **Local** | http://localhost:7860 | http://localhost:8100 |
| **Code Engine** | https://cuga-core.1gxwxi8kos9y.us-east.codeengine.appdomain.cloud | https://cuga-events-svc.1gxwxi8kos9y.us-east.codeengine.appdomain.cloud |

`make up-noap` starts **both**. **UI lives at `/studio` on CUGA**; it calls the eventing service
cross-origin via `EVENTS_API_URL`.

### One caveat worth knowing before you start

**Only one process at a time may serve a given Slack app or long-poll a given Telegram bot.** All
three deployments share the bot tokens in `.env`, so running local while CE is up gives Telegram
`409 Conflict` and split-brain on Slack. Stop one before exercising inbound chat on the other.
Outbound delivery (the fire step) is unaffected — that's why the harnesses pass either way.

**CE tool servers scale to zero.** The first call that needs a cold `cuga-*` MCP server can exceed
the webhook's internal timeout and come back `502`. Warm, the same call returns in ~30s. If a first
run shows one webhook failure, re-run before believing it.

---

## 1. Local — the 10-minute pass

```bash
make pg                      # the events DATABASE — PostgreSQL 16 in a container (first time only)
make up-noap                 # BOTH services: CUGA :7860 + eventing :8100, no Activepieces
make test                    # offline suite — run it with the stack DOWN (see below)
make test-pg                 # the store tests against REAL PostgreSQL (the deployed SQL path)
make test-e2e                # REAL channels + native cron/poll fire
```

### The database — why `make pg` comes first

`EVENTS_DB` takes a **`postgresql://` URL**, and local dev runs the **same engine as Code Engine**.
That is deliberate. Local used to be a SQLite file that survives everything while the deployment ran
SQLite on an ephemeral disk — two different durability stories, and the fragile one was the only one
nobody exercised. A pod replacement on 2026-08-05 silently deleted a cron armed from Slack twelve
minutes earlier, and no local test could have caught it, because locally that failure mode did not
exist.

| Command | Does |
|---|---|
| `make pg` | start Postgres 16 (podman, `:5433`) and print the DSN — idempotent |
| `make pg-psql` | a `psql` shell on the local events DB |
| `make pg-stop` | stop it, keep the data |
| `make pg-reset` | **destroy** and recreate — drops every locally armed flow |
| `make test-pg` | 20 store tests against the real engine |

Put the printed DSN in `.env` as `EVENTS_DB`. There is **no Postgres installed on your machine** —
it is a container (`cuga-events-pg`), and the data lives in a podman volume, so `podman rm` without
`-v` keeps it.

SQLite still works (`EVENTS_DB=/abs/path/events.db`) and is what the hermetic offline suite uses, but
it is **not** what we deploy — see [ARCHITECTURE.md](ARCHITECTURE.md) §7.

`make test` is hermetic — `tests/events/conftest.py` points every loopback seam at a closed port
for the session, so it behaves identically whether or not a dev stack is running. (It did not use
to: with the stack up those inner calls hit a real server and a 50-second suite took 25+ minutes.)

Confirm you got the roster and its tools before testing anything else:
```bash
curl -s localhost:7860/api/apps | jq '.apps[].name'          # seven cuga_* servers
curl -s localhost:8100/api/events/status -H "X-Gateway-Token: $TOK" \
  | jq -r '.capability[1]'      # supervisor on CUGA (…) — 8 sub-agent(s): pricebot, …
```

**Try HITL by hand** — open http://localhost:7860/studio → Concierge tab, and type:

```
/automate every 5 minutes send IBM stock price
```
You get a **confirm card**, not an armed flow. It shows the exact prompt the agent will be handed
every fire. Then:
- **Arm it** → armed (check the Flows tab)
- **Edit prompt** → prefill, change it, send → re-confirms with your text
- **Cancel** → nothing armed

The same dialogue works in the main chat box and on every channel.

**Try it on Slack** (the whole story on one channel):
```
@your-bot /automate every minute send me the price of bitcoin
```
The bot replies **in a thread** with the confirm card. Reply `yes` **in that thread** — no new
@mention needed, because the bot rooted the thread. A minute later the tick is delivered back into
that same thread.

Automated version of exactly that:
```bash
.venv/bin/python tests/events/live_fire.py --only channel-arm
```

**Starting them by hand** (what `make up-noap` does for you):
```bash
# 1. CUGA as the supervisor. The roster must be here: this is where execution happens.
CUGA_SUPERVISOR_ROSTER=supervisor_agents.yaml \
MCP_SERVERS_FILE=src/cuga/backend/tools_env/registry/config/mcp_servers_cuga_apps.yaml \
  .venv/bin/cuga start demo                    # :7860

# 2. The eventing service beside it (EVENTS_DB comes from .env — make pg prints it)
CUGA_URL=http://localhost:7860 make run-events # :8100

make test-e2e
```
Check the seam before running anything else — it should list `cuga` plus every sub-agent:
```bash
curl -s localhost:7860/run/agents -H "X-Gateway-Token: $TOK" | jq '.agents[].name'
curl -s localhost:8100/api/events/agents -H "X-Gateway-Token: $TOK" | jq '.agents|length'   # 9
```
If the second number is 1, the eventing service could not read CUGA's roster (usually a token
mismatch) and fell back to a stale local row.

---

## 2. Deployed (Code Engine)

```bash
ibmcloud login --sso          # region us-east, group routing
make ce-status                # revision, env, capability report
make ce-smoke                 # status + channels + one authenticated web-chat turn
make test-e2e-ce              # the SAME channel + fire e2e against the deployed app
make ce-logs GREP=launched    # expect: "events service: launched N background task(s)"
```

**First-time deploy** needs the database provisioned once — details in
[`deploy/ce/README.md`](../deploy/ce/README.md):
```bash
cd deploy/ce && YES=1 ./4_postgres.sh    # managed PostgreSQL + DSN into the CE secret (billable)
make ce-build && make ce-deploy
```

**Two things that fail silently** — `2_deploy.sh` now asserts both after every deploy, and you should
read them:
```
== post-deploy checks ==
  ✓ roster: 9 agents on cuga-core              # 1 agent → every fire runs the bare default agent
  ✓ durability: PostgreSQL — an instance replace is a non-event
```
Check durability any time:
```bash
curl -s "$URL/api/events/status" -H "X-Gateway-Token: $TOK" | jq .durability
# {"durable": true, "backend": "postgres", "mechanism": "database"}
```
If that ever reads `"durable": false`, armed flows are one instance-replace away from being deleted
— and Code Engine records **no restart** when it replaces an instance, so nothing will tell you.

**HITL against the deployed app** (`$TOK` = `GATEWAY_TOKEN` from `.env`):
```bash
URL=https://cuga-events-svc.1gxwxi8kos9y.us-east.codeengine.appdomain.cloud
curl -s -X POST $URL/api/concierge -H 'Content-Type: application/json' \
     -H "X-Gateway-Token: $TOK" \
     -d '{"text":"/automate every 5 minutes send IBM stock price","thread_id":"t1"}'
# → {"state":"confirm","summary":{"trigger":"every 5 minute(s)","prompt":"IBM stock price.",...}}
curl -s -X POST $URL/api/concierge ... -d '{"text":"yes","thread_id":"t1"}'
# → {"state":"armed",...}
```
**Clean up anything you arm** — it fires forever: Studio → Flows → delete, or
`curl -X DELETE $URL/api/events/subscriptions/<id> -H "X-Gateway-Token: $TOK"`.

**Rebuild / redeploy:**
```bash
make ce-build          # cloud buildrun → ICR   (needed after ANY code or roster change)
make ce-deploy         # both apps (cuga-core + cuga-events-svc), one image
```

---

## 3. Redirect / callback URLs — what to update, and when

> ⚠️ **Slack's Request URL points at the EVENTING SERVICE (`cuga-events-svc`), never at CUGA.**
> "CUGA is the door" describes where the *decision* happens, one hop later and invisible to Slack.
> The *receiver* — the webhook route, the bot token, the signature check — lives in the eventing
> service and did not move. Point Slack at `cuga-core` and it answers **405** for that path (its SPA
> catch-all takes GET, not POST), which Slack reports as *"Your request URL returned an HTTP error"*.
>
> ```
> https://cuga-events-svc.…/api/events/slack/events     ← correct (note the -svc)
> https://cuga-core.…/api/events/slack/events           ← 405, verification fails
> ```
>
> To check the endpoint yourself before blaming Slack — it must echo the challenge:
> ```bash
> curl -s -X POST "$EVENTS/api/events/slack/events" -H 'Content-Type: application/json' \
>      -d '{"type":"url_verification","challenge":"probe"}'
> # → probe
> ```


Only **inbound push** channels need a URL. They point at whichever app serves `/api/events/*`.

| | Needs a URL? | Set it to |
|---|---|---|
| **Slack** | **YES** — Event Subscriptions Request URL | `<events-app>/api/events/slack/events` |
| **Discord** | no — outbound Gateway (websocket) | — |
| **Telegram** | no — long-poll | — |
| **Web** | no | — |
| **OAuth connects** (Box/Gmail/GitHub via AP) | yes, per provider | `<events-app>/connect/<app>/callback` |

**When to change it:**
- local → CE, or CE → local: yes
- the events app is `cuga-events-svc` — CUGA (`cuga-core`) never serves a channel callback
- redeploying the same app: no, CE URLs are stable across delete/recreate

The app prints the exact URL at the end of every deploy, and `make ce-status` shows it.
Only one process may serve a given Slack app / long-poll a given Telegram bot at a time — running
local and CE against the same bot gives `409 Conflict` on Telegram and split-brain on Slack.

---

## 4. Rosters — swapping the agent set

| File | Agents |
|---|---|
| `supervisor_agents.yaml` | **8** — the core: one per capability, all MCP servers |
| `rosters/supervisor_agents_extras.yaml` | 19 — research/media/travel/ops variations |
| `rosters/supervisor_agents_full.yaml` | 27 — the original example |

Whatever roster you write, **every registry trigger must be claimed by some agent's
structured `integrations[].triggers`** in `events/seed.py` — that is where trigger ownership lives
event. `tests/events/test_supervisor_roster.py` enforces it; the 8-agent roster covers all of them
by giving `pr_reviewer` the `github/*` repo events, `incident_triage` the rest of
slack/discord/box/gmail/calendar, and `webpage_summarizer` the URL-shaped ones (rss, youtube,
pinterest). An unclaimed trigger is not a crash — it is the supervisor guessing, silently.

```bash
CUGA_SUPERVISOR_ROSTER=rosters/supervisor_agents_full.yaml    # set on CUGA — that is where execution happens
```
`CUGA_SUPERVISOR_ROSTER` also switches the tool registry into FILE mode, so the roster's MCP
servers are actually served. A roster that fails to load returns a 500 naming the file rather than
silently falling back to a tool-less default agent.

---

## 5. Where it stands

| | e2e / flows | fire | notes |
|---|---|---|---|
| **Local** | 30 ✓ · 2 ✗ · 2 – | **2/2** cron+poll · **Slack round trip PASS** | both ✗ = "Activepieces is not running" |
| **Code Engine** | 28 ✓ · 1 ✗ · 5 – | cron ✓ · poll ✓ | the ✗ is a cold start — re-run and it passes |

Offline suite: **347 passed in ~70s**, and hermetic — it no longer matters whether a dev stack is
running.

### One safety property worth knowing how to check

Nothing may arm without a human confirming. The gate is enforced in two places — CUGA's door
(`_SLASH_VERBS`, "is this arming?") and the concierge (`_slash_parse`, "which verb?") — and if the
door is ever MORE permissive than the parser, the extra utterances fall through to the NL path,
**which arms directly with no card**. That happened once (a mention-prefixed `/automate`), and is
now pinned by `test_the_door_and_the_concierge_agree_on_what_a_slash_command_is`.

To check it by hand, send the awkward shape and look for the card, not an "Armed" line:

```bash
curl -s -X POST $CUGA/run -H "X-Gateway-Token: $TOK" -H 'Content-Type: application/json' \
  -d '{"query":"<@U0BFR0NS7ME> /automate every minute send me the price of bitcoin",
       "thread_id":"gw:slack:CTEST#1",
       "channel":{"name":"slack","native_id":"CTEST","user":"U1"}}' | jq -r .answer | head -2
# → **Ready to arm — check this first.**      ← correct
# → Armed poll for cuga …                      ← THE GATE LEAKED
```

**The two remaining local failures are both "AP is down"**, which is the state you asked for. CE
reports the same condition as *skips* because AP is deliberately unconfigured there rather than
configured-but-unreachable.

**CE cold start.** CE tool servers scale to zero. The first call that needs a cold `cuga-*` server
can exceed the webhook's internal timeout and return `502`. Warm, the same routed webhook answers in
~28s with a real review. If a first run shows one webhook failure, re-run before believing it.

### Proving the door, deployed

```bash
C=https://cuga-core.1gxwxi8kos9y.us-east.codeengine.appdomain.cloud
run(){ curl -s -X POST $C/run -H "X-Gateway-Token: $TOK" -H 'Content-Type: application/json' \
        -d "{\"query\":\"$1\",\"thread_id\":\"gw:slack:CDEMO#1\",\"channel\":{\"name\":\"slack\",\"native_id\":\"CDEMO\",\"user\":\"U1\"}}"; }
run "what is the capital of France?"                  # routed_to absent  → the agent
run "/automate every 5 mins check ibm stock price"    # routed_to: events → confirm card
run "yes"                                             # routed_to: events → ARMED … → slack
run "what is 2+2?"                                    # routed_to absent  → the agent
```

**Delete anything you arm** — it fires forever.
