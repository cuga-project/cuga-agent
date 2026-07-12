"""The Examples catalog — the click-to-load utterances behind the Studio **Examples** tab AND the
filterable ``events_docs/api/examples.html`` board. This file is the SOLE source of truth: the board's
data array is generated from it by ``scripts/gen_examples.py`` and locked by a consistency test
(``test_examples_board_matches_the_catalog``), so add examples HERE and regenerate — never hand-edit
the HTML array.

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
        phase="run", live=False, note="", star=False, ap_trigger=""):
    outcome = {"now": "answer-now", "cron": "flow-cron", "poll": "flow-poll",
               "push": "flow-push", "connect": "connect", "decline": "decline"}[trigger]
    return {"id": id, "title": title, "trigger": trigger, "outcome": outcome,
            "utterance": utterance, "agent": agent, "channel": channel,
            "integration": integration, "phase": phase, "live": live, "note": note,
            "star": star,   # star = a curated "recommended starter flow" (featured in the UI)
            # ap_trigger = the SPECIFIC Activepieces piece trigger this example maps to
            # (e.g. "new_labeled_email"). Set → this is an ADVANCED, trigger-tied example: it is
            # rendered in the collapsible "Advanced" section grouped by integration, NOT the main list.
            "ap_trigger": ap_trigger}


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
    _ex("now-yt-research", "Research a topic via YouTube", "now",
        "what do top YouTube creators say about RAG vs fine-tuning?",
        agent="youtube_research", channel="web", phase="run", live=True,
        note="cuga-apps: YouTube Research — web_search + transcripts across several videos"),
    _ex("now-ailabs", "AI labs digest", "now", "what's new from OpenAI and Anthropic this week?",
        agent="ai_labs_news", channel="web", phase="run", live=True,
        note="cuga-apps: AI Labs News — web_search over the labs' blogs; a natural CRON digest"),
    _ex("now-wiki", "Deep Wikipedia dive", "now",
        "give me a deep dive on the history of the transistor — key sections + related topics",
        agent="wiki_dive", channel="web", phase="run", live=True,
        note="cuga-apps: Wiki Dive — get_article_sections + cross-links, not just the lead"),
    _ex("now-movies", "Movie recommendations", "now",
        "recommend movies like Arrival and Interstellar but a bit lighter",
        agent="movie_recommender", channel="web", phase="run", live=True,
        note="cuga-apps: Movie Recommender — taste → grounded picks via web_search"),
    _ex("now-recipe", "Compose a recipe", "now",
        "a quick vegetarian dinner with chickpeas, spinach and no dairy",
        agent="recipe_composer", channel="web", phase="run", live=True,
        note="cuga-apps: Recipe Composer — stateless, grounds ratios/substitutions on the web"),
    _ex("now-meetups", "Find meetups", "now", "AI/ML meetups in San Francisco in the next two weeks",
        agent="meetup_finder", channel="web", phase="run", live=True,
        note="cuga-apps: Meetup Finder — web_search over Meetup/Luma/Eventbrite"),
    _ex("now-doctor", "Find a doctor", "now",
        "an experienced pediatric dentist in Austin who's good with anxious kids",
        agent="find_a_doctor", channel="web", phase="run", live=True,
        note="cuga-apps: Find a Doctor — geocode + web listings & review snippets"),
    _ex("now-ibmdocs", "IBM Cloud docs Q&A", "now",
        "how do I set up autoscaling on IBM Cloud Kubernetes?",
        agent="ibm_docs_qa", channel="web", phase="run", live=True,
        note="cuga-apps: IBM Docs Q&A — web_search site:cloud.ibm.com, answer grounded in the doc"),
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
    _ex("push-webhook-ci", "CI/deploy failure webhook", "push",
        "when my CI posts a failed build, triage it and tell me what broke",
        agent="incident_triage", channel="slack", integration="webhook", phase="run", live=True,
        note="same GENERIC WEBHOOK-IN worker, a build-failure payload "
             "({repo, branch, job, status:failed, log_url}) — proves it isn't monitoring-specific"),
    _ex("push-webhook-lead", "Form/lead submission webhook", "push",
        "when a lead form is submitted, summarize it and post to our channel",
        agent="incident_triage", channel="slack", integration="webhook", phase="run", live=True,
        note="a lead/form payload ({name, email, company, message}) through the same worker — "
             "arbitrary JSON, no bespoke piece"),
    _ex("push-webhook-routed", "⭐ Webhook that picks its own agent", "push",
        "POST any event to /api/events/hook/<name>?route=1 — CUGA routes it like a chat message",
        agent="(auto-routed)", channel="slack", integration="webhook", phase="run", live=True,
        star=True,
        note="ROUTED webhook-IN: the caller names NO agent; the concierge picks the best-fit pre-built "
             "agent by capability, exactly like a Slack/web chat message (a PR payload → pr_reviewer, a "
             "payment dispute → incident_triage). Decouples the external system from the agent catalog"),
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

    # ═══════════════════════════════════════════════════════════════════════════════════════════════
    # ADVANCED — one example per REAL Activepieces trigger, tied to an existing agent.
    # Each names the exact piece trigger (``ap_trigger``) pulled from the running AP catalog, so it is
    # a flow AP could actually run. Today our NL→flow router maps each integration to its DEFAULT
    # trigger only (gmail→new_email, github→pull_request, box→new_file); selecting a NON-default
    # trigger is the next build step — so these are live=False (the AP trigger exists; our mapping to
    # it is pending). They are the design target for "watch X, not just the obvious thing."
    # ═══════════════════════════════════════════════════════════════════════════════════════════════

    # ── Gmail ── (triggers: gmail_new_email_received[✓ live above], new_labeled_email, new_attachment, new_label)
    _ex("adv-gmail-labeled", "Gmail · a label is applied", "push",
        "when I label an email 'Read-later', summarize it and send me the digest",
        agent="mailbot", channel="telegram", integration="gmail", phase="run", live=False,
        ap_trigger="new_labeled_email",
        note="fires when a LABEL is applied (triage-then-act), not on every inbound email"),
    _ex("adv-gmail-attach-resume", "Gmail · resume attachment", "push",
        "when an email arrives with a resume attached, judge it against the JD and message me",
        agent="resume_judge", channel="telegram", integration="gmail", phase="run", live=False,
        ap_trigger="new_attachment",
        note="resume_judge tied to a Gmail ATTACHMENT instead of a Box file — same agent, new source"),
    _ex("adv-gmail-attach-summ", "Gmail · any attachment", "push",
        "when an email attachment (PDF or doc) arrives, summarize its contents for me",
        agent="mailbot", channel="slack", integration="gmail", phase="run", live=False,
        ap_trigger="new_attachment",
        note="summarize the FILE, not the email body"),
    _ex("adv-gmail-newlabel", "Gmail · a new label is created", "push",
        "when a new Gmail label is created, announce it in our Slack",
        agent="incident_triage", channel="slack", integration="gmail", phase="run", live=False,
        ap_trigger="new_label",
        note="an org-structure change becomes an event — light-touch notify"),

    # ── Slack ── (14 triggers; the converse path is the Events API — these are the OTHER triggers)
    _ex("adv-slack-msg-anywhere", "Slack · any public message", "push",
        "when any public message mentions 'CUGA', flag it to our brand channel",
        agent="incident_triage", channel="slack", integration="slack", phase="run", live=False,
        ap_trigger="new-message",
        note="New Public Message Posted Anywhere → keyword/brand monitoring across the workspace"),
    _ex("adv-slack-msg-in-channel", "Slack · a message in a channel", "push",
        "when a message is posted in #incidents, triage it and thread a severity",
        agent="incident_triage", channel="slack", integration="slack", phase="run", live=False,
        ap_trigger="new-message-in-channel",
        note="New Message Posted to Channel → watch ONE channel (vs the converse/DM path)"),
    _ex("adv-slack-dm", "Slack · a DM to the bot", "push",
        "when someone DMs the bot, route it to the right agent and reply",
        agent="(auto-routed)", channel="slack", integration="slack", phase="run", live=False,
        ap_trigger="new-direct-message",
        note="New Direct Message → concierge routing (like the routed webhook)"),
    _ex("adv-slack-mention", "Slack · team @mention in a channel", "push",
        "when the team is @mentioned with a question, draft an answer for review",
        agent="research_compass", channel="slack", integration="slack", phase="run", live=False,
        ap_trigger="new_mention",
        note="New Mention in Channel → draft, don't auto-send"),
    _ex("adv-slack-mention-dm", "Slack · @mention in a DM", "push",
        "when I'm @mentioned in a DM thread, summarize what's being asked of me",
        agent="research_compass", channel="slack", integration="slack", phase="run", live=False,
        ap_trigger="new-mention-in-direct-message",
        note="New Mention in Direct Message → summarize the ask"),
    _ex("adv-slack-reaction-bug", "Slack · a :bug: reaction", "push",
        "when a message gets a :bug: reaction, triage it as an incident and thread the summary",
        agent="incident_triage", channel="slack", integration="slack", phase="run", live=False,
        ap_trigger="new_reaction_added",
        note="a REACTION as a trigger — emoji-as-a-verb (filter on the :bug: emoji)"),
    _ex("adv-slack-reaction-research", "Slack · a :bookmark: reaction", "push",
        "when I react :bookmark: to a message with a link, research the topic and DM me",
        agent="research_compass", channel="slack", integration="slack", phase="run", live=False,
        ap_trigger="new_reaction_added",
        note="save-to-research: the reaction picks the message, research_compass does the work"),
    _ex("adv-slack-reaction-removed", "Slack · a reaction is removed", "push",
        "when a :white_check_mark: is removed from a task message, re-open it and ping the owner",
        agent="incident_triage", channel="slack", integration="slack", phase="run", live=False,
        ap_trigger="new_reaction_removed",
        note="Reaction Removed → un-done detection (the inverse of a reaction trigger)"),
    _ex("adv-slack-channel-created", "Slack · a channel is created", "push",
        "when a new channel is created, post a welcome and suggest a charter",
        agent="support_digest", channel="slack", integration="slack", phase="run", live=False,
        ap_trigger="channel_created",
        note="workspace lifecycle event → onboarding"),
    _ex("adv-slack-command", "Slack · a slash command in a channel", "push",
        "when someone runs a /ask command in a channel, route it to an agent and reply",
        agent="(auto-routed)", channel="slack", integration="slack", phase="run", live=False,
        ap_trigger="new_command",
        note="New Command in Channel → concierge routing (a slash command as the trigger)"),
    _ex("adv-slack-command-dm", "Slack · a slash command in a DM", "push",
        "when someone runs /ask in a DM, answer it",
        agent="(auto-routed)", channel="slack", integration="slack", phase="run", live=False,
        ap_trigger="new-command-in-direct-message",
        note="New Command in Direct Message → same routing, DM-scoped"),
    _ex("adv-slack-saved", "Slack · a saved message", "push",
        "when I save a message, research anything it references and DM me the notes",
        agent="research_compass", channel="slack", integration="slack", phase="run", live=False,
        ap_trigger="new-saved-message",
        note="'save for later' → the agent actually does the later"),
    _ex("adv-slack-newuser", "Slack · a new teammate", "push",
        "when a new user joins the workspace, send them an onboarding brief",
        agent="support_digest", channel="slack", integration="slack", phase="run", live=False,
        ap_trigger="new-user",
        note="New User → personalized welcome"),
    _ex("adv-slack-emoji", "Slack · a new custom emoji", "push",
        "when a new custom emoji is added, post a fun announcement about it",
        agent="support_digest", channel="slack", integration="slack", phase="run", live=False,
        ap_trigger="new-team-custom-emoji",
        note="New Team Custom Emoji → workspace-culture housekeeping (a light-touch trigger)"),
    _ex("adv-slack-modal", "Slack · a submitted modal form", "push",
        "when a Slack modal form is submitted, process the fields and route them",
        agent="(auto-routed)", channel="slack", integration="slack", phase="run", live=False,
        ap_trigger="new-modal-interaction",
        note="New Modal Interaction → treat a form submission like an inbound payload"),

    # ── Discord ── (new_message, new_member)
    _ex("adv-discord-member", "Discord · a new member joins", "push",
        "when a new member joins the server, greet them and point to the resources",
        agent="support_digest", channel="discord", integration="discord", phase="run", live=False,
        ap_trigger="new_member",
        note="New Member → welcome + orient"),
    _ex("adv-discord-help", "Discord · a question in #help", "push",
        "when someone posts in #help, answer from the docs",
        agent="ibm_docs_qa", channel="discord", integration="discord", phase="run", live=False,
        ap_trigger="new_message",
        note="channel-scoped New Message → docs Q&A (vs the generic converse path)"),

    # ── Telegram ── (new_telegram_message)
    _ex("adv-telegram-url", "Telegram · a link is sent", "push",
        "when I send the bot a link, summarize the page and reply",
        agent="webpage_summarizer", channel="telegram", integration="telegram", phase="run", live=False,
        ap_trigger="new_telegram_message",
        note="New Update carrying a URL → webpage_summarizer (a content filter on the channel trigger)"),

    # ── GitHub ── (14 triggers; pull_request[✓ live above] is one — these are the rest)
    _ex("adv-gh-star", "GitHub · a new star", "push",
        "when my repo gets a new star, post a thank-you and who starred it",
        agent="github_trending", channel="slack", integration="github", phase="run", live=False,
        ap_trigger="trigger_star",
        note="New Star → social signal"),
    _ex("adv-gh-issue", "GitHub · a new issue", "push",
        "when a new issue is filed, triage its severity and suggest a label",
        agent="incident_triage", channel="slack", integration="github", phase="run", live=False,
        ap_trigger="trigger_issues",
        note="New Issue → triage + label suggestion"),
    _ex("adv-gh-release", "GitHub · a new release", "push",
        "when a new release is published, summarize the changelog for the team",
        agent="webpage_summarizer", channel="slack", integration="github", phase="run", live=False,
        ap_trigger="new_release",
        note="New Release → changelog digest"),
    _ex("adv-gh-push", "GitHub · a push to main", "push",
        "when code is pushed to main, audit the diff for obvious risks",
        agent="code_auditor", channel="slack", integration="github", phase="run", live=False,
        ap_trigger="trigger_push",
        note="Push → code_auditor on the diff"),
    _ex("adv-gh-discussion", "GitHub · a new discussion", "push",
        "when a new discussion opens, summarize it and surface related prior work",
        agent="research_compass", channel="slack", integration="github", phase="run", live=False,
        ap_trigger="trigger_discussion",
        note="New Discussion → summary + related work"),
    _ex("adv-gh-comment", "GitHub · a new comment", "push",
        "when a comment is posted on an issue, flag if it names a blocker",
        agent="incident_triage", channel="slack", integration="github", phase="run", live=False,
        ap_trigger="trigger_discussion_comment",
        note="New Comment → blocker detection"),
    _ex("adv-gh-review-req", "GitHub · review requested", "push",
        "when I'm requested to review a PR, summarize the diff and flag the risks",
        agent="pr_reviewer", channel="telegram", integration="github", phase="run", live=False,
        ap_trigger="new_review_request",
        note="New Review Request → pr_reviewer, scoped to PRs assigned to you"),
    _ex("adv-gh-commit", "GitHub · a new commit", "push",
        "when a commit lands on any branch, audit it for obvious bugs",
        agent="code_auditor", channel="slack", integration="github", phase="run", live=False,
        ap_trigger="new_commit",
        note="New Commit → per-commit audit"),
    _ex("adv-gh-milestone", "GitHub · a new milestone", "push",
        "when a milestone is created, draft a plan and a checklist for it",
        agent="support_digest", channel="slack", integration="github", phase="run", live=False,
        ap_trigger="new_milestone",
        note="New Milestone → planning"),
    _ex("adv-gh-branch", "GitHub · a new branch", "push",
        "when a new branch is created, note it and check it against our naming convention",
        agent="incident_triage", channel="slack", integration="github", phase="run", live=False,
        ap_trigger="new_branch",
        note="New Branch → convention check + log"),
    _ex("adv-gh-collaborator", "GitHub · a new collaborator", "push",
        "when a collaborator is added to the repo, log it and note their access level",
        agent="incident_triage", channel="slack", integration="github", phase="run", live=False,
        ap_trigger="new_collaborator",
        note="New Collaborator → access audit trail"),
    _ex("adv-gh-label", "GitHub · a new label", "push",
        "when a new label is created in the repo, announce it to the team",
        agent="incident_triage", channel="slack", integration="github", phase="run", live=False,
        ap_trigger="new_label",
        note="New Label → housekeeping notify"),
    _ex("adv-gh-mention", "GitHub · a mention of me", "push",
        "when my repo @mentions me anywhere, summarize the surrounding context",
        agent="research_compass", channel="telegram", integration="github", phase="run", live=False,
        ap_trigger="new_mention",
        note="New Mention → context summary so you can respond fast"),

    # ── Box ── (new_file[✓ live above], new_folder, new_comment)
    _ex("adv-box-folder", "Box · a new folder", "push",
        "when a new folder appears in Box, index its contents and summarize what's inside",
        agent="support_digest", channel="slack", integration="box", phase="run", live=False,
        ap_trigger="new_folder",
        note="New Folder → index + summary"),
    _ex("adv-box-comment", "Box · a comment on a file", "push",
        "when someone comments on a Box file, summarize the thread and flag action items",
        agent="incident_triage", channel="slack", integration="box", phase="run", live=False,
        ap_trigger="new_comment",
        note="New Comment → discussion summary + actions"),
    _ex("adv-box-file-summ", "Box · any document", "push",
        "when a document lands in Box, summarize it for me",
        agent="webpage_summarizer", channel="telegram", integration="box", phase="run", live=False,
        ap_trigger="new_file",
        note="New File → a SUMMARIZE flavor (vs resume_judge on the same trigger)"),

    # ── Webhook ── (not an AP piece — the generic inbound endpoint; its "trigger" is any POST.
    # The live pinned + routed + CI + lead examples are in the main list above; these round out the
    # per-source coverage in the Advanced section for completeness.)
    _ex("adv-webhook-pinned", "Webhook · pinned agent", "push",
        "POST any JSON to /api/events/hook/<name>?agent=incident_triage — that agent triages it",
        agent="incident_triage", channel="slack", integration="webhook", phase="run", live=True,
        ap_trigger="inbound (pinned)",
        note="generic inbound webhook, PINNED: you name the agent in the URL (deterministic)"),
    _ex("adv-webhook-routed", "Webhook · routed agent", "push",
        "POST any JSON to /api/events/hook/<name>?route=1 — the concierge picks the agent, like chat",
        agent="(auto-routed)", channel="slack", integration="webhook", phase="run", live=True,
        ap_trigger="inbound (routed)",
        note="generic inbound webhook, ROUTED: the concierge routes by capability (a PR payload → "
             "pr_reviewer, a dispute → incident_triage). No agent catalog knowledge needed"),
]


def _feasibility(integration: str, ap_trigger: str) -> tuple[str, str]:
    """CAN WE TEST THIS TODAY? Returns (tier, needs) for a trigger-tied example.

    Arming a PUSH watcher via the concierge has TWO gates, both must hold:
      G1  an AGENT declares that integration — only mailbot[gmail], resume_judge[box],
          support_digest[slack], pr_reviewer[github] do (seed.py).
      G2  flows.SOURCE_TRIGGER maps the specific trigger — only box/new_file, github/PR,
          github/issue, gmail/new_email today; anything else builds a non-existent trigger.
    Plus a FIRE gate: github (webhook) triggers can be synth-fired via /run; gmail/box (polling)
    need a real event; slack/discord run on the DIRECT backend (no AP connection at all).

    Tiers:
      now      — arms + verifies today (only the webhook endpoint clears every gate out of the box).
      select   — the trigger and/or a declaring agent is missing, but the connection exists; NL→flow +
                 (agent | SOURCE_TRIGGER | payload-map) work closes it. github is closest (synth-fire ready).
      backend  — Slack/Discord (direct-event handling, no AP connection) or Box folders/comments
                 (the direct poller lists files only). A larger lift than trigger-selection.
    Grounded in flows.SOURCE_TRIGGER, seed.py integrations, delivery._DEFAULT_BACKEND, box_direct (2026-07-11)."""
    i, t = integration, ap_trigger
    if not t:
        return ("", "")
    if i == "webhook":
        return ("now", "live today — pinned + routed both proven end-to-end (the webhook endpoint "
                       "bypasses the concierge, so neither gate applies)")
    if i == "github":
        if t == "trigger_issues":
            return ("select", "CLOSEST: trigger_issues IS in SOURCE_TRIGGER — the only gap is an AGENT "
                             "that handles issues (pr_reviewer declares github but the router treats it "
                             "as PR-only). Add issue handling to pr_reviewer (or a new issue agent), then "
                             "it arms + synth-fires via /run like PR")
        return ("select", "add the trigger to SOURCE_TRIGGER + a PUSH_PAYLOAD field-map AND an agent "
                          "that declares github for it; then arms + synth-fires via /run (github OAuth connected)")
    if i == "box":
        if t == "new_file":
            return ("select", "the new_file poll is LIVE (resume_judge uses it); routing it to a "
                             "DIFFERENT agent needs that agent to declare box — or drive it directly via "
                             "POST /api/events/box/poll?agent=…")
        return ("backend", "extend box_direct: the poller lists FILES only (should_process skips "
                           "subfolders) and has no Box comments API")
    if i == "gmail":
        return ("select", "non-default trigger — map it in SOURCE_TRIGGER (+ label/search config). "
                          "mailbot already declares gmail, so the agent is fine; firing still needs a "
                          "real email (polling trigger, can't be fired out of band)")
    if i == "telegram":
        return ("select", "a content-filter on the existing AP telegram message trigger")
    if i in ("slack", "discord"):
        transport = ("Slack Events API" if i == "slack" else "Discord Gateway")
        return ("backend", f"{i} runs on the DIRECT backend — no AP {i} connection. We already receive "
                           f"the {transport}; route THIS event type in our own handler")
    return ("select", "")


# Stamp every example with its testability AS OF TODAY (basic examples get ("","")).
for _e in EXAMPLES:
    _e["feasibility"], _e["needs"] = _feasibility(_e["integration"], _e.get("ap_trigger", ""))


def as_list() -> list[dict]:
    return list(EXAMPLES)
