# Try it — local and deployed

Everything below is what actually shipped in this branch: HITL arming (nothing arms until you
approve the exact prompt), CUGA preloaded with a supervisor roster, and the optional split into two
services. Full design record: `events_docs/plans/SPLIT_AND_HITL_ARMING_SPEC.md`.

**Want the picture first?** Open **[`DECK.html`](DECK.html)** in a browser — architecture diagram,
the NL→Flow lifecycle, why Activepieces is in but off, the blast-radius numbers, and the roadmap.

---

## The three deployments

| | URL / command | What it is |
|---|---|---|
| **Local (combined)** | `make up-noap` → http://localhost:7860 | one process: CUGA + eventing. **Start here.** |
| **CE (combined)** | https://cuga-events.1gxwxi8kos9y.us-east.codeengine.appdomain.cloud | the supported deployment |
| **CE (split)** | core: https://cuga-core.1gxwxi8kos9y.us-east.codeengine.appdomain.cloud · events: https://cuga-events-svc.1gxwxi8kos9y.us-east.codeengine.appdomain.cloud | two services, see §5 |

**UI lives at `/studio`** on the combined apps, and on **cuga-core** in the split (it calls the
events service cross-origin via `EVENTS_API_URL`).

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
make up-noap                 # CUGA + eventing on :7860, no Activepieces
make test                    # offline suite — run it with the stack DOWN (see below)
make test-e2e                # REAL channels + native cron/poll fire
```

⚠️ **Run `make test` with nothing listening on :7860.** The offline tests fire webhooks at
`127.0.0.1:$EVENTS_CUGA_PORT` (default 7860). With the stack up, those requests reach the live
server, and a suite that should take a couple of minutes spends them on real LLM calls.

Confirm you got the roster and its tools before testing anything else:
```bash
curl -s localhost:7860/api/apps | jq '.apps[].name'          # seven cuga_* servers
curl -s localhost:7860/api/events/status -H "X-Gateway-Token: $TOK" \
  | jq -r '.capability[1]'                                   # supervisor: ON — 8 sub-agent(s)
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

**Local split** (two processes — CUGA *without* `--events`, eventing beside it):
```bash
# 1. CUGA as the supervisor. The roster must be here: this is where execution happens.
CUGA_SUPERVISOR_ROSTER=supervisor_agents.yaml \
MCP_SERVERS_FILE=src/cuga/backend/tools_env/registry/config/mcp_servers_cuga_apps.yaml \
  .venv/bin/cuga start demo                    # :7860

# 2. The eventing service beside it
CUGA_URL=http://localhost:7860 make run-events # :8100

make test-e2e-split
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
make ce-logs GREP=launched    # expect: "events: launched N background task(s)"
```

**HITL against the deployed app** (`$TOK` = `GATEWAY_TOKEN` from `.env`):
```bash
URL=https://cuga-events.1gxwxi8kos9y.us-east.codeengine.appdomain.cloud
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
make ce-deploy         # combined
make ce-deploy-split   # two apps (cuga-core + cuga-events-svc)
```

---

## 3. Redirect / callback URLs — what to update, and when

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
- combined → **split**: **yes** — the events app changes from `cuga-events` to `cuga-events-svc`
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
`HANDLES TRIGGERS:` line** — that is how the supervisor knows which specialist owns an inbound
event. `tests/events/test_supervisor_roster.py` enforces it; the 8-agent roster covers all of them
by giving `pr_reviewer` the `github/*` repo events, `incident_triage` the rest of
slack/discord/box/gmail/calendar, and `webpage_summarizer` the URL-shaped ones (rss, youtube,
pinterest). An unclaimed trigger is not a crash — it is the supervisor guessing, silently.

```bash
CUGA_SUPERVISOR_ROSTER=rosters/supervisor_agents_full.yaml    # CUGA preloaded (split / /run)
EVENTS_SUPERVISOR_ROSTER=rosters/supervisor_agents_full.yaml  # events runtime (combined)
```
`CUGA_SUPERVISOR_ROSTER` also switches the tool registry into FILE mode, so the roster's MCP
servers are actually served. A roster that fails to load returns a 500 naming the file rather than
silently falling back to a tool-less default agent.

---

## 5. Split status — verified, not experimental

| | e2e / flows | fire |
|---|---|---|
| Local combined | 30 ✓ · 2 ✗ (AP down) · 2 – | 2/2 |
| Local split | 30 ✓ · 2 ✗ (AP down) · 2 – | 2/2 |
| CE combined | 16 ✓ · **0 ✗** · 5 – | 2/2 |
| CE split | 29 ✓ · **0 ✗** · 5 – | **2/2** |

Offline suite: **330 passed in 46s** (stack down).

Every remaining local failure is *"Activepieces is not running"*. CE reports those as **skips**
rather than failures because AP is deliberately unconfigured there, versus configured-but-down
locally — same condition, different honest label. Green across the board: transport, UI wiring,
CORS, the preloaded supervisor, sub-agent tools, all three webhook modes, and cron/poll ticks
across the HTTP hop.

The two topologies now describe the roster identically because they ask the same question. CUGA
publishes what it has loaded at **`GET /run/agents`** (the machine sibling of `/run`, same shared
secret), and the eventing service resolves every agent against it:

```bash
curl -s $CUGA/run/agents -H "X-Gateway-Token: $TOK"     # cuga + its sub-agents, with mcp_servers
```

If the events side ever reports one agent while CUGA has a roster loaded, that call is failing —
check the token first.

**Combined remains the default and the one to reach for.** Split earns its keep when you want CUGA
and the eventing layer to scale, deploy or hold secrets separately.

Both split apps run at `min-scale 1` (always warm, always billing):
```bash
ibmcloud ce app delete -n cuga-core -n cuga-events-svc
```
