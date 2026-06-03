# Channels × Triggers — example matrix

Four channels (Slack, Box, Email, FS) × four trigger types (CRON, PUSH, PULL, HOOK). Each cell is one concrete subscription a user could express in chat.

---

## Quick cross-reference

| Channel \ Trigger | **CRON** (time)         | **PUSH** (external initiates)     | **PULL** (poll + state-diff)     | **HOOK** (observe agent activity)        |
| ----------------- | ----------------------- | --------------------------------- | -------------------------------- | ---------------------------------------- |
| **Slack**         | daily standup post      | DM lands in `#help`               | scan `#alerts` for unresolved    | when agent posts to `#prod-incidents`    |
| **Box**           | nightly folder report   | new shared file (webhook)         | poll `/uploads` for new docs     | when agent uploads to `/legal`           |
| **Email**         | morning digest          | message arrives (IDLE / Pub/Sub)  | poll for starred / filtered      | when agent sends to external recipient   |
| **FS**            | hourly cleanup of `tmp` | new file via inotify              | scan dir for changes (no inotify)| when agent writes to `~/data/exports`    |

---

## Bucket 1 — Slack channel

### CRON · "Daily standup post"
> *"Every weekday 9:30am, post a standup template in `#eng-standup`."*
- **Trigger**: `{ kind: cron, cron: "30 9 * * 1-5" }`
- **Target agent**: `standup_poster`
- **Tools**: `slack.post`
- **Outcome**: `slack.post(channel: slack://acme-ws/#eng-standup, text: "<template>")`

### PUSH · "Triage incoming DMs"
> *"When a customer DM lands in `#help`, look up their account and post triage notes to `#triage-log`."*
- **Trigger**: `{ kind: push, channel: slack://acme-ws/#help, event: message }`
- **Target agent**: `triage_agent`
- **Tools**: `postgres.query`, `slack.post`
- **Outcome**: `slack.post(channel: slack://acme-ws/#triage-log, ...)`

### PULL · "Find unresolved alerts"
> *"Every 10 min, scan `#alerts` for threads without a reply older than 30 min — ping the on-call."*
- **Trigger**: `{ kind: pull, channel: slack://acme-ws/#alerts, query: "unresolved>30m", interval: 10m, state_key: alerts-anu }`
- **Target agent**: `oncall_ping_agent`
- **Tools**: `slack.history`, `slack.post`
- **Outcome**: `slack.post(channel: slack://acme-ws/@oncall, ...)`

### HOOK · "Notify when agents post to prod incidents"
> *"Whenever any agent posts to `#prod-incidents`, page the on-call leader."*
- **Trigger**: `{ kind: hook, channel: skill://*/slack.post, filter: args.channel == "slack://acme-ws/#prod-incidents" }`
- **Target agent**: `page_oncall_lead_agent`
- **Outcome**: `pagerduty.trigger(...)`

---

## Bucket 2 — Box folder

### CRON · "Nightly folder report"
> *"Every night at midnight, summarize what was uploaded to `/uploads` today and email me."*
- **Trigger**: `{ kind: cron, cron: "0 0 * * *" }`
- **Target agent**: `box_daily_report_agent`
- **Tools**: `box.list`, `gmail.send`
- **Outcome**: `gmail.send(to: anu@…, subject: "Box uploads today", body: <summary>)`

### PUSH · "React to new shared files"
> *"When someone shares a file with my Box account, summarize it and add a comment."*
- **Trigger**: `{ kind: push, channel: box://acme/anu, event: file_shared }`  *(via Box webhooks)*
- **Target agent**: `box_intake_agent`
- **Tools**: `box.fetch`, `box.comment`
- **Outcome**: `box.comment(file_id, text: <summary>)`

### PULL · "Poll uploads for new contracts"
> *"Every 15 min, check `/uploads/contracts` for new PDFs and create a Linear review ticket."*
- **Trigger**: `{ kind: pull, channel: box://acme/uploads/contracts, query: "ext:pdf", interval: 15m, state_key: box-contracts }`
- **Target agent**: `contract_intake_agent`
- **Tools**: `box.fetch`, `linear.create_issue`
- **Outcome**: `linear.create_issue(team: LEGAL, title: <filename>, body: <summary>)`

### HOOK · "Audit legal uploads"
> *"Whenever any agent uploads to `/legal`, log the action for compliance."*
- **Trigger**: `{ kind: hook, channel: skill://*/box.upload, filter: args.path startswith "/legal" }`
- **Target agent**: `compliance_log_agent`
- **Outcome**: `postgres.insert(table: audit_log, ...)`

---

## Bucket 3 — Email (Gmail / Outlook)

### CRON · "Morning digest"
> *"Every weekday 8am, summarize the top 5 most important emails from the last 24h."*
- **Trigger**: `{ kind: cron, cron: "0 8 * * 1-5" }`
- **Target agent**: `digest_agent`
- **Tools**: `gmail.list`, `gmail.fetch`, `gmail.send`
- **Outcome**: `gmail.send(to: anu@…, subject: "Morning digest")`

### PUSH · "Triage on arrival"
> *"When email arrives at `support@acme.com`, classify it as bug/billing/other and label it."*
- **Trigger**: `{ kind: push, channel: outlook://support@acme.com, event: message_received }`
- **Target agent**: `triage_agent`
- **Tools**: `outlook.label`
- **Outcome**: `outlook.label(message_id, label: <class>)`

### PULL · "Watch starred filter"
> *"Every 5 min, find new starred emails and add them to Linear backlog."*
- **Trigger**: `{ kind: pull, channel: gmail://anu@…, query: "is:starred newer_than:1d", interval: 5m, state_key: gmail-starred-anu }`
- **Target agent**: `linear_create_agent`
- **Tools**: `gmail.fetch`, `linear.create_issue`
- **Outcome**: `linear.create_issue(title: msg.subject, body: msg.snippet)`

### HOOK · "Flag outbound emails to external recipients"
> *"Whenever any agent sends an email to a non-`@acme.com` address, log it."*
- **Trigger**: `{ kind: hook, channel: skill://*/gmail.send, filter: not args.to endswith "@acme.com" }`
- **Target agent**: `external_email_audit_agent`
- **Outcome**: `postgres.insert(table: external_email_log, ...)`

---

## Bucket 4 — File system

### CRON · "Hourly cleanup"
> *"Every hour, delete files older than 24h in `/tmp/cuga-cache`."*
- **Trigger**: `{ kind: cron, cron: "0 * * * *" }`
- **Target agent**: `cache_cleanup_agent`
- **Tools**: `file.list`, `file.delete`
- **Outcome**: `file.delete(...)` (no external channel; pure local)

### PUSH · "React to drops in inbox dir"
> *"When a file lands in `~/inbox`, run OCR and move it to `~/processed`."*
- **Trigger**: `{ kind: push, channel: file:///Users/anu/inbox, event: created }`  *(via inotify / FSEvents adapter)*
- **Target agent**: `ocr_intake_agent`
- **Tools**: `ocr.extract`, `file.move`
- **Outcome**: `file.move(src: ~/inbox/<f>, dst: ~/processed/<f>)`

### PULL · "Scan a mounted drive (no inotify)"
> *"Every 30 min, scan `/mnt/share/incoming` for new CSVs and load them into Postgres."*
- **Trigger**: `{ kind: pull, channel: file:///mnt/share/incoming, query: "ext:csv", interval: 30m, state_key: shared-incoming }`
- **Target agent**: `csv_loader_agent`
- **Tools**: `file.read`, `postgres.bulk_insert`
- **Outcome**: `postgres.bulk_insert(...)`

### HOOK · "Track exports"
> *"Whenever any agent writes to `~/data/exports`, record the filename, size, and originating agent."*
- **Trigger**: `{ kind: hook, channel: skill://*/file.write, filter: args.path startswith "~/data/exports" }`
- **Target agent**: `export_audit_agent`
- **Outcome**: `postgres.insert(table: export_log, ...)`

---

## Reading the matrix

- **Rows (channels)** show how *one data source* powers all four trigger styles. Useful when onboarding a new integration: implement once, get four interaction patterns.
- **Columns (triggers)** show how *one trigger type* feels across data sources. Useful for picking the right trigger for a new use case.
- **CRON** doesn't need a channel for the trigger itself (the clock fires), but its **outcomes** still target channels — e.g., the morning digest fires on time and writes to email.
- **PUSH** requires the data source to support inbound notifications (webhook, socket, IDLE, inotify). For systems without that, fall back to **PULL**.
- **HOOK** never reads from a channel — it observes *agents* using channels. The channel scheme `skill://<agent_or_skill>/<tool>` is the hook's "address."

---

---

## Bucket 5 — External-tool-driven workflows

These don't sit on one channel — they pull from web APIs, search the internet, query specialized services, and *then* deliver via a channel. Inspired by cuga-apps (newsletter, paper_scout, stock_alert, hiking_research, city_beat, etc.).

### CRON · "Daily AI agents news digest"
> *"Every morning 7am, find the top AI-agents news from the last 24h and email me a digest."*
- **Trigger**: `{ kind: cron, cron: "0 7 * * *" }`
- **Target agent**: `ai_news_digest_agent`
- **External tools**: `hackernews.search("AI agents")`, `arxiv.recent("cs.AI")`, `twitter.search("AI agents")`, `web.fetch`, `web.summarize`
- **Outcome**: `gmail.send(to: anu@…, subject: "AI Agents — Today", body: <ranked + summarized>)`

### CRON · "Weekly paper scout"
> *"Every Monday morning, search arXiv for new papers on LLM agents and tool use; rank by citations/likes; post the top 5 to `#research-feed`."*
- **Trigger**: `{ kind: cron, cron: "0 9 * * 1" }`
- **Target agent**: `paper_scout_agent`
- **External tools**: `arxiv.search`, `semantic_scholar.lookup`, `web.fetch`
- **Outcome**: `slack.post(channel: slack://acme-ws/#research-feed, blocks: <ranked list>)`

### CRON · "City beat — local news roundup"
> *"Every weekday 8am, summarize today's local news for Boston and DM it to me."*
- **Trigger**: `{ kind: cron, cron: "0 8 * * 1-5" }`
- **Target agent**: `city_beat_agent`
- **External tools**: `newsapi.search(location: Boston)`, `web.fetch`, `web.summarize`, `weather.today(Boston)`
- **Outcome**: `slack.post(channel: slack://acme-ws/@anu, text: <digest + weather header>)`

### PULL · "Stock price threshold alert"
> *"Every 5 min during market hours, check AAPL and NVDA. If either drops >3% intraday, page me."*
- **Trigger**: `{ kind: pull, source: stock_api, query: "AAPL,NVDA", interval: 5m, state_key: stock-watch-anu, schedule_window: "09:30-16:00 ET" }`
- **Target agent**: `stock_alert_agent`
- **External tools**: `stock.quote`, `stock.intraday_change`
- **State-diff**: emits Event only when `intraday_pct < -3` for the first time today
- **Outcome**: `slack.post(channel: slack://acme-ws/@anu, text: "<ticker> down <%>")` + `gmail.send(...)`

### PULL · "Trip designer — flight price drop"
> *"Every hour, check flights SFO→TYO in November under \$1200; let me know if anything matches."*
- **Trigger**: `{ kind: pull, source: travel_api, query: "SFO TYO Nov", interval: 1h, state_key: sfo-tyo-nov }`
- **Target agent**: `flight_watcher_agent`
- **External tools**: `kayak.search`, `google_flights.lookup`, `web.fetch`
- **State-diff**: emits Event only when a new sub-\$1200 itinerary appears (not in `seen_ids`)
- **Outcome**: `gmail.send(to: anu@…, subject: "Flight match: SFO→TYO \$<price>", body: <itinerary>)`

### PULL · "Server monitor"
> *"Every 2 min, hit `/healthz` on prod-api. If it's failed twice in a row, page the on-call."*
- **Trigger**: `{ kind: pull, source: https://prod-api.acme.com/healthz, interval: 2m, state_key: prod-healthz }`
- **Target agent**: `server_monitor_agent`
- **External tools**: `http.get`, `pagerduty.trigger`
- **State-diff**: track consecutive-failure count; emit Event when count crosses 2
- **Outcome**: `pagerduty.trigger(service: prod-api, severity: critical)`

### PUSH · "Newsletter — react to inbound RSS"
> *"When the OpenAI blog publishes anything new, summarize it and post to `#openai-watch`."*
- **Trigger**: `{ kind: push, channel: rss://openai.com/blog, event: item_published }`  *(via RSS-to-webhook adapter)*
- **Target agent**: `newsletter_agent`
- **External tools**: `web.fetch`, `web.summarize`
- **Outcome**: `slack.post(channel: slack://acme-ws/#openai-watch, text: <title + summary + link>)`

### PUSH · "Webpage change detector"
> *"When the IBM Cloud pricing page changes, summarize what changed and email me."*
- **Trigger**: `{ kind: push, channel: page-watch://www.ibm.com/cloud/pricing }`  *(via diff-watcher adapter)*
- **Target agent**: `pricing_change_agent`
- **External tools**: `web.fetch`, `web.diff`, `web.summarize`
- **Outcome**: `gmail.send(to: anu@…, subject: "IBM Cloud pricing changed", body: <diff summary>)`

### HOOK · "Hiking research — track external-API cost"
> *"Whenever any agent calls `maps.directions`, log mileage + estimated API cost."*
- **Trigger**: `{ kind: hook, channel: skill://*/maps.directions }`
- **Target agent**: `api_cost_tracker_agent`
- **External tools**: none (just records)
- **Outcome**: `postgres.insert(table: api_usage, {tool, agent, ts, cost_estimate})`

### HOOK · "Web research — fact-check claims"
> *"Whenever `research_agent` returns, run a fact-check pass on its claims."*
- **Trigger**: `{ kind: hook, channel: skill://research_agent/return }`
- **Target agent**: `fact_check_agent`
- **External tools**: `web.search`, `web.fetch`, `wikipedia.lookup`
- **Outcome**: `slack.post(channel: slack://acme-ws/@anu, text: <claims with confidence scores>)`

---

## Bucket 6 — Multi-step external workflows

Sometimes a single utterance needs more than one external integration in series. The trigger fires once; the agent orchestrates multiple tool calls.

### "Recipe composer" (CRON)
> *"Every Sunday 5pm, suggest 3 dinner recipes for the week using what's in my Notion pantry list. Email me the recipes plus a shopping list of missing ingredients."*
- **Trigger**: `{ kind: cron, cron: "0 17 * * 0" }`
- **External tools**: `notion.read(page: pantry)`, `recipe_api.search`, `recipe_api.ingredients`, `gmail.send`
- **Agent reasoning**: pantry → candidate recipes → diff against pantry → shopping list
- **Outcome**: `gmail.send(...)`

### "Travel planner" (PUSH from chat)
> *"Plan a 5-day trip to Lisbon next month — flights, hotel, top 8 sights, day-by-day itinerary."*  *(one-shot, not a standing intent — included for contrast)*
- **Trigger**: none — direct chat invocation
- **External tools**: `google_flights.search`, `booking.search`, `maps.places`, `tripadvisor.top_sights`, `web.fetch`, `web.summarize`
- **Outcome**: PDF/markdown itinerary returned in chat

### "YouTube research" (CRON)
> *"Every Friday, find the best new YouTube videos on Rust async runtime and summarize each in 3 lines."*
- **Trigger**: `{ kind: cron, cron: "0 17 * * 5" }`
- **External tools**: `youtube.search`, `youtube.transcript`, `llm.summarize`
- **Outcome**: `slack.post(channel: slack://acme-ws/@anu, text: <summaries>)`

### "Drop summarizer" (PUSH — file system)
> *"When a meeting recording lands in `~/recordings`, transcribe it, summarize key decisions, and post to the relevant project Slack channel."*
- **Trigger**: `{ kind: push, channel: file:///Users/anu/recordings, event: created }`
- **External tools**: `whisper.transcribe`, `llm.extract_decisions`, `slack.post`
- **Agent reasoning**: detect project from filename → pick channel → post
- **Outcome**: `slack.post(channel: <detected>, text: <summary>)`

---

## Where do external tools come from?

External tools are exposed through the same **adapter** abstraction:

| Tool family               | Adapter        | How CUGA gets it                  |
| ------------------------- | -------------- | --------------------------------- |
| Web search / fetch        | `web` adapter  | wraps Brave/Google/Bing + fetcher |
| arXiv / Semantic Scholar  | `arxiv` adapter| wraps academic search APIs        |
| Hacker News / Reddit      | `news` adapter | wraps HN/Reddit search APIs       |
| Stock quotes              | `stock` adapter| wraps Alpha Vantage / Yahoo       |
| Maps / directions         | `maps` adapter | wraps Google Maps / Mapbox        |
| Travel / flights / hotels | `travel` adapter| wraps Kayak / Booking / Skyscanner|
| Weather                   | `weather` adapter| wraps OpenWeather / NWS         |
| YouTube                   | `youtube` adapter| wraps YT Data API + transcripts |
| Audio / video transcribe  | `whisper` adapter| wraps Whisper / Deepgram        |
| PagerDuty / OpsGenie      | `pager` adapter| wraps incident-management APIs    |

Same shape as Slack/Box/Email/FS adapters. Most external-tool adapters are **outbound-only** (no inbound face) — but a few like RSS-feeds, page-diff-watchers, and stock-tick streams have inbound faces too.

---

## What this proves about the architecture

Adding a new channel means writing **one adapter** with two faces (inbound, outbound). The four interaction patterns above come "for free":

- CRON works (any tool the adapter exposes outbound is callable on a schedule).
- PUSH works (the adapter's inbound face plugs into `POST /events`).
- PULL works (the adapter's outbound `list`/`query` tools feed the generic pull poller).
- HOOK works (`skill://*/<adapter>.<tool>` matches the moment any agent calls it).

That's the test of whether the channel abstraction earns its keep: adding **Box**, **Notion**, **Jira**, **S3**, or anything else lights up all four trigger patterns without changing the dispatcher, inboxes, Invoker, or any other agent.
