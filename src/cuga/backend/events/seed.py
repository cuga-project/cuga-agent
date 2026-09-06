"""Seed pre-built agents — what a BUILDER creates at design time.

In the refined model the concierge NEVER creates agents; a builder does (skill + MCP tools +
policies + the channels/integrations the agent may use). Until the builder UI lands, this module
seeds a demo fleet so the runtime router (answer-now / reuse-or-create-flow / decline) has agents
to work with — and so tests/UI have a realistic starting point.

Each agent declares:
  - ``mcp_servers``   — its tools (from mcp_catalog; loaded via the CUGA registry).
  - ``channels``      — where it can converse (web / telegram / …).
  - ``integrations``  — apps it can watch/act on, each with credential OWNERSHIP:
        'shared'   → the builder connects a service account once.
        'per-user' → each chatting user logs in with their own (OAuth/token) at first use.

Enable with ``EVENTS_SEED_AGENTS=1`` (main.py seeds on startup). Idempotent (upsert by name).
"""

from __future__ import annotations

import logging

try:
    from .runtime import AgentSpec, DEFAULT_SCOPE
except ImportError:  # flat load (offline tests)
    from runtime import AgentSpec, DEFAULT_SCOPE

log = logging.getLogger("cuga.events.seed")


def _worker_backend() -> str:
    import os

    return os.environ.get("EVENTS_WORKER_BACKEND", "cuga")


def default_agents(backend: str | None = None) -> list[AgentSpec]:
    """The demo fleet. Names are GENERAL/reusable (pricebot handles any price, not just BTC)."""
    b = backend or _worker_backend()
    web = ["web", "telegram"]
    return [
        AgentSpec(
            name="pricebot",
            backend=b,
            mcp_servers=["cuga_finance"],
            channels=web,
            prompt="You answer crypto/stock price questions concisely using your tools "
            "(get_crypto_price / get_stock_quote). Give the price, the 24h change, and a "
            "one-line read on the move.",
        ),
        AgentSpec(
            name="geobot",
            backend=b,
            mcp_servers=["cuga_knowledge", "cuga_geo"],
            channels=web,
            prompt="You answer country/geography questions — capital, population, region — by looking "
            "them up on Wikipedia (cuga_knowledge: search_wikipedia / get_article_summary). "
            "You can also geocode a place and find nearby hikes/attractions (cuga_geo).",
        ),
        AgentSpec(
            name="weatherbot",
            backend=b,
            mcp_servers=["cuga_web"],
            channels=web,
            prompt="You answer current-weather questions for a city.",
        ),
        AgentSpec(
            name="papers",
            backend=b,
            mcp_servers=["cuga_knowledge"],
            channels=web,
            prompt="You find and summarize recent arXiv papers on a topic.",
        ),
        AgentSpec(
            name="market_briefer",
            backend=b,
            mcp_servers=["cuga_finance", "cuga_web"],
            channels=web,
            access=["builder", "admin"],  # restricted → demonstrates perms
            prompt="You produce a short market brief on request.",
        ),
        # ── cuga-apps-inspired agents (see cuga-apps/apps/*): richer, tool-combining skills ──
        AgentSpec(
            name="research_compass",
            backend=b,
            mcp_servers=["cuga_knowledge", "cuga_web"],
            channels=web,  # inspired by cuga-apps: Paper Scout + Web Researcher
            integrations=[
                {"app": "slack", "ownership": "shared", "triggers": ["new_slack_mention", "saved_message"]}
            ],
            prompt="Research any topic. Search arXiv + Semantic Scholar (cuga_knowledge) AND the "
            "web (cuga_web), then synthesize the findings with citations, name the 2-3 most "
            "important papers/sources, and suggest what to read next. Be concrete.",
        ),
        AgentSpec(
            name="city_briefing",
            backend=b,
            mcp_servers=["cuga_geo", "cuga_web", "cuga_knowledge"],
            channels=web,  # inspired by cuga-apps: City Beat + Travel Planner
            prompt="Given a city, produce a one-screen briefing: geocode it and get current weather "
            "(cuga_geo), 3-5 key facts (cuga_knowledge/Wikipedia), and 3-5 things to do "
            "(attractions/hikes). Tight, scannable, bulleted.",
        ),
        AgentSpec(
            name="code_auditor",
            backend=b,
            mcp_servers=["cuga_code", "cuga_web"],
            channels=web,
            prompt="Analyze a code snippet the user pastes: check syntax, detect the language, and "
            "report metrics (size/complexity) via cuga_code. If they ask about a library or "
            "API, look it up on the web. Give a short, actionable verdict.",
        ),
        # ── agents that reach the cuga_web tools BEYOND web_search. Before these, the whole fleet
        #    used exactly one of that server's seven tools (see tests/events/AGENT_NOW_CATALOG.md).
        AgentSpec(
            name="webpage_summarizer",
            backend=b,
            mcp_servers=["cuga_web"],
            channels=web,
            integrations=[{"app": "telegram", "ownership": "shared", "triggers": ["new_channel_message"]}],
            prompt="Given a URL, fetch the page (fetch_webpage) and summarize it: what it is, the "
            "3-5 key points, and who should care. If asked what a page links to, use "
            "fetch_webpage_links. If given a topic instead of a URL, web_search for it "
            "first, then fetch the best result. Never summarize a page you did not fetch.",
        ),
        AgentSpec(
            name="video_qa",
            backend=b,
            mcp_servers=["cuga_web"],
            channels=web,
            prompt="Answer questions about a YouTube video. Use get_youtube_video_info for title/"
            "channel/duration and get_youtube_transcript for what was actually said. Quote "
            "the transcript when you answer, and say so if the video has no transcript.",
        ),
        AgentSpec(
            name="feed_watcher",
            backend=b,
            mcp_servers=["cuga_web"],
            channels=web,
            prompt="Report what is new in an RSS/Atom feed. Use fetch_feed for one feed, or "
            "search_feeds to scan several for keywords. List items newest-first with title, "
            "date, and a one-line summary. This agent is built to run on a schedule: when "
            "asked what changed, report ONLY items you have not reported before.",
        ),
        AgentSpec(
            name="trip_planner",
            backend=b,
            mcp_servers=["cuga_geo", "cuga_web"],
            channels=web,
            prompt="Plan an outdoor day near a place. Geocode it (geocode), then find hikes "
            "(find_hikes) and attractions (search_attractions), and check the weather "
            "(get_weather) before recommending. Give 3-5 options with distance and a reason.",
        ),
        # ── more cuga-apps ports (cuga-apps/apps/*) — each mapped onto the generic cuga-* tool
        #    servers (the apps' bespoke tools aren't in this registry, so we do it the github_trending
        #    way: reach the same goal with web_search / fetch_webpage / the knowledge+geo tools) ──
        AgentSpec(
            name="ai_labs_news",
            backend=b,
            mcp_servers=["cuga_web"],
            channels=["web", "slack", "telegram"],  # digest-y → demoes scheduled delivery
            prompt="Produce a glanceable digest of the latest posts from the major AI labs "
            "(OpenAI, Anthropic, Google DeepMind, Meta AI, Mistral, …). Use web_search / "
            "fetch_feed / fetch_webpage to pull their recent blog & research posts. If the "
            "user names labs or topics, focus there. List newest-first: lab · title · date · "
            "one-line takeaway. Built to run on a schedule — when asked what's new, report "
            "ONLY posts you have not reported before.",
        ),
        AgentSpec(
            name="wiki_dive",
            backend=b,
            mcp_servers=["cuga_knowledge"],
            channels=web,
            prompt="Deep-dive a topic on Wikipedia — not just the lead. search_wikipedia to find "
            "the article, get_article_summary for the intro, then get_article_sections to "
            "read specific sections in depth, following cross-links to related articles. "
            "Synthesize a structured explanation, cite the sections used, and point to "
            "related articles worth reading next.",
        ),
        AgentSpec(
            name="movie_recommender",
            backend=b,
            mcp_servers=["cuga_web"],
            channels=web,
            prompt="Recommend movies/shows from a free-form description of the user's taste. You "
            "are STATELESS — infer everything from the current message (liked titles, mood, "
            "genre, era, constraints). Use web_search to ground picks in real, current "
            "titles and reviews. Return 4-6 recommendations, each with a one-line "
            "why-you'll-like-it tied to what they said. Never assume a preference unstated.",
        ),
        AgentSpec(
            name="recipe_composer",
            backend=b,
            mcp_servers=["cuga_web", "cuga_text"],
            channels=web,
            prompt="Compose a recipe from a free-form request. You are STATELESS — use only what "
            "the message states (ingredients on hand, cuisine, diet, allergies, time). Use "
            "web_search to ground techniques, ratios, and substitutions when unsure. Return "
            "a titled recipe: ingredients with quantities, numbered steps, total time, and "
            "one substitution tip. Never assume a pantry item or restriction not stated.",
        ),
        AgentSpec(
            name="meetup_finder",
            backend=b,
            mcp_servers=["cuga_web"],
            channels=web,
            prompt="Find upcoming events/meetups (tech/AI by default) for a topic + city + "
            "timeframe. Use web_search and fetch_webpage over Meetup, Luma, and Eventbrite "
            "discovery pages. List 5-8 soonest-first: name · date · venue/online · one-line "
            "what-and-who, each linked. Say a source had nothing rather than inventing events.",
        ),
        AgentSpec(
            name="youtube_research",
            backend=b,
            mcp_servers=["cuga_web"],
            channels=web,
            prompt="Research a topic across YouTube. Given a topic (no URL), web_search for the "
            "best videos, then get_youtube_video_info + get_youtube_transcript on the top "
            "few and synthesize what the creators actually say — attributing points to "
            "specific videos. Given a URL, answer from that video's transcript. Quote "
            "transcripts; note when a video has none. (Broader than video_qa's single video.)",
        ),
        AgentSpec(
            name="find_a_doctor",
            backend=b,
            mcp_servers=["cuga_web", "cuga_geo"],
            channels=web,
            prompt="Help find a good doctor/provider in a location, grounded in real listings and "
            "review snippets. Geocode the location if useful (cuga_geo), then web_search "
            "trusted directories and fetch_webpage the best listings for specialty, "
            "experience, and patient-review signals. Return 3-6 candidates: name · specialty "
            "· location · why they fit (with a source). Never fabricate a provider or review.",
        ),
        AgentSpec(
            name="ibm_docs_qa",
            backend=b,
            mcp_servers=["cuga_web"],
            channels=web,
            integrations=[{"app": "discord", "ownership": "shared", "triggers": ["new_channel_message"]}],
            prompt="Answer IBM Cloud questions from real IBM documentation. For each question, "
            "web_search with `site:cloud.ibm.com` or `site:ibm.com` prepended, review the "
            "snippets, fetch_webpage the most relevant doc, and answer grounded in it. Cite "
            "the doc URL. If the docs don't cover it, say so rather than guessing.",
        ),
        # agents that ACT ON integrations — these drive the per-user login (OAuth) story
        AgentSpec(
            name="mailbot",
            backend=b,
            mcp_servers=["cuga_text"],
            channels=web,
            integrations=[{"app": "gmail", "ownership": "per-user"}],
            prompt="You summarize and triage the user's Gmail. Uses their own Gmail login.",
        ),
        AgentSpec(
            name="resume_judge",
            backend=b,
            mcp_servers=["cuga_text"],
            channels=web,
            integrations=[{"app": "box", "ownership": "per-user"}, {"app": "gmail", "ownership": "per-user"}],
            # The watcher inlines the resume's TEXT into the message — CUGA downloads it
            # server-side, because the agent holds no Box credential. Without saying so, the
            # agent reaches for its extract_text tool, finds no file on disk, and replies "the
            # resume could not be found" while the text sits in front of it.
            prompt=(
                "You judge resumes. The resume's full text is given to you INLINE in the "
                "message, between '--- contents of ... ---' markers — read it there. Do NOT "
                "look for a file on disk, and never say the file is missing. If the message "
                "says the file is binary instead, decode event.payload.file_base64 with "
                "extract_text_from_bytes. Judge fit against the job description in the "
                "message: start your reply with MATCH or SKIP, then two lines of reasoning "
                "citing specifics from the resume."
            ),
        ),
        AgentSpec(
            name="support_digest",
            backend=b,
            mcp_servers=["cuga_web"],
            channels=["web", "slack"],
            integrations=[
                {
                    "app": "slack",
                    "ownership": "shared",
                    "triggers": ["channel_created", "new_slack_user", "new_emoji"],
                },
                {"app": "discord", "ownership": "shared", "triggers": ["new_member"]},
                {"app": "box", "ownership": "per-user", "triggers": ["new_folder"]},
            ],
            prompt="You post an overnight support digest to a shared Slack channel, and handle "
            "workspace-lifecycle events: welcome a new teammate or server member with a "
            "short onboarding note, announce a newly-created channel with a suggested "
            "charter, note a new custom emoji, or index a new Box folder.",
        ),
        AgentSpec(
            name="pr_reviewer",
            backend=b,
            mcp_servers=["cuga_code", "cuga_text"],
            channels=web,
            # trigger-grain declaration: WHICH github events this agent handles. Issues and
            # repo-lifecycle events belong to incident_triage / repo_watcher below.
            integrations=[
                {"app": "github", "ownership": "per-user", "triggers": ["new_pr", "new_review_request"]}
            ],
            prompt="When a pull request opens (or your review is requested), summarize what it "
            "changes and flag risks (bugs, security, breaking changes).",
        ),
        # repo lifecycle watcher — the home for github's NON-PR/issue triggers (star, release,
        # push, branch, milestone, …). One thin agent instead of overloading pr_reviewer, and it
        # keeps per-user github OFF agents that have live cron examples (their connect gate).
        AgentSpec(
            name="repo_watcher",
            backend=b,
            mcp_servers=["cuga_text", "cuga_web"],
            channels=["web", "slack", "telegram"],
            integrations=[
                {
                    "app": "github",
                    "ownership": "per-user",
                    "triggers": [
                        "new_star",
                        "new_release",
                        "new_push",
                        "new_commit",
                        "new_branch",
                        "new_collaborator",
                        "new_repo_label",
                        "new_milestone",
                        "new_discussion",
                        "new_gh_mention",
                    ],
                }
            ],
            prompt="You announce and summarize repository lifecycle events (stars, releases, "
            "pushes, commits, branches, collaborators, labels, milestones, discussions, "
            "mentions). Read the event payload you are given, say plainly what happened "
            "and who did it, and add one useful next step when it is obvious (e.g. a "
            "release → one-line changelog digest; a push → note what changed). Concise, "
            "chat-friendly.",
        ),
        # a scheduled digest agent → posts to a chat channel (demoes CRON → Slack delivery)
        AgentSpec(
            name="github_trending",
            backend=b,
            mcp_servers=["cuga_web"],
            channels=["web", "slack", "telegram"],
            prompt="Report the current top trending GitHub repositories (search the web / browse "
            "github.com/trending). For each of ~5–7 repos give: name, primary language, and "
            "a one-line description of what it does and why it's notable. Concise, bulleted "
            "— this posts to a busy chat channel.",
        ),
        # the generic inbound-webhook worker: any external system POSTs a payload → this triages it
        AgentSpec(
            name="incident_triage",
            backend=b,
            mcp_servers=["cuga_text"],
            channels=["web", "slack"],
            # triage-shaped triggers beyond the webhook: new github issues / blocker comments,
            # :bug:-reaction escalations, box file-comment action items. slack is a DIRECT
            # backend (no AP connection) and box comments ride the direct poller.
            integrations=[
                {
                    "app": "github",
                    "ownership": "per-user",
                    "triggers": ["new_issue", "new_discussion_comment"],
                },
                {
                    "app": "slack",
                    "ownership": "shared",
                    "triggers": ["new_reaction", "reaction_removed", "new_channel_message"],
                },
                {"app": "box", "ownership": "per-user", "triggers": ["new_box_comment"]},
            ],
            prompt="You triage an inbound alert/incident payload from a webhook or watcher "
            "(a monitoring alert, a new GitHub issue, a :bug: reaction, a comment "
            "flagging a blocker). Summarize what happened in one line, classify severity "
            "(P1/P2/P3), name the likely component, and suggest the first action. Be "
            "concise — this goes to a busy on-call channel.",
        ),
    ]


def seed_default_agents(runtime, scope: str = DEFAULT_SCOPE, backend: str | None = None) -> list[str]:
    """Upsert the demo fleet into the runtime's store for ``scope``. Returns the names.

    NOTE: agents are per-``scope`` in the AgentStore. So that BOTH alice and bob (distinct
    scopes) can use the fleet, seed under each user's scope — or use tenant-shared agents. For
    the demo we seed the DEFAULT scope + each seeded user's scope (see main.py)."""
    names = []
    for spec in default_agents(backend):
        runtime.upsert_agent(spec, scope=scope)
        names.append(spec.name)
    log.info("seeded %d agents into scope=%s: %s", len(names), scope, ", ".join(names))
    return names


def seed_default_users(user_store, tenant: str = "default") -> list[str]:
    """A demo org: an admin/builder + two plain users (for two-user isolation tests)."""
    user_store.add(
        "admin", email="admin@acme.test", roles=["admin", "builder", "user"], password="admin", tenant=tenant
    )
    user_store.add("alice", email="alice@acme.test", roles=["user"], password="alice", tenant=tenant)
    user_store.add("bob", email="bob@acme.test", roles=["user"], password="bob", tenant=tenant)
    log.info("seeded users into tenant=%s: admin, alice, bob", tenant)
    return ["admin", "alice", "bob"]
