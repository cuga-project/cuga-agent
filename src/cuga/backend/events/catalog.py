"""The Examples catalog — the click-to-load utterances behind the Studio **Examples** tab AND the
filterable ``roadmap/examples.html`` board (keep the two in sync; this file is the source of truth).

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
        phase="run", live=False, note=""):
    outcome = {"now": "answer-now", "cron": "flow-cron", "poll": "flow-poll",
               "push": "flow-push", "connect": "connect", "decline": "decline"}[trigger]
    return {"id": id, "title": title, "trigger": trigger, "outcome": outcome,
            "utterance": utterance, "agent": agent, "channel": channel,
            "integration": integration, "phase": phase, "live": live, "note": note}


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
