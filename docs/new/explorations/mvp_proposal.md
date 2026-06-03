# MVP proposal — Slack-only, chat-driven CUGA

## Scope

- **One channel adapter**: Slack (inbound = Socket Mode; outbound = `slack.post`, `slack.react`).
- **One UI**: existing CUGA chat (no per-app web UI).
- **One daemon**: cron + pull + push triggers, dispatcher, per-agent inboxes, Invoker.
- **Three additional tool adapters** for the chat-driven examples: `stock`, `arxiv`, `http` (for `/healthz`).

That's the entire surface area for the MVP.

---

# Part A — Slack as inbound + outbound (3 examples)

These all read from Slack *and* write to Slack. No other channels involved.

## A1. CRON · Daily standup prompt

> *"Every weekday at 9:30am, post the standup template in `#eng-standup`."*

| | |
|---|---|
| **Trigger type** | **CRON** |
| **Inbound channel** | (none — clock) |
| **Outbound channel** | `slack://acme-ws/#eng-standup` |
| **Tools** | `slack.post` |
| **Registry row (sketch)** | `trigger: { kind: cron, cron: "30 9 * * 1-5" }` · `target_agent: standup_poster` · `outcomes: [{ slack.post, channel: #eng-standup, template: ... }]` |
| **What the daemon sees on fire** | `Event { target: standup_poster, payload: { trigger_time } }` |

## A2. PULL · Find unresolved threads

> *"Every 10 minutes, scan `#alerts` for threads older than 30 min without a reply, and ping the on-call user in the same channel."*

| | |
|---|---|
| **Trigger type** | **PULL** (poll + state-diff) |
| **Inbound channel** | `slack://acme-ws/#alerts` (read via `slack.history`) |
| **Outbound channel** | `slack://acme-ws/#alerts` (same) |
| **Tools** | `slack.history`, `slack.post` |
| **State persisted** | `poller_state.seen_ids` — thread ts already pinged |
| **Registry row (sketch)** | `trigger: { kind: pull, channel: slack://…/#alerts, query: "unresolved>30m", interval: 10m, state_key: alerts-watch }` · `target_agent: oncall_ping` |
| **What the daemon sees on fire** | `Event { target: oncall_ping, payload: { thread_ts, original_message, age_minutes } }` — one Event per newly stale thread |

## A3. PUSH · Triage on incoming DM

> *"When someone DMs me on Slack, classify it as bug/billing/other and post a triage note to `#triage-log`."*

| | |
|---|---|
| **Trigger type** | **PUSH** |
| **Inbound channel** | `slack://acme-ws/@me` (Socket Mode delivers DMs) |
| **Outbound channel** | `slack://acme-ws/#triage-log` |
| **Tools** | `slack.post` |
| **Registry row (sketch)** | `trigger: { kind: push, channel: slack://…/@me, event: message.im }` · `target_agent: dm_triage` · `outcomes: [{ slack.post, channel: #triage-log }]` |
| **What the daemon sees on fire** | `Event { target: dm_triage, payload: { sender, text, ts, channel_id } }` |

---

# Part B — Chat-initiated, more tools, Slack as outbound (3 examples)

User starts in the chat UI, expresses the intent in English, CUGA classifies → standing intent → row written → trigger begins firing. Output always lands in Slack.

## B1. PULL · Stock price alerts

> *"Watch AAPL and NVDA. If either drops more than 3% intraday, ping me in `#trading-alerts`."*

| | |
|---|---|
| **Trigger type** | **PULL** |
| **Inbound channel** | (none — outbound `stock.intraday` calls; daemon initiates) |
| **Outbound channel** | `slack://acme-ws/#trading-alerts` |
| **Tools** | `stock.quote`, `stock.intraday`, `slack.post` |
| **State persisted** | `poller_state` — `{fired_today_set, last_prices}` — to avoid re-paging on every poll |
| **Registry row (sketch)** | `trigger: { kind: pull, source: stock, query: "AAPL,NVDA", interval: 5m, state_key: trading-watch-anu, schedule_window: "09:30-16:00 ET" }` · `target_agent: stock_alert` |
| **What the daemon sees on fire** | `Event { target: stock_alert, payload: { ticker, current_price, intraday_pct } }` — one Event per ticker that newly crossed −3% |
| **From cuga-apps** | `stock_alert` |

## B2. CRON · Weekly arXiv paper scout

> *"Every Monday 9am, find new arXiv papers on LLM agents from last week, rank them, and post the top 5 to `#research-feed`."*

| | |
|---|---|
| **Trigger type** | **CRON** |
| **Inbound channel** | (none — clock; agent calls `arxiv.search` mid-turn) |
| **Outbound channel** | `slack://acme-ws/#research-feed` |
| **Tools** | `arxiv.search`, `web.fetch`, `slack.post` |
| **Registry row (sketch)** | `trigger: { kind: cron, cron: "0 9 * * 1" }` · `target_agent: paper_scout` · `prompt: "search arxiv for ..."` |
| **What the daemon sees on fire** | `Event { target: paper_scout, payload: { trigger_time, query: "LLM agents" } }` |
| **From cuga-apps** | `paper_scout` |

## B3. PULL · Production health monitor

> *"Hit `https://prod-api.acme.com/healthz` every 2 minutes. If it fails twice in a row, ping `#oncall` in Slack."*

| | |
|---|---|
| **Trigger type** | **PULL** |
| **Inbound channel** | `https://prod-api.acme.com/healthz` (HTTP GET) |
| **Outbound channel** | `slack://acme-ws/#oncall` |
| **Tools** | `http.get`, `slack.post` |
| **State persisted** | `poller_state` — `{consecutive_failures, last_status}` |
| **Registry row (sketch)** | `trigger: { kind: pull, source: https://prod-api/healthz, interval: 2m, state_key: prod-healthz }` · `target_agent: server_monitor` |
| **What the daemon sees on fire** | `Event { target: server_monitor, payload: { url, status_code, latency_ms, consecutive_failures } }` — only emitted on the 2nd failure |
| **From cuga-apps** | `server_monitor` |

---

# Classification summary

| #  | Example                       | CRON | PULL | PUSH |
| -- | ----------------------------- | :--: | :--: | :--: |
| A1 | Daily standup prompt          | ✓    |      |      |
| A2 | `#alerts` unresolved scan     |      | ✓    |      |
| A3 | DM triage                     |      |      | ✓    |
| B1 | Stock alerts (AAPL/NVDA)      |      | ✓    |      |
| B2 | Weekly arXiv paper scout      | ✓    |      |      |
| B3 | Prod health monitor           |      | ✓    |      |

Coverage of trigger types in 6 examples: 2 cron, 3 pull, 1 push.

---

# What the MVP daemon must implement

| Component                | MVP scope                                                                                       |
| ------------------------ | ----------------------------------------------------------------------------------------------- |
| **Routing agent**        | Classify utterance → setup_standing; call `register_task(...)`.                                 |
| **Registry**             | SQLite with `subscriptions`, `agents`, `channels`, `poller_state`. No `skills` table yet.       |
| **Subscription manager** | At startup + on row change: arm APScheduler jobs, spawn poller tasks, register agent inboxes.   |
| **APScheduler producer** | In-process timer for cron rows.                                                                 |
| **`POST /events`**       | FastAPI ingress for push. Slack adapter (Socket Mode) POSTs to this.                            |
| **Pull poller**          | One asyncio task per pull subscription; reads/writes `poller_state`.                            |
| **Dispatcher**           | Route by `ev.target.agent_name`.                                                                |
| **Per-agent inboxes**    | `asyncio.Queue` per registered agent.                                                           |
| **Invoker loop**         | `dequeue → lock[thread_id] → agent_fn(event)`.                                                  |
| **Slack adapter**        | Socket Mode (inbound) + `slack.post` / `slack.history` (outbound).                              |
| **Tool adapters**        | `stock`, `arxiv`, `http`, `web.fetch`.                                                          |

**Out of scope for MVP** (still good architecture; just not in v1):
- HOOK trigger / SkillHookDispatcher
- Skills loader
- Declarative YAML (Mode 2) + `cuga apply`
- Adapters beyond Slack + the 4 tools listed above
- Multi-agent swarm via emit-Event

---

# What success looks like

A user opens chat, types all six utterances in one session (or across sessions), and over the next week:
- Standup prompts post Mon–Fri at 9:30 (A1)
- Alert threads get pinged when they go stale (A2)
- Inbound DMs get triaged into `#triage-log` (A3)
- AAPL/NVDA drops show up in `#trading-alerts` (B1)
- Monday morning brings a paper scout digest in `#research-feed` (B2)
- Two consecutive `/healthz` failures page `#oncall` (B3)

All six are subscription rows in one SQLite file. No per-use-case code. No web UI. The chat surface set them up; the daemon runs them.
