# Domain Rosters — organized agent families

The root `/supervisor_agents.yaml` is one flat supervisor over ~27 sub-agents thrown together —
a grab-bag with no theme. These roster files reorganize the **same schema** into **coherent
domain-intelligence families**: one supervisor per domain, each owning a *small set of focused
sub-agents that belong together*, so an agent means something ("Box Intelligence") instead of
being a random pile.

Inspired by the groupings in
`cuga-agent-skills-branch/docs/new/explorations/activepieces_agents.html` (Enterprise Productivity,
Market & Research Intelligence, document-/recording-/code-centric flows).

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

## The organizing principle

- **One domain = one file = one supervisor.** A domain is a *source of events + a purpose*, not a
  single integration. "Box Intelligence" is about documents, so it also fields email attachments;
  "Team Comms" spans Slack + Discord + Telegram because they're the same job on different transports.
- **Each sub-agent does ONE meaningful thing** and declares the exact triggers it HANDLES (mirrored
  from `events/triggers.py`). No sub-agent is a catch-all.
- **Tools stay within the real MCP set**: `cuga-finance · cuga-knowledge · cuga-geo · cuga-web ·
  cuga-code · cuga-text`. No invented servers.
- **Supervisor name stays `cuga`** so a file is drop-in: events still address the one agent `cuga`;
  only its roster (and personality) changes per domain.

## How to test one

```sh
# from repo root
cp supervisor_agents.yaml supervisor_agents.yaml.bak      # back up the flat roster
cp rosters/box_document_intelligence.yaml supervisor_agents.yaml
make reload                                                # rebuild the supervisor
# …exercise it (synth-fire a box/new_file, or chat)…
cp supervisor_agents.yaml.bak supervisor_agents.yaml       # restore when done
```

Swap in a different file to test a different family. Because each is a smaller, themed roster, the
supervisor's routing is easier to reason about and to score with `make test-delegation`.

## Where this points (future, needs code)

Today the runtime is a **single-level** supervisor over a flat list. The natural next step these
files set up: a **two-level hierarchy** — a top `cuga` that routes to a *domain supervisor*
(Box / Inbox / Repo / Comms / Research), which then picks the specialist. That's a code change
(nested supervisors) and is intentionally NOT done here — these files are the content that would
populate such a hierarchy, and are useful now as standalone test rosters.
