# Cuga-apps coverage under the new event-driven architecture

**Vision:** one app, one UI, one entry point. Configure with **tools** (MCP adapters) and **channels** (URI references with credentials). Both **one-shot conversational tasks** and **standing intents** (cron/push/pull/hook) flow through the same daemon. No per-app web app.

**Verdict column:**
- ✅ **Yes** — fully subsumed by the new architecture as a standing intent or specialist agent.
- 🟡 **Yes, one-shot** — works as a specialist agent invoked from chat; not event-driven, but no web app needed either.
- ⚠️ **Partial** — core flow fits, but something (UI affordance, multi-modal media, interactive HITL) needs care.

---

## Master table

**Pre-work legend:** O = one-time external wiring (webhook, OAuth, Pub/Sub config). A = adapter installation/auth (API key, OAuth grant). S = skill/prompt authored. P = persistent storage created (poller_state, indices). U = utterance issued by user (almost always required — omitted from column).

| #  | App                                | What it does                                                            | Core tools (MCP)                                            | Event-driven nature              | Inbound channel(s)                | Outbound channel(s)              | **Pre-work needed**                                                                                                                          | New arch solves it? |
| -- | ---------------------------------- | ----------------------------------------------------------------------- | ----------------------------------------------------------- | -------------------------------- | --------------------------------- | -------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- | ------------------- |
| 1  | **api_doc_gen**                    | Generate API docs from a repo                                           | `git.read`, `file.read`, `llm`                              | one-shot (chat invocation)       | chat                              | file / chat reply                | A: git creds. S: doc-gen skill (prompt + tools).                                                                                              | 🟡 one-shot         |
| 2  | **arch_diagram**                   | Generate architecture diagrams                                          | `file.read`, `llm`, `graphviz.render`                       | one-shot                         | chat                              | image / chat                     | A: install graphviz adapter (local binary). S: diagram skill.                                                                                 | 🟡 one-shot         |
| 3  | **bird_invocable_api_creator**     | Wraps an existing API into a BIRD-invocable form                        | `openapi.read`, `llm`, `file.write`                         | one-shot                         | chat                              | file                             | S: BIRD-creator skill (prompt + output schema).                                                                                               | 🟡 one-shot         |
| 4  | **bird_invocable_api_creator_cuga_native** | Same, native variant                                          | (same as above)                                             | one-shot                         | chat                              | file                             | (same as #3)                                                                                                                                  | 🟡 one-shot         |
| 5  | **box_qa**                         | Q&A over Box documents                                                  | `box.list`, `box.fetch`, `llm`                              | one-shot **+** push (new uploads index automatically) | chat / `box://acme/docs`          | chat                             | A: Box OAuth. O: Box webhook → `/events` (for auto-index). P: vector index store.                                                             | ✅ Yes               |
| 6  | **brief_budget**                   | Daily/weekly budget brief                                               | `bank.balance`, `transactions.list`, `llm`                  | **cron** (daily)                 | (none — schedule)                 | email / slack                    | A: bank API token (Plaid/Teller). A: email or slack channel registered.                                                                       | ✅ Yes               |
| 7  | **city_beat**                      | Local news roundup                                                      | `newsapi.search`, `weather.today`, `web.fetch`              | **cron** (morning)               | (none — schedule)                 | slack / email                    | A: NewsAPI key, OpenWeather key. A: slack/email channel.                                                                                      | ✅ Yes               |
| 8  | **code_engine_deployer**           | Deploy to IBM Code Engine                                               | `git.read`, `ce.deploy`, `ce.status`                        | one-shot **or** push (on tag / PR merge) | chat / `github://acme/repo`       | slack notif + CE                 | A: IBM Cloud API key + CE project. O: GitHub webhook → `/events` (if push-driven). S: approval skill if HITL.                                 | ✅ Yes               |
| 9  | **code_reviewer**                  | Review code / PRs                                                       | `github.pr_diff`, `llm`, `github.comment`                   | **push** (PR opened)             | `github://acme/repo`              | `github` (PR comment) / slack    | A: GitHub app/token. O: webhook from repo → `/events`. S: review prompt/criteria.                                                             | ✅ Yes               |
| 10 | **deck_forge**                     | Generate slide decks                                                    | `llm`, `pptx.render`, `image.gen`                           | one-shot                         | chat                              | file                             | A: image-gen API key. A: pptx adapter installed.                                                                                              | 🟡 one-shot         |
| 11 | **drop_summarizer**                | Summarize files dropped into a folder                                   | `file.read`, `whisper.transcribe`, `llm`, `slack.post`      | **push** (FS inotify)            | `file:///Users/anu/recordings`    | slack / file (summary)           | A: FS adapter (inotify task running). A: Whisper API or local model. A: slack channel.                                                        | ✅ Yes               |
| 12 | **hiking_research**                | Research hiking trails                                                  | `web.search`, `maps.places`, `weather.forecast`             | one-shot                         | chat                              | chat (itinerary)                 | A: maps & weather API keys. A: web search key.                                                                                                | 🟡 one-shot         |
| 13 | **ibm_cloud_advisor**              | IBM Cloud recommendations                                               | `ibm_cloud.catalog`, `web.search`, `llm`                    | one-shot                         | chat                              | chat                             | A: IBM Cloud token. S: advisor prompt.                                                                                                        | 🟡 one-shot         |
| 14 | **ibm_docs_qa**                    | Q&A over IBM docs                                                       | `docs.search`, `web.fetch`, `llm`                           | one-shot (RAG)                   | chat                              | chat                             | P: docs corpus indexed (offline ingestion). A: search index (e.g. pgvector).                                                                  | 🟡 one-shot         |
| 15 | **ibm_whats_new**                  | What's new in IBM products                                              | `ibm_blog.rss`, `web.fetch`, `llm`                          | **cron** (weekly)                | (none — schedule)                 | slack / email                    | A: RSS feed URLs. A: slack/email channel.                                                                                                     | ✅ Yes               |
| 16 | **movie_recommender**              | Recommend movies                                                        | `tmdb.search`, `llm`                                        | one-shot                         | chat                              | chat                             | A: TMDB API key.                                                                                                                              | 🟡 one-shot         |
| 17 | **newsletter**                     | Daily/weekly newsletter from RSS feeds                                  | `rss.fetch`, `web.fetch`, `llm`, `email.send`               | **cron** (daily)                 | (RSS-bridge or schedule)          | email                            | A: RSS feed list. A: email channel/auth.                                                                                                      | ✅ Yes               |
| 18 | **ouroboros**                      | Multi-agent lead generation                                             | `web.search`, `linkedin.lookup`, `crm.write`, `llm`         | **cron** + multi-agent swarm     | (none — schedule)                 | crm / slack                      | A: LinkedIn API/scraper auth, CRM API key. S: scout/research/writer specialist prompts. P: dedup store for leads.                              | ✅ Yes (swarm via emit-Event) |
| 19 | **paper_scout**                    | New arXiv papers on a topic                                             | `arxiv.search`, `semantic_scholar.lookup`, `llm`            | **cron** (weekly)                | (none — schedule)                 | slack / email                    | A: S2 API key (optional, arXiv is public). A: slack/email channel.                                                                            | ✅ Yes               |
| 20 | **recipe_composer**                | Weekly meal plan + shopping list                                        | `notion.read`, `recipe_api.search`, `gmail.send`            | **cron** (weekly)                | (none — schedule)                 | email                            | A: Notion integration auth + pantry page id. A: recipe API key. A: gmail channel.                                                             | ✅ Yes               |
| 21 | **server_monitor**                 | Monitor servers / endpoints                                             | `http.get`, `pagerduty.trigger`                             | **pull** (every 2 min, state-diff on failure-count) | `https://prod-api/healthz`        | pagerduty / slack                | A: PagerDuty service key. P: poller_state row (failure-count cursor).                                                                         | ✅ Yes               |
| 22 | **smart_todo**                     | Auto-populate todos from email/calendar/Slack                           | `gmail.list`, `calendar.events`, `slack.history`, `todo.write` | **cron** + **push** + **hook** (multi-source) | gmail / calendar / slack          | todo store / chat                | A: gmail + calendar + slack OAuth. O: Gmail Pub/Sub + Slack Socket Mode wired. P: todo store schema. S: extractor prompt.                     | ✅ Yes               |
| 23 | **stock_alert**                    | Stock price threshold alerts                                            | `stock.quote`, `stock.intraday`                             | **pull** (5 min, market hours)   | stock API                         | slack / email                    | A: stock API key (Alpha Vantage / Yahoo). P: poller_state (last-known prices, fired-today set). A: slack channel.                              | ✅ Yes               |
| 24 | **travel_planner**                 | Plan a multi-day trip                                                   | `flights.search`, `hotels.search`, `maps.places`, `web.fetch` | one-shot                         | chat                              | chat / pdf                       | A: flights + hotels + maps API keys. A: PDF render adapter.                                                                                   | 🟡 one-shot         |
| 25 | **trip_designer**                  | Trip itinerary designer                                                 | (same as travel_planner)                                    | one-shot                         | chat                              | chat / pdf                       | (same as #24)                                                                                                                                 | 🟡 one-shot         |
| 26 | **video_qa**                       | Q&A over a video file                                                   | `whisper.transcribe`, `video.frames`, `llm`                 | one-shot                         | chat (file upload)                | chat                             | A: Whisper. A: video frame extractor (ffmpeg). **UI: file-upload affordance in chat.**                                                        | ⚠️ Partial (multimodal file upload UX) |
| 27 | **voice_journal**                  | Voice-driven journal entries                                            | `whisper.transcribe`, `llm`, `notion.write`                 | **push** (new recording in folder) **or** one-shot | `file:///recordings`              | notion / file                    | A: Whisper. A: Notion integration. A: FS adapter on recordings dir.                                                                           | ✅ Yes               |
| 28 | **web_researcher**                 | General web research                                                    | `web.search`, `web.fetch`, `llm`                            | one-shot                         | chat                              | chat                             | A: search API key (Brave/Google/Bing).                                                                                                        | 🟡 one-shot         |
| 29 | **webpage_summarizer**             | Summarize one or many URLs                                              | `web.fetch`, `llm`                                          | one-shot **or** push (page-diff) | chat / `page-watch://…`           | chat / email                     | A: page-diff-watcher adapter (for push). P: per-page hash store.                                                                              | ✅ Yes               |
| 30 | **wiki_dive**                      | Deep dive on a topic                                                    | `wikipedia.lookup`, `web.search`, `llm`                     | one-shot                         | chat                              | chat                             | (none beyond defaults)                                                                                                                        | 🟡 one-shot         |
| 31 | **youtube_research**               | Find + summarize new YouTube videos on a topic                          | `youtube.search`, `youtube.transcript`, `llm`               | **cron** (weekly) **or** one-shot| (none — schedule) / chat          | slack / email                    | A: YouTube Data API key. A: transcript provider (if not using YT). P: poller_state (seen video ids).                                          | ✅ Yes               |

---

## Aggregate counts

| Category                                     | Count |
| -------------------------------------------- | ----- |
| **Standing-intent apps** (cron/push/pull/hook) — fully event-driven | **14** |
| **One-shot specialists** (chat → agent → reply) — no event needed   | **15** |
| **Hybrid** (works either way: one-shot or scheduled)                | **2** (webpage_summarizer, voice_journal) |
| **Partial** (multimodal file upload needs UX care)                   | **1** (video_qa) |

### Standing-intent apps (covered by the new arch)
brief_budget, box_qa (intake side), city_beat, code_engine_deployer, code_reviewer, drop_summarizer, ibm_whats_new, newsletter, ouroboros, paper_scout, recipe_composer, server_monitor, smart_todo, stock_alert.

### One-shot specialists (chat-invoked)
api_doc_gen, arch_diagram, bird_invocable_api_creator, bird_invocable_api_creator_cuga_native, deck_forge, hiking_research, ibm_cloud_advisor, ibm_docs_qa, movie_recommender, travel_planner, trip_designer, web_researcher, wiki_dive, video_qa, youtube_research.

---

## What "configure with tools + channels and it works magically" looks like

A user wanting **all of the above** in one app would, in practice, configure:

### Tools (adapters to install)
- `web` (search + fetch)
- `llm` (model providers)
- `gmail` / `outlook` (email)
- `slack`
- `file` (local FS)
- `box`
- `github`
- `notion`
- `arxiv`, `semantic_scholar`, `newsapi`, `rss`, `wikipedia`, `tmdb`
- `weather`, `maps`, `flights`, `hotels`
- `stock`, `bank`, `crm`
- `whisper`, `youtube`, `pptx`, `graphviz`
- `pagerduty`
- `ibm_cloud`, `ce` (Code Engine)

### Channels (URI references + creds)
- `slack://acme-ws/#…` (multiple channels)
- `gmail://anu@…` / `outlook://support@…`
- `box://acme/…`
- `github://acme/repo-name`
- `file:///Users/anu/recordings`, `file:///Users/anu/inbox`, `file:///tmp`
- `https://prod-api.acme.com/healthz`
- `page-watch://…`
- `notion://workspace/…`
- `crm://acme/leads`

### Then every cuga-app is just *either*:
- A **chat-invocable specialist agent** (one-shot apps), registered in the `agents` table with prompt + tool list.
- **One or more standing-intent subscriptions** (event-driven apps), registered via `register_task(...)`.

No bespoke web app per app. One UI: the chat surface (for one-shots + subscription setup) and a registry browser (for viewing/editing standing intents). One entry point: the CUGA daemon.

---

## Where the new architecture falls short

1. **Multimodal file uploads in chat** (`video_qa`, sometimes `deck_forge`/`arch_diagram`) — the chat UI needs an upload affordance. Backend is fine; this is a UX concern.
2. **Long-running synchronous tasks** (`travel_planner` returning a 20-page PDF) — works, but the user is waiting on a single agent invocation for minutes. May want a "task started → notify when done" pattern (which IS expressible via emit-Event-to-self-on-completion).
3. **Apps with custom interactive UIs** (`deck_forge` editing slides inline, `arch_diagram` letting users drag boxes) — these aren't really "agent" apps; they're tools with embedded agents. Out of scope.
4. **HITL approval loops** (any high-stakes deployer like `code_engine_deployer`) — needs an approval Event pattern: agent emits "needs-approval" Event → human resolves it → resumes. Expressible in the architecture but a deliberate design pattern, not free.

---

## Verdict

> Of 31 cuga-apps: **29 fit the new architecture cleanly** (14 as event-driven standing intents, 15 as chat-invocable specialist agents). The remaining 2–3 are partial — needing UI affordances rather than architectural changes.

The vision holds: **one app, one UI, one daemon, configured with tools + channels**, and the existing cuga-apps fleet collapses into specialist agents and subscriptions inside it.
