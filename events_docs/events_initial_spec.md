# Event-driven agents — take and utterances

## Capability checklist

- [ ] **Gateways** — inbound/outbound channels the agent can be reached on: Slack, WhatsApp, Telegram, email
    - [ ] Supported input formats and modalities:
        - [ ] Plain text
        - [ ] File uploads (generic)
        - [ ] Documents (PDFs, decks, slides)
        - [ ] Audio / video (STT, VLM in the loop)
- [ ] **Connectors (MCP)** — drop-in access to third-party systems (Box, Gmail, Calendar, …). *Already supported in CUGA; no new work, except per-user/per-thread credential binding.*
- [ ] **[pub] Publish** — the agent can emit events to a destination (Slack channel, webhook, topic, queue) rather than only replying inline.
- [ ] **[sub] Subscribe** — the agent can register interest in an external event and wake on it. Two flavors:
    - Always-on listener (websocket / IMAP idle / webhook receiver)
    - Hook-style: subscriptions stored and dispatched by a poller
- [ ] **[cron] Schedule** — the agent can poll or act on a time-based trigger (interval, cron, one-shot delay). *Already supported via CUGA loops / self-scheduling.*
- [ ] **[swarm] Multi-agent collaboration** — agents can address and message each other directly (pipelines, fan-out/fan-in, critic pairs, market/auction patterns).

## My take

An agent today is **request → response** (synchronous, user-initiated). Event-driven flips two things:

1. **The trigger isn't the user** — it's a clock (`cron`) or an external signal (`sub`).
2. **The agent can also emit** — it doesn't just answer back; it `pub`lishes events that fan out to humans or other systems.

CUGA loops today gives `cron` cleanly (self-scheduling via APScheduler+SQLite). The other rows are real gaps.

## Honest take per row

**Gateways** — the *cheapest* row to build but the most visible. Each gateway is just an inbound HTTP/webhook adapter that calls `supervisor.invoke(prompt, thread_id=channel+user)`. The trap is **modality**: text is easy, files/PDFs need a unified attachment schema across channels, audio/video means STT/VLM in the loop. Recommend phasing: text → files → audio, and treat each channel's *outbound* (Slack thread replies, WhatsApp templates) as separate work from inbound.

**MCP connectors** — mostly solved. The thing CUGA *doesn't* have yet is **per-thread connector binding** (Alice's Gmail vs Bob's Gmail). That's the actual product gap, not the protocol.

**[pub]** — easy to underestimate. "Publish an event" needs a **destination contract**: is it Slack? a webhook? a row in a topic table? Build this as a single `publish(topic, payload)` tool the LLM can call, backed by a routing table (`topic → [slack_channel, webhook_url, ...]`). Reuse the existing `runs/` persistence for replay/audit.

**[sub]** — the hard one. Two architectures, very different:
- **Always-on listener**: long-lived process holding a websocket/IMAPidle/webhook receiver, invoking the agent on each event. Simple to reason about, expensive at idle, doesn't scale to thousands of subscriptions.
- **Hook table + poller** (claude-loops style): subscriptions stored as cron jobs that check an inbox and dispatch. Cheaper, slightly laggy.

Recommend **hooks via webhooks for push sources** (Slack, GitHub, Stripe) and **scheduled poll for pull sources** (RSS, arxiv, scraping). Don't try to unify them — the abstraction will leak.

**The unifying abstraction**: every row is really *"a trigger fires → agent runs with some prompt + context → agent emits zero or more events."* Cron, inbound message, webhook — all the same shape. If you build the trigger→invoke→emit core once, you add channels by writing adapters, not rewiring the agent.

---

## Utterances by capability

### Gateways (inbound channels)
- *"@cuga in Slack: scout new restaurants in Brooklyn"* — Slack mention triggers supervisor
- *"[WhatsApp message] here's a deck, summarize the top 3 risks"* — file input over WhatsApp
- *"[Email to cuga@anu.dev with PDF attached] extract action items and reply"*
- *"[Telegram voice note] schedule a lead-gen loop for Pleasantville every 10 days"* — audio → STT → cron
- *"[email forward] turn this thread into a CRM entry"*

### MCP / connectors
- *"Look at my Box folder 'leads-Q2' and enrich each with revenue estimates"*
- *"Pull my last week of Gmail with label 'investor', summarize per thread"*
- *"Find a free 30-min slot for me and Alice next week on Calendar"*

### [pub] — agent emits an event
- *"When you find a hot lead (fit_score≥8), post it to #sales-hot in Slack"*
- *"After each arxiv sweep, publish new papers to the `arxiv-rag` topic so my notion-sync picks them up"*
- *"If staging deploy fails, page oncall via PagerDuty"*
- *"Whenever a lead's website goes from no-online-ordering to online-ordering, email me — they're a lost cause now."*

### [sub] — agent reacts to external events
- *"Whenever I get a Calendly booking, draft a prep doc from the attendee's LinkedIn"* (webhook sub)
- *"Whenever a PR is opened on repo X with label `needs-design`, ping the design channel with a summary"* (GitHub webhook)
- *"Watch this Slack channel — when anyone posts a customer complaint, file a Linear ticket"* (Slack events API sub)
- *"When my Stripe MRR drops more than 5% week-over-week, draft a churn analysis"* (webhook + state diff)
- *"Whenever Gmail receives a message with subject containing 'invoice', extract amount and append to a sheet"* (IMAP idle / Gmail push)

### [cron] — already supported
- *"Check arxiv daily and email a digest"*
- *"Every 10 days, find me a fresh lead"*
- *"Every Friday 4pm, draft my weekly status"*
- *"Every Monday at 9am, pull the top 10 HN posts about LLM tooling and send a digest."*
- *"Watch the OpenAI changelog every 6 hours and ping me only when there's a new model."*
- *"Watch this Greenhouse careers page every hour — alert me when an AI role opens."*
- *"Check flight prices NYC→Tokyo every 12 hours, notify me if anything dips below $700."*
- *"Every 15 minutes, check whether the staging deploy passed and Slack me if it failed."*
- *"In 2 hours, check whether PR #482 has been reviewed and ping me."* (one-shot delay)
- *"Watch this PR every 30 minutes — when CI turns green, merge it and stop."* (interval + self-cancel)

### Combinations (where it gets interesting)
- *"[sub + pub] When a customer emails support, classify; if it's a bug, file Linear; if it's a sales question, ping #sales."*
- *"[cron + pub] Every Monday, scout leads, draft emails, post the top 3 to Slack for human approval before send."*
- *"[gateway + sub] WhatsApp me only when a watched arxiv paper has >50 citations within a week."*
- *"[gateway + cron + pub] Daily at 8am, voice-call me with a 30-second brief of overnight events."* (audio output gateway)

## MVP examples — Slack-only, chat-driven (from `mvp_proposal.md`)

Six concrete utterances forming the smallest end-to-end demo: Slack as the only channel adapter, plus three small tool adapters (`stock`, `arxiv`, `http`). One UI (chat), one daemon, all six rows in SQLite.

### Part A — Slack as inbound + outbound

- **[cron] A1** — *"Every weekday at 9:30am, post the standup template in `#eng-standup`."*
  Outbound: `#eng-standup`. Tools: `slack.post`.
- **[pull] A2** — *"Every 10 minutes, scan `#alerts` for threads older than 30 min without a reply, and ping the on-call in the same channel."*
  Inbound: `slack://acme-ws/#alerts` (via `slack.history`). Outbound: `#alerts`. State-diff: `seen_ids`.
- **[push] A3** — *"When someone DMs me on Slack, classify it as bug/billing/other and post a triage note to `#triage-log`."*
  Inbound: Slack Socket Mode → `POST /events`. Outbound: `#triage-log`.

### Part B — Chat-initiated, Slack as outbound only

- **[pull] B1** — *"Watch AAPL and NVDA. If either drops more than 3% intraday, ping me in `#trading-alerts`."*
  Tools: `stock.quote`, `stock.intraday`, `slack.post`. State: `{fired_today, last_prices}`. Schedule window: market hours.
- **[cron] B2** — *"Every Monday 9am, find new arXiv papers on LLM agents from last week, rank them, post the top 5 to `#research-feed`."*
  Tools: `arxiv.search`, `web.fetch`, `slack.post`. (From cuga-apps `paper_scout`.)
- **[pull] B3** — *"Hit `https://prod-api.acme.com/healthz` every 2 minutes. If it fails twice in a row, ping `#oncall`."*
  Tools: `http.get`, `slack.post`. State: `{consecutive_failures, last_status}`. Emit only on the 2nd failure. (From cuga-apps `server_monitor`.)

### MVP coverage at a glance

| | CRON | PULL | PUSH |
|---|:---:|:---:|:---:|
| Part A (Slack ↔ Slack) | A1 | A2 | A3 |
| Part B (chat → tools → Slack) | B2 | B1, B3 | — |

**Totals:** 2 cron · 3 pull · 1 push · 6 standing intents · 1 channel adapter · 4 tool adapters.

**Out of scope for the MVP:** hooks, skills, declarative YAML mode, swarm emit, any non-Slack channel.

---

## The "shape" test

A good event-driven framework should make all of these expressible as: `trigger × agent × emit`. If your DSL needs separate concepts for "cron job" vs "webhook handler," you've under-abstracted. If everything is one concept (`on <trigger> run <agent> emit <events>`), users compose freely and you only maintain adapters.
