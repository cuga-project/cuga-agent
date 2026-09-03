# Domain Rosters — organized agent families

The default roster `events/examples/rosters/default.yaml` is one flat supervisor over ~27 sub-agents thrown together —
a grab-bag with no theme. These roster files reorganize the **same schema** into **coherent
domain-intelligence families**: one supervisor per domain, each owning a *small set of focused
sub-agents that belong together*, so an agent means something ("Box Intelligence") instead of
being a random pile.

Grouped along the same lines as the Activepieces app catalogue — Enterprise Productivity,
Market & Research Intelligence, and the document-/recording-/code-centric flows.

> **Nothing here is wired in.** These are drop-in *alternatives* to the root roster, authored for
> testing. No code changed, no server restarted. Each file is loader-compatible with the existing
> `supervisor: … / agents: …` schema and its `HANDLES TRIGGERS:` convention.

## The families

| File | Domain | Watches | Sub-agents |
|---|---|---|---|
| `box_document_intelligence.yaml` | **Box & Documents** | Box files/folders/comments, email attachments | resume_reviewer · recording_summarizer · document_summarizer · comment_responder · folder_indexer |
| `inbox_calendar_intelligence.yaml` | **Inbox & Calendar** | Gmail, Google Calendar | email_triager · attachment_handler · label_watcher · meeting_prep · followup_writer |
| `repository_intelligence.yaml` | **GitHub repo** | all 14 GitHub triggers | pr_reviewer · security_watchdog · issue_triager · repo_lifecycle · release_notes |
| `team_comms_intelligence.yaml` | **Team chat** | Slack, Discord, Telegram | channel_monitor · incident_triage · workspace_lifecycle · link_summarizer · support_digest |
| `market_research_intelligence.yaml` | **Outside world** | RSS, YouTube, Pinterest, markets, papers | ai_trend_radar · competitive_analyst · feed_watcher · video_researcher · market_briefer · paper_scout |
| `personal_assistant.yaml` | **Everyday chat** | (conversation only, no triggers) | pricebot · weatherbot · places_guide · recipe_composer · entertainment · find_a_doctor · meetup_finder |

Together they cover every trigger the flat roster did, but each sub-agent now sits in a family
where its siblings share tools, payload conventions, and a supervisor whose routing instruction is
domain-specific (e.g. "route a resume to resume_reviewer, a recording to recording_summarizer").

## Enterprise test-bed rosters — split by AP dependency

A second, orthogonal cut for demoing "the teams an enterprise would deploy." These are named
`no_ap_*` / `ap_*` so the Activepieces dependency is legible from the filename alone. **AP-dependency
is a property of the *triggers*, not the agents** — an `ap_*` roster fields SaaS push events
(Gmail/GitHub/Box/Calendar) that CUGA can't watch directly, so it needs AP; a `no_ap_*` roster's
triggers are all direct channels (Slack/Discord/Telegram) + native cron/poll/RSS, so it runs with
**zero AP infra**. All agents are pulled verbatim from `events/examples/rosters/default.yaml` — regrouped, not
rewritten. Where a borrowed agent had a mixed trigger set, the `no_ap_*` files trim its
`HANDLES TRIGGERS` lines down to only the AP-free triggers so the file is honestly no-AP.

| File | Persona | Sub-agents | AP |
|---|---|---|---|
| `no_ap_research_desk.yaml` | Research & Strategy | research_compass · papers · ai_labs_news · wiki_dive · webpage_summarizer | none |
| `no_ap_markets_desk.yaml` | Markets / Finance | pricebot · market_briefer · competitive_analyst · feed_watcher (RSS) | none |
| `no_ap_it_helpdesk.yaml` | IT Helpdesk / Support | ibm_docs_qa · code_auditor · incident_triage · support_digest | none (Slack/Discord direct) |
| `ap_exec_office.yaml` | Executive Office | mailbot · resume_judge | **AP** — Gmail/Calendar/Box |
| `ap_devops.yaml` | Engineering / DevOps | pr_reviewer · repo_watcher · incident_triage · github_trending · code_auditor | **AP** — GitHub push |

## The organizing principle

- **One domain = one file = one supervisor.** A domain is a *source of events + a purpose*, not a
  single integration. "Box Intelligence" is about documents, so it also fields email attachments;
  "Team Comms" spans Slack + Discord + Telegram because they're the same job on different transports.
- **Each sub-agent does ONE meaningful thing** and declares the exact triggers it HANDLES (mirrored
  from `events/triggers.py`). No sub-agent is a catch-all.
- **Tools stay within the real MCP set**: `cuga_finance · cuga_knowledge · cuga_geo · cuga_web ·
  cuga_code · cuga_text`. No invented servers.
- **Supervisor name stays `cuga`** so a file is drop-in: events still address the one agent `cuga`;
  only its roster (and personality) changes per domain.

## How to test one

Point `CUGA_SUPERVISOR_ROSTER` at the file you want and bounce the servers. Nothing is copied
and nothing needs restoring — `make reload` on its own puts the default back.

```sh
# from repo root
CUGA_SUPERVISOR_ROSTER=rosters/box_document_intelligence.yaml make reload
# …exercise it (synth-fire a box/new_file, or chat)…
make reload                                                # back to the default roster
```

Swap in a different file to test a different family. Because each is a smaller, themed roster, the
supervisor's routing is easier to reason about and to score with `make test-delegation`.

## Where this points (future, needs code)

Today the runtime is a **single-level** supervisor over a flat list. The natural next step these
files set up: a **two-level hierarchy** — a top `cuga` that routes to a *domain supervisor*
(Box / Inbox / Repo / Comms / Research), which then picks the specialist. That's a code change
(nested supervisors) and is intentionally NOT done here — these files are the content that would
populate such a hierarchy, and are useful now as standalone test rosters.
