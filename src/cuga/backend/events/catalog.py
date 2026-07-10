"""The Examples catalog — the click-to-load utterances behind the Studio **Examples** tab AND the
filterable ``events_docs/api/examples.html`` board (keep the two in sync; this file is the source of truth).

Agents are PRE-BUILT (see ``seed.py``); the runtime concierge ROUTES an utterance to one outcome.
Each example is tagged so it can be filtered by **trigger · channel · integration · phase · live**:

  trigger    ∈ now | cron | poll | push | connect | decline
  channel    ∈ web | telegram | discord | slack | whatsapp | email | any   (where the human talks)
  integration∈ none | box | github | gmail | slack | webhook | calendar | drive | notion | rss
  phase      ∈ run | sprint | fly            (crawl→walk→RUN(MVP)→SPRINT→FLY)
  live       = True when it runs end-to-end TODAY; False = aspirational (needs that channel/integration)

``outcome`` (answer-now|flow-cron|flow-poll|connect|decline) is kept for back-compat with the Studio.
"""

from __future__ import annotations


def _ex(id, title, trigger, utterance, *, agent="—", channel="web", integration="none",
        phase="run", live=False, note="", star=False):
    outcome = {"now": "answer-now", "cron": "flow-cron", "poll": "flow-poll",
               "push": "flow-push", "connect": "connect", "decline": "decline"}[trigger]
    return {"id": id, "title": title, "trigger": trigger, "outcome": outcome,
            "utterance": utterance, "agent": agent, "channel": channel,
            "integration": integration, "phase": phase, "live": live, "note": note,
            "star": star}   # star = a curated "recommended starter flow" (featured in the UI)


EXAMPLES = [
    # ─────────────────────────── NOW — answer immediately ───────────────────────────
    _ex("now-price", "Crypto price now", "now", "what is the current price of bitcoin in usd?",
        agent="pricebot", channel="telegram", phase="run", live=True,
        note="router → pricebot → cuga_finance → number"),
    _ex("now-geo", "Geography + memory", "now", "what is the capital of Japan?",
        agent="geobot", channel="web", phase="run", live=True,
        note="follow with 'and its population?' — per-thread memory holds"),
    _ex("now-weather", "Weather now", "now", "what's the weather in Tokyo right now?",
        agent="weatherbot", channel="telegram", phase="run", live=True),
    _ex("now-arxiv", "Latest papers now", "now", "3 latest arXiv papers on mixture-of-experts",
        agent="papers", channel="web", phase="run", live=True, note="router → papers (one-shot)"),
    # ── cuga-apps-inspired agents ──
    _ex("now-research", "Research a topic", "now",
        "research parameter-efficient fine-tuning (LoRA) — papers + web, with citations",
        agent="research_compass", channel="web", phase="run", live=True,
        note="cuga-apps: Paper Scout + Web Researcher (cuga-knowledge + cuga-web)"),
    _ex("now-city", "City briefing", "now", "brief me on Lisbon — weather, key facts, things to do",
        agent="city_briefing", channel="web", phase="run", live=True,
        note="cuga-apps: City Beat + Travel Planner (cuga-geo + cuga-web + cuga-knowledge)"),
    _ex("now-code", "Audit a code snippet", "now",
        "check this Python function for syntax and complexity: def f(x): return x*2",
        agent="code_auditor", channel="web", phase="run", live=True,
        note="cuga-apps: Code Reviewer (cuga-code + cuga-web)"),
    # ── agents that reach the cuga-web tools BEYOND web_search (fetch_webpage / feeds / youtube) ──
    _ex("now-webpage", "Summarize a web page", "now", "summarize https://arxiv.org/abs/1706.03762",
        agent="webpage_summarizer", channel="web", phase="run", live=True,
        note="cuga-apps: Webpage Summarizer — the only agent that calls cuga-web.fetch_webpage"),
    _ex("now-video", "Ask about a YouTube video", "now",
        "what does this talk say about attention? https://www.youtube.com/watch?v=iDulhoQ2pro",
        agent="video_qa", channel="web", phase="run", live=True,
        note="cuga-apps: Video QA — cuga-web.get_youtube_transcript + get_youtube_video_info"),
    _ex("now-feed", "What's new in a feed", "now", "what's new on https://hnrss.org/frontpage ?",
        agent="feed_watcher", channel="web", phase="run", live=True,
        note="cuga-apps: AI Labs News / Newsletter — cuga-web.fetch_feed; the natural CRON/POLL agent"),
    _ex("now-trip", "Plan an outdoor day", "now", "plan an outdoor day near Boulder, Colorado",
        agent="trip_planner", channel="web", phase="run", live=True,
        note="cuga-apps: Hiking Research + Travel Planner — cuga-geo.find_hikes/search_attractions"),
    _ex("now-discord", "Ask from Discord", "now", "what's the price of ethereum?",
        agent="pricebot", channel="discord", phase="run", live=False,
        note="same path as Telegram; Discord send wired, not yet round-trip-verified"),
    _ex("now-slack", "Ask from Slack", "now", "capital of Brazil?",
        agent="geobot", channel="slack", phase="run", live=False,
        note="Slack send needs sendAsBot=true; wired, not verified"),
    _ex("now-whatsapp", "Ask from WhatsApp", "now", "weather in Mumbai?",
        agent="weatherbot", channel="whatsapp", phase="sprint", live=False,
        note="WhatsApp channel — Sprint (needs Meta/Twilio business onboarding)"),

    # ─────────────────────────── CRON — scheduled at a time ───────────────────────────
    _ex("cron-arxiv", "arXiv digest (daily)", "cron", "every day at 9am send me new arXiv papers on mixture-of-experts",
        agent="papers", channel="telegram", phase="run", live=True,
        note="AP CRON flow → papers → Telegram send; asking again REUSES it (dedup)"),
    _ex("cron-brief", "Weekday market brief", "cron", "every weekday at 8am send me a market brief",
        agent="market_briefer", channel="telegram", phase="run", live=True,
        note="cron 0 8 * * 1-5 → market_briefer → deliver"),
    _ex("cron-feed", "Daily feed digest", "cron",
        "every morning at 8am tell me what's new on https://hnrss.org/frontpage",
        agent="feed_watcher", channel="slack", phase="run", live=True,
        note="the honest CRON demo: a feed really does change, so 'what's new' means something"),
    _ex("cron-slack-digest", "Overnight support digest → Slack", "cron",
        "every morning at 7am post an overnight support digest to our Slack channel",
        agent="support_digest", channel="slack", integration="slack", phase="run", live=False,
        note="shared-Slack delivery; digest agent wired"),
    _ex("cron-email-brief", "Daily brief to my email", "cron",
        "every day at 8am email me a market brief",
        agent="market_briefer", channel="email", integration="gmail", phase="run", live=False,
        note="EMAIL delivery sink (Run) — deliver to Gmail, not a chat channel"),
    _ex("cron-calendar", "Morning agenda from Calendar", "cron",
        "every weekday at 7:30am send me today's calendar agenda",
        agent="—", channel="telegram", integration="calendar", phase="sprint", live=False,
        note="Google Calendar integration — Sprint"),

    # ─────────────────────────── POLL — every N, report on change ───────────────────────────
    _ex("poll-price", "Price move watcher", "poll", "watch bitcoin every 2 minutes and ping me on any move",
        agent="pricebot", channel="telegram", phase="run", live=True,
        note="POLL: a no-change tick delivers nothing (emit-on-change)"),
    _ex("poll-papers", "New-papers watcher", "poll",
        "every 5 minutes check for new MoE papers and tell me only if there are new ones",
        agent="papers", channel="telegram", phase="run", live=True),
    _ex("poll-rss", "Watch an RSS feed", "poll",
        "every 15 minutes check this RSS feed and message me new items",
        agent="—", channel="telegram", integration="rss", phase="sprint", live=False,
        note="RSS/feeds integration — Sprint"),

    # ─────────────────────────── PUSH — an integration event fires ───────────────────────────
    _ex("push-box-resume", "Box resume watcher", "push",
        "when a new resume lands in my Box, judge it against the JD and email me",
        agent="resume_judge", channel="email", integration="box", phase="run", live=False,
        note="Box new_file → resume_judge → Gmail; wired, not live-verified"),
    _ex("push-github-pr", "GitHub PR reviewer", "push",
        "when a new PR opens on psf/requests, summarize it and flag risks",
        agent="pr_reviewer", channel="telegram", integration="github", phase="run", live=True,
        note="GitHub PUSH → pr_reviewer reads the real diff (recommended FIRST push test)"),
    _ex("push-github-issue", "GitHub issue triage", "push",
        "when a new issue is filed, label it and post a summary to Slack",
        agent="—", channel="slack", integration="github", phase="run", live=False),
    _ex("push-gmail", "Important-email watcher", "push",
        "when an email from my boss arrives, summarize it and text me",
        agent="mailbot", channel="telegram", integration="gmail", phase="run", live=False,
        note="Gmail PUSH (per-user OAuth)"),
    _ex("push-webhook-in", "Generic inbound webhook", "push",
        "when my system POSTs to my webhook, run the triage agent and reply",
        agent="incident_triage", channel="slack", integration="webhook", phase="run", live=True,
        note="GENERIC WEBHOOK-IN — any external system → incident_triage → Slack, no bespoke piece"),
    _ex("cron-trending", "GitHub trending → Slack (hourly)", "cron",
        "every hour post the top trending GitHub repos in this channel",
        agent="github_trending", channel="slack", phase="run", live=True,
        note="AP CRON → github_trending (browses the web) → direct Slack delivery"),
    _ex("now-email-summary", "Summarize a new email", "push",
        "when a new email arrives in my inbox, summarize it and message me",
        agent="mailbot", channel="telegram", integration="gmail", phase="run", live=True,
        note="Gmail new-email PUSH → mailbot summarizes the payload (per-user OAuth)"),
    _ex("push-notion", "Notion task watcher", "push",
        "when a task is added to my Notion board, draft a plan and DM me",
        agent="—", channel="telegram", integration="notion", phase="sprint", live=False,
        note="Notion/Jira/Linear 'work' tool — Sprint"),
    _ex("push-drive", "New file in Drive", "push",
        "when a file is added to this Drive folder, summarize it",
        agent="—", channel="telegram", integration="drive", phase="sprint", live=False),

    # ────────── per-agent round-out: give each agent examples across the modes it supports ──────────
    _ex("cron-price", "Daily price ping", "cron", "every day at 9am send me the price of bitcoin",
        agent="pricebot", channel="telegram", phase="run", live=True,
        note="pricebot on a CRON clock (vs the NOW price / the POLL move-watcher)"),
    _ex("cron-weather", "Morning weather", "cron", "every morning at 7am send me the weather in New York",
        agent="weatherbot", channel="telegram", phase="run", live=True,
        note="weatherbot on a schedule (vs the NOW 'weather right now')"),
    _ex("poll-weather", "Rain watcher", "poll",
        "check the New York weather every hour and ping me only if rain is forecast",
        agent="weatherbot", channel="telegram", phase="run", live=True,
        note="weatherbot POLL — emit only on the condition"),
    _ex("now-market", "Market brief now", "now", "give me a market brief right now",
        agent="market_briefer", channel="web", phase="run", live=True,
        note="market_briefer answering NOW (vs its CRON/weekday brief)"),
    _ex("cron-research", "Weekly research digest", "cron",
        "every Monday at 9am send me a research digest on parameter-efficient fine-tuning",
        agent="research_compass", channel="telegram", phase="run", live=True,
        note="research_compass on a CRON clock (vs its NOW research)"),
    _ex("now-trending", "Trending repos now", "now", "show me the top trending GitHub repos right now",
        agent="github_trending", channel="web", phase="run", live=True,
        note="github_trending answering NOW (vs its hourly CRON → Slack)"),

    # ─────── ⭐ RECOMMENDED starter flows — one per integration/mode, via the /automate command ───────
    # (star=True → featured & sorted first in the Studio Examples tab. The canonical "try this first"
    # set: a standing flow for each integration plus a cron + poll, all through the one command.)
    _ex("automate-gmail", "⭐ Gmail — summarize new email", "push",
        "/automate summarize new emails and message me",
        agent="mailbot", channel="web", integration="gmail", phase="run", live=True, star=True,
        note="Gmail PUSH → mailbot summarizes each new email and delivers it to you"),
    _ex("automate-box", "⭐ Box — judge new resumes", "push",
        "/automate when a resume lands in Box, judge it and message me",
        agent="resume_judge", channel="web", integration="box", phase="run", live=False, star=True,
        note="DIRECT box (EVENTS_BOX_BACKEND=direct): /automate arms a schedule→/box/poll watcher — no "
             "OAuth, no AP box connection. Set BOX_FOLDER_ID + a fresh BOX_DEV_TOKEN; fires resume_judge per new file"),
    _ex("automate-pr", "⭐ GitHub — watch a repo's PRs", "push",
        "/automate new pull requests on psf/requests and summarize them",
        agent="pr_reviewer", channel="telegram", integration="github", phase="run", live=True, star=True,
        note="GitHub PUSH → pr_reviewer. Name the repo owner/repo. Connect GitHub via OAuth "
             "(scopes repo + admin:repo_hook) — a pasted PAT is not accepted by AP's github piece"),
    _ex("automate-brief", "⭐ Schedule — weekday market brief", "cron",
        "/automate the market brief every weekday at 8am",
        agent="market_briefer", channel="telegram", phase="run", live=True, star=True,
        note="CRON (0 8 * * 1-5) → market_briefer → delivered on a fixed clock"),
    _ex("automate-price", "⭐ Poll — bitcoin move watcher", "poll",
        "/automate check bitcoin every 5 minutes and ping me on a move",
        agent="pricebot", channel="telegram", phase="run", live=True, star=True,
        note="POLL (interval + act-on-change) → pricebot pings only when the price moves"),

    # ─────────────────────────── OUTBOUND webhook sink ───────────────────────────
    _ex("sink-webhook", "Deliver to a webhook", "poll",
        "watch bitcoin every 5 minutes and POST any move to this URL",
        agent="pricebot", channel="web", integration="webhook", phase="run", live=False,
        note="GENERIC WEBHOOK-OUT (MVP) — deliver to any HTTP endpoint; enables flow→flow chaining"),

    # ─────────────────────────── CONNECT — just-in-time per-user login ───────────────────────────
    _ex("connect-gmail", "My Gmail summary (login)", "connect", "summarize my gmail every morning",
        agent="mailbot", channel="telegram", integration="gmail", phase="run", live=False,
        note="mailbot uses Gmail (per-user) → concierge replies CONNECT NEEDED with an OAuth link"),
    _ex("connect-box", "Connect my Box", "connect", "watch my Box folder for new resumes",
        agent="resume_judge", channel="web", integration="box", phase="run", live=False,
        note="per-user Box login before the watcher can arm"),

    # ─────────────────────────── DECLINE — nothing fits (never invents an agent) ───────────────────────────
    _ex("decline-flight", "No agent (declines)", "decline", "book me a flight to Tokyo next Friday",
        agent="—", channel="telegram", phase="run", live=True,
        note="nothing fits → 'no agent set up for that; ask a builder'"),
    _ex("decline-vague", "Ambiguous → clarify/decline", "decline", "do the thing with the stuff",
        agent="—", channel="web", phase="run", live=True,
        note="unresolvable → decline rather than guess"),
]


def as_list() -> list[dict]:
    return list(EXAMPLES)
