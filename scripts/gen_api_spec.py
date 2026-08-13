#!/usr/bin/env python3
"""Generate `events_docs/api/api_spec.html` — the golden, try-it-yourself API spec.

    python scripts/gen_api_spec.py            # rewrite events_docs/api/api_spec.html
    python scripts/gen_api_spec.py --check    # exit 1 if the file is stale (used by the test)

**Why a generator.** A hand-written spec rots the moment someone adds a route, and nobody notices
until an integrator files a bug. Here the endpoint list is checked against `events/app.py` at build
time: add a route without describing it and `--check` fails, which the offline test suite runs. The
prose below is the part a machine can't derive — who calls each endpoint, in what context, and what
the response means. That is the whole value of the document, so it lives in code review like
everything else.

Each entry carries real example payloads (copied from the harnesses that actually send them) and the
real responses, including the interesting failures — a spec that only documents 200s is a spec that
lies about the system you'll actually integrate with.
"""

from __future__ import annotations

import argparse
import html
import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
APP = REPO / "src" / "cuga" / "backend" / "events" / "app.py"
OUT = REPO / "events_docs" / "api" / "api_spec.html"

# Who the caller is. Rendered as a coloured chip, and it's the thing most readers scan for.
ACTORS = {
    "ap": ("Activepieces", "Every armed AP flow — its HTTP step calls back into CUGA"),
    "studio": ("Studio UI", "The web console; it renders whatever these return, no client logic"),
    "channel": ("Chat channel", "Slack / Discord / Telegram, via the direct backend or an AP flow"),
    "external": ("External system", "Monitoring, CI, a form — anything that can POST JSON"),
    "browser": ("A person's browser", "OAuth consent redirects land here"),
    "cuga": ("CUGA itself", "An internal call from another handler in this same server"),
    "tests": ("Test harness", "tests/events/*.py"),
    "operator": ("You, from a shell", "curl / the Makefile targets"),
}

# Tiers. Not a taste call: the UI column was derived by grepping the shipped Studio bundle
# (src/cuga/frontend/dist) for each path. The bundle calls 19 of the 33; it never touches /invoke,
# the inbound receivers, the flows console, or channel arming.
TIERS = {
    "core": (
        "CORE",
        "#2b5fd9",
        "The reasoning seam. Two endpoints do the actual work; everything else feeds them or "
        "reads what they produced. If you are integrating CUGA into another system, these are "
        "the only two you need.",
    ),
    "edge": (
        "EDGE",
        "#b06f10",
        "Inbound receivers. An external system — Slack, a monitoring tool, Box — speaks its own "
        "dialect here; the handler normalises it into an envelope and calls <code>/invoke</code>. "
        "The Studio never calls these.",
    ),
    "ui": (
        "UI",
        "#1a7f4b",
        "The Studio's data contract. Read endpoints the console renders, plus the writes its forms "
        "perform. Safe to ignore unless you are building a UI.",
    ),
    "ops": ("OPS", "#7a3fa8", "Operator surface. Driven from a shell or the Makefile, not from the console."),
    "debug": (
        "DEBUG",
        "#c0392b",
        "Debugging aids with <b>real side effects</b>. Not part of the product surface; not "
        "called by the Studio. Disable them in a shared deployment.",
    ),
}

GROUPS = [
    (
        "core",
        "Core — the reasoning seam",
        "Two endpoints do all the thinking. Everything else is plumbing around them.",
    ),
    (
        "flows",
        "Flows — the standing subscriptions",
        "A flow is one armed subscription. CUGA stores "
        "the model; Activepieces owns the trigger and the sink.",
    ),
    ("runs", "Runs — the execution log", "What actually fired, what it answered, what broke."),
    (
        "studio",
        "Studio reads — the UI's data contract",
        "All GET, all scope-isolated, all return "
        "200 even when Activepieces is down (status becomes <code>unknown</code>, never a 500).",
    ),
    (
        "agents",
        "Agents — the sub-agent roster",
        "Read-only view of supervisor_agents.yaml (the "
        "canonical source of truth); registration via the API is retired (410).",
    ),
    (
        "inbound",
        "Inbound surfaces — events arriving from the outside",
        "These are the front doors. "
        "Each one normalises its payload into an envelope and calls <code>/invoke</code>.",
    ),
    (
        "connect",
        "Connect &amp; credentials",
        "Activepieces holds every token, encrypted. The agent never sees one.",
    ),
    ("identity", "Identity &amp; account-linking", "Binding a chat account to a CUGA profile."),
    ("admin", "Admin", "Admin role required — <code>403</code> otherwise."),
]

GW = "X-Gateway-Token: $GATEWAY_TOKEN"


def E(
    verb,
    path,
    group,
    summary,
    *,
    tier,
    callers,
    auth="none",
    path_params=(),
    query=(),
    body=(),
    responses=(),
    notes="",
    try_it=True,
):
    return dict(
        verb=verb,
        path=path,
        group=group,
        summary=summary,
        tier=tier,
        callers=list(callers),
        auth=auth,
        path_params=list(path_params),
        query=list(query),
        body=list(body),
        responses=list(responses),
        notes=notes,
        try_it=try_it,
    )


# The envelope every inbound surface builds. Repeated here because it IS the contract.
ENV_NOW = {
    "agent": "pricebot",
    "text": "what is the current price of bitcoin?",
    "deliver": False,
    "source": {"type": "time", "name": "cron", "thread_id": "web:local"},
    "event": {"kind": "runonce", "payload": {}},
}

ENDPOINTS = [
    # ── core ──────────────────────────────────────────────────────────────────
    E(
        "POST",
        "/invoke",
        "core",
        "Run one agent on one event. The single seam every trigger converges on.",
        tier="core",
        callers=["ap", "cuga", "tests", "operator"],
        auth="gateway",
        body=[
            (
                "Direct agent call (NOW) — no channel, no concierge",
                ENV_NOW,
                "This is what <code>make test-suite-now</code> sends. <code>source.type:\"time\"</code> + "
                "<code>event.kind:\"runonce\"</code> means \"just answer, don't arm anything\". The "
                "response's <code>meta.mcp</code> proves the agent reached a real tool instead of "
                "answering from the model's memory.",
            ),
            (
                "A chat message routed through the concierge",
                {
                    "agent": "concierge",
                    "text": "every day at 9am send me the price of bitcoin",
                    "deliver": False,
                    "source": {
                        "type": "channel",
                        "name": "slack",
                        "thread_id": "gw:slack:C0BEYJ9NATB#1783630758.450769",
                        "user": "U123",
                    },
                    "event": {"kind": "message", "payload": {"slack_user": "U123"}},
                },
                "<code>agent:\"concierge\"</code> is the router, not a worker. The "
                "<code>gw:&lt;channel&gt;:&lt;native&gt;</code> thread_id is how the reply finds its way "
                "home; the <code>#&lt;ts&gt;</code> suffix scopes conversation memory to one Slack thread "
                "without changing the delivery target.",
            ),
            (
                "A PUSH flow firing (Gmail → agent → Slack)",
                {
                    "agent": "mailbot",
                    "text": "summarize this email",
                    "deliver": True,
                    "source": {
                        "type": "integration",
                        "name": "gmail",
                        "thread_id": "default/default/local::gw:slack:C0BEYJ9NATB",
                    },
                    "event": {
                        "kind": "new_email",
                        "payload": {
                            "subject": "Q3 numbers",
                            "from": "boss@corp.com",
                            "body": "See attached.",
                        },
                    },
                },
                "The source is the <b>integration that fired</b>, never the sink. The sink is parsed out "
                "of the scope-prefixed thread_id. <code>deliver:true</code> means CUGA sends the answer "
                "itself (a direct channel like Slack); AP-backed sinks use an AP send step and pass "
                "<code>deliver:false</code>.",
            ),
            (
                "A CRON flow firing on schedule",
                {
                    "agent": "papers",
                    "text": "new arxiv papers on mixture of experts",
                    "deliver": False,
                    "scope": "default/default/local",
                    "source": {"type": "time", "name": "schedule", "thread_id": "gw:telegram:8840265085"},
                    "event": {"kind": "tick", "payload": {}},
                },
                "What Activepieces' schedule trigger POSTs every morning. <code>deliver:false</code> — the "
                "AP flow has its own send step after this one. <code>scope</code> was baked in when the "
                "flow was armed, which is how a background flow knows whose credentials to use.",
            ),
            (
                "A POLL flow reporting a change",
                {
                    "agent": "feed_watcher",
                    "text": "tell me only about new items",
                    "deliver": True,
                    "source": {
                        "type": "time",
                        "name": "schedule",
                        "thread_id": "default/default/local::gw:discord:1522408587958423675",
                    },
                    "event": {"kind": "tick", "payload": {"seen_watermark": "2026-07-09T08:00:00Z"}},
                },
                "Poll and cron differ only in cadence and in what the agent does with the payload — the "
                "envelope shape is identical. Discord is a direct sink, so <code>deliver:true</code>.",
            ),
            (
                "A Box file landing (direct poller)",
                {
                    "agent": "resume_judge",
                    "deliver": True,
                    "scope": "default/default/local",
                    "text": "A file 'resume.pdf' landed in Box. Judge fit vs the JD. Start your reply with "
                    "MATCH or SKIP.",
                    "source": {"type": "channel", "name": "slack", "thread_id": "gw:slack:C0BEYJ9NATB"},
                    "event": {"kind": "new_file", "payload": {"file_id": "9", "name": "resume.pdf"}},
                },
                "Built by <code>_box_dispatch</code>. Note the source is the <b>sink channel</b> here, not "
                "Box — because the direct poller sends the answer itself. When there's no direct sink it "
                "instead sends <code>source: {type:\"integration\", name:\"box\", "
                "thread_id:\"box:&lt;file_id&gt;\"}</code>.",
            ),
            (
                "A generic webhook triage",
                {
                    "agent": "incident_triage",
                    "deliver": True,
                    "text": "An external system POSTed to webhook 'monitoring'. Triage this payload:\n\n"
                    "{\n  \"alert\": \"HighCPU\",\n  \"service\": \"checkout-api\"\n}",
                    "source": {"type": "channel", "name": "slack", "thread_id": "gw:slack:C0BEYJ9NATB"},
                    "event": {"kind": "message", "payload": {"alert": "HighCPU", "service": "checkout-api"}},
                },
                "The hook serialises the caller's JSON into the prompt <i>and</i> passes it structurally in "
                "<code>event.payload</code>. The agent gets both.",
            ),
            (
                "Account-linking handshake",
                {
                    "agent": "concierge",
                    "text": "/start 4f3c1a…",
                    "source": {"type": "channel", "name": "telegram", "thread_id": "gw:telegram:8840265085"},
                    "event": {"kind": "message", "payload": {}},
                },
                "A message beginning <code>/start &lt;token&gt;</code> or <code>/link &lt;token&gt;</code> "
                "binds the sender's native id to the profile that issued the token, and short-circuits "
                "before any agent runs — the response is <code>{ok, linked, answer}</code>, no agent runs.",
            ),
            (
                "Minimal — the smallest thing that works",
                {
                    "agent": "pricebot",
                    "text": "bitcoin price?",
                    "source": {"type": "time", "name": "cron"},
                    "event": {"kind": "runonce"},
                },
                "<code>deliver</code>, <code>scope</code>, <code>thread_id</code> and "
                "<code>event.payload</code> are all optional. This is the call to reach for when you are "
                "testing whether an agent works at all.",
            ),
        ],
        responses=[
            (
                200,
                {
                    "ok": True,
                    "agent": "pricebot",
                    "answer": "Bitcoin is currently **$63,240 USD**.",
                    "trace_id": "6f2a…",
                    "meta": {
                        "agent": "pricebot",
                        "backend": "cuga",
                        "mcp": ["cuga-finance"],
                        "tools": ["get_crypto_price"],
                        "ms": 6412,
                    },
                },
                "The answer, plus who produced it. <code>meta.mcp</code> is the honest signal.",
            ),
            (
                400,
                {"ok": False, "error": "invalid envelope: source.type 'carrier-pigeon' not in …"},
                "The envelope failed validation. Checked before the agent is even looked up.",
            ),
            (
                401,
                {"ok": False, "error": "bad or missing X-Gateway-Token"},
                "Checked first, before the body is parsed.",
            ),
            (404, {"ok": False, "error": "unknown agent 'ghost'"}, "No such agent in this scope."),
            (
                501,
                {"ok": False, "error": "concierge not configured"},
                "<code>agent:\"concierge\"</code> with no concierge wired up.",
            ),
        ],
        notes="<b>Envelope rules</b> (<code>events/envelope.py</code>): <code>source.type</code> ∈ "
        "<code>channel · integration · time</code>; <code>event.kind</code> ∈ the known kinds; a "
        "<code>channel</code> message envelope may not have empty text. <br><b>Auth:</b> "
        "<code>X-Gateway-Token</code> is enforced only when <code>GATEWAY_TOKEN</code> is set. "
        "Unset, this endpoint runs arbitrary agents on a caller-supplied scope for anyone who can "
        "reach it — fine on localhost, dangerous behind a tunnel.",
    ),
    E(
        "POST",
        "/api/concierge",
        "core",
        "Natural language → a flow. The web chat's front door.",
        tier="core",
        callers=["studio", "tests", "operator"],
        query=[
            ("dry_run", "<code>1</code> = plan only, no side effects, no LLM", "1"),
            (
                "flow",
                "<code>1</code> = also return the flow(s) this utterance armed, as a digest; "
                "<code>full</code> = the raw Activepieces flow JSON. Costs one AP call per new "
                "subscription, so it is off by default.",
                "",
            ),
        ],
        body=[
            (
                "Arm a scheduled flow",
                {"text": "every day at 9am send me the price of bitcoin", "thread_id": "web:local"},
                "<code>thread_id</code> keys the conversation memory. Reuse it and the concierge "
                "remembers the previous turns — including, unhelpfully, that it already armed something.",
            ),
            (
                "Plan without arming (<code>?dry_run=1</code>)",
                {"text": "watch my Box folder and judge every resume", "agent": "worker"},
                "Runs the deterministic planner, not the LLM. Safe to call in a loop; useful for asserting "
                "that an utterance classifies as the mode you expect.",
            ),
            (
                "A one-shot question",
                {"text": "what is bitcoin worth?", "thread_id": "web:local"},
                "No cadence in the sentence ⇒ the concierge picks a worker agent, answers, arms nothing.",
            ),
            (
                "Arm a POLL — watch something and only speak on change",
                {
                    "text": "check the New York weather every hour and ping me only if rain is forecast",
                    "thread_id": "web:local",
                },
                "\"every N minutes … only if / only new\" is what separates POLL from CRON. <b>Known gap:</b> "
                "\"check … every 15 minutes and tell me only about new items\" still classifies as "
                "<code>CRON</code> — deterministic, reproducible via <code>?dry_run=1</code>.",
            ),
            (
                "Arm a PUSH watcher on an integration",
                {
                    "text": "when an email from my boss arrives, summarize it and message me",
                    "thread_id": "gw:slack:C0BEYJ9NATB",
                },
                "The sink is derived from the thread_id's <code>gw:&lt;channel&gt;:&lt;native&gt;</code> "
                "origin — say this in Slack and the summary comes back to Slack. <b>\"my boss\" is not "
                "resolved anywhere</b>: there is no contacts store, and the Gmail trigger takes no sender "
                "filter, so the agent asks for an address or summarises whatever arrives.",
            ),
            (
                "Arm a PUSH trigger + a post-agent ACTION",
                {
                    "text": "when an email arrives, reply to the sender with a short acknowledgement",
                    "thread_id": "web:local",
                },
                "The <b>action half</b>: after the agent answers, a connector ACTION runs as a step in the "
                "same Activepieces flow (here <code>gmail/reply_to_email</code>). Gmail is the live pilot "
                "(reply / draft / send, plus multi-action and N-way branching). Three gates guard an arm — "
                "deterministic build, an AP <b>validity gate</b> (an invalid step is refused, never a false "
                "\"ARMED\"), and an LLM intent-verifier. An unsupported action declines, never a silent drop.",
            ),
            (
                "Direct trigger + action (the executor, Option A)",
                {
                    "text": "when a message is posted in #alerts, email me a summary at me@example.com",
                    "thread_id": "gw:slack:C0BEYJ9NATB",
                },
                "A <b>direct</b> trigger (slack/discord/telegram) owns no AP flow, so its Gmail action runs "
                "via a reusable <b>executor flow</b> (<code>catch_webhook ▸ gmail/send_email</code>) CUGA "
                "fires after the agent answers — AP still holds the credentials. No new endpoint: the "
                "executor is created during this arm. <b>Status:</b> arm + validity gate are live-verified; "
                "the executor <i>run</i> currently errors at the Activepieces platform level (send not yet "
                "executed on the test instance). If the executor can't be built, the arm declines.",
            ),
            (
                "A slash command",
                {"text": "/cron 0 9 * * * papers: new arxiv papers on MoE", "thread_id": "web:local"},
                "<code>/watch · /schedule · /cron · /poll · /push</code> skip the LLM's mode classification. "
                "Handled inside <code>concierge.run</code>, so they work identically from every surface.",
            ),
            (
                "Override the delivery channel",
                {
                    "text": "every day at 9am send the bitcoin price to my telegram",
                    "thread_id": "gw:discord:1522408587958423675",
                },
                "An explicit <code>deliver_to</code> in the utterance <b>overrides the origin channel</b> "
                "(<code>concierge.py:266</code>). Ask from Discord, name Telegram, and the flow delivers to "
                "Telegram. Surprising, and a real source of \"why didn't my flow reply here\".",
            ),
            (
                "Tear down what you armed",
                {"text": "stop sending me the bitcoin price", "thread_id": "web:local"},
                "<b>Known gap:</b> the concierge trusts its thread memory over the store, so after a "
                "subscription is deleted out from under it, it may still answer \"that's already set up\". "
                "A fresh <code>thread_id</code> avoids this.",
            ),
        ],
        responses=[
            (
                200,
                {
                    "ok": True,
                    "reply": "Cron flow set up — papers, daily at 09:00, delivering here.",
                    "scope": "default/default/local",
                    "trace_id": "1b9e…",
                },
                "The live path. <code>reply</code> is prose meant for a human.",
            ),
            (
                200,
                {
                    "ok": True,
                    "dry_run": True,
                    "decision": {"mode": "CRON", "cron": "0 9 * * *"},
                    "flow": {"trigger": "…", "steps": ["…"]},
                    "trace_id": "1b9e…",
                },
                "The <code>?dry_run=1</code> path: the plan, and nothing armed.",
            ),
            (
                200,
                {
                    "ok": True,
                    "reply": "Armed a daily 9am cron for pricebot.",
                    "scope": "default/default/local",
                    "trace_id": "1b9e…",
                    "flows": [
                        {
                            "subscription_id": "pricebot-88a42a",
                            "mode": "CRON",
                            "agent": "pricebot",
                            "deliver_to": ["telegram"],
                            "flow_name": "ea:cron-pricebot-0_9_*_*_*-7c53",
                            "ap_flow_id": "DLupsrNjNpIkboZXywNjX",
                            "dedup_key": "pricebot|time|0 9 * * *|telegram|default",
                            "exists_in_ap": True,
                            "flow": {
                                "id": "DLupsrNjNpIkboZXywNjX",
                                "status": "ENABLED",
                                "trigger": {
                                    "piece": "@activepieces/piece-schedule",
                                    "name": "cron_expression",
                                    "input": {"cronExpression": "0 9 * * *", "timezone": "UTC"},
                                },
                                "steps": [
                                    {
                                        "name": "step_1",
                                        "display": "Invoke CUGA",
                                        "piece": "@activepieces/piece-http",
                                        "action": "send_request",
                                        "text": None,
                                    },
                                    {
                                        "name": "step_2",
                                        "display": "telegram · send",
                                        "piece": "@activepieces/piece-telegram-bot",
                                        "action": "send_text_message",
                                        "text": "{{step_1.body.answer}}",
                                    },
                                ],
                            },
                        }
                    ],
                },
                "<code>?flow=1</code>. This is how you check the pieces are right. Read the chain: the "
                "schedule fires, <code>step_1</code> POSTs to <code>/invoke</code>, and "
                "<code>step_2</code> sends <code>{{step_1.body.answer}}</code> — the <b>HTTP response of "
                "step_1</b>. There is no callback; Activepieces simply blocks on CUGA's reply.",
            ),
            (
                200,
                {
                    "ok": True,
                    "reply": "Push flow set up.",
                    "flows": [
                        {"subscription_id": "mailbot-77c1", "ap_flow_id": "gone-abc", "exists_in_ap": False}
                    ],
                },
                "<b>Dangling.</b> The subscription names a flow Activepieces does not have, so the watcher "
                "can never fire. <code>exists_in_ap</code> is the only honest signal — a non-empty "
                "<code>ap_flow_id</code> proves nothing.",
            ),
            (
                200,
                {"ok": True, "reply": "Bitcoin is about $63,964 USD.", "flows": []},
                "<code>?flow=1</code> on an utterance that armed nothing. An utterance that <i>reused</i> "
                "an existing flow also returns <code>[]</code> — nothing was created. Use "
                "<code>GET /api/events/subscriptions</code> to see what already exists.",
            ),
            (
                500,
                {"ok": False, "error": "AP unreachable", "trace_id": "1b9e…"},
                "The concierge raised. The trace_id is the thread to pull.",
            ),
            (
                501,
                {
                    "ok": False,
                    "reason": "concierge not configured; use ?dry_run=1 for the plan",
                    "plan": {"decision": {"mode": "NOW"}},
                },
                "No concierge instance — it still hands back the plan.",
            ),
        ],
        notes="Slash commands (<code>/watch · /schedule · /cron · /poll · /push</code>) are handled "
        "inside <code>concierge.run</code>, so they work identically from web chat and from every "
        "channel. There is no interception here.<br><br><b>To inspect what an utterance built</b>, "
        "use <code>?flow=1</code> (digest) or <code>?flow=full</code> (raw AP JSON). For a flow "
        "armed earlier, <code>GET /api/events/subscriptions/&lt;id&gt;/flow</code>, or the live "
        "<code>GET /api/events/dashboard</code>.",
    ),
    E(
        "GET",
        "/api/events/dry-run",
        "core",
        "Preview what an utterance WOULD do — with ZERO side effects (nothing armed, nothing persisted). "
        "Browser-friendly: paste a URL. The same routing the concierge uses (classify → resolve → "
        "native-vs-AP → AP reachability), returned as a verdict.",
        tier="ui",
        callers=["studio", "tests", "operator"],
        query=[
            ("text", "the utterance — the same one the web UX sends", "every 5 minutes give me a tip"),
            ("utterance", "alias for <code>text</code>", ""),
        ],
        responses=[
            (
                200,
                {
                    "ok": True,
                    "utterance": "every 5 minutes give me a tip",
                    "mode": "CRON",
                    "backend": "native",
                    "routing": "native scheduler — runs in-process, no Activepieces",
                    "cadence": {"interval_seconds": 300},
                    "cadence_human": "every 5 min",
                    "bounded_run_seconds": None,
                    "next_fire_preview": "2026-07-30T01:24:58Z",
                    "would": "arm",
                    "side_effects": "none (dry run)",
                },
                "A CRON/POLL routes to the native scheduler — no AP. <code>next_fire_preview</code> is the "
                "real next fire time.",
            ),
            (
                200,
                {
                    "ok": True,
                    "mode": "PUSH",
                    "backend": "ap",
                    "source": "github",
                    "event": "new_pr",
                    "confidence": "high",
                    "ap_reachable": False,
                    "would": "decline — Activepieces not reachable",
                },
                "A push on an integration needs AP; it declines cleanly (and fast) when AP is down.",
            ),
            (
                200,
                {
                    "ok": True,
                    "mode": "NOW",
                    "backend": "—",
                    "would": "answer",
                    "routing": "answered by the agent now (no flow armed)",
                },
                "A plain question — the agent answers, nothing is armed.",
            ),
            (
                400,
                {"ok": False, "error": "provide ?text=<utterance> (or POST {\"text\": ...})"},
                "No utterance given.",
            ),
        ],
        notes="Complements <code>POST /api/concierge?dry_run=1</code> (which returns the would-be flow "
        "JSON) with a one-line, browser-friendly verdict. Purely read-only.",
    ),
    E(
        "POST",
        "/api/events/dry-run",
        "core",
        "Same as <code>GET /api/events/dry-run</code>, for a JSON body — the exact payload the web UX "
        "posts. ZERO side effects.",
        tier="ui",
        callers=["studio", "tests"],
        body=[
            (
                "Preview an utterance",
                {"text": "watch bitcoin every 2 minutes and ping me on a big move"},
                "Returns the same verdict object as the GET form — mode, backend, cadence, would.",
            )
        ],
        responses=[
            (
                200,
                {
                    "ok": True,
                    "mode": "POLL",
                    "backend": "native",
                    "cadence_human": "every 2 min",
                    "would": "arm",
                    "side_effects": "none (dry run)",
                },
                "POLL → native scheduler.",
            ),
            (400, {"ok": False, "error": "provide ?text=<utterance> (or POST {\"text\": ...})"}, ""),
        ],
    ),
    # ── flows ─────────────────────────────────────────────────────────────────
    E(
        "GET",
        "/api/events/subscriptions",
        "flows",
        "This principal's standing flows.",
        tier="ui",
        callers=["studio", "tests"],
        responses=[
            (
                200,
                {
                    "scope": "default/default/local",
                    "subscriptions": [
                        {
                            "id": "s1",
                            "mode": "CRON",
                            "target_agent": "papers",
                            "deliver_to": ["telegram"],
                            "ap_flow_id": "lqfzMLeItA",
                            "flow_name": "cron-papers",
                            "status": "active",
                            "prompt": "new arxiv papers on mixture of experts",
                            "dedup_key": "papers|time|1d|telegram|default",
                        }
                    ],
                },
                "Scope-isolated: you only ever see your own.",
            )
        ],
        notes="<code>dedup_key</code> = <code>agent|source|cadence|sink|owner</code>. "
        "<code>find_or_create_flow</code> reuses a subscription whose key matches — "
        "<b>without checking the AP flow still exists</b> (<code>concierge.py:285-289</code>). "
        "That is why a subscription can claim an <code>ap_flow_id</code> that is long gone.",
    ),
    E(
        "POST",
        "/api/events/subscriptions/{sub_id}/pause",
        "flows",
        "Pause a flow — disables it in Activepieces, not just in CUGA's row.",
        tier="ui",
        callers=["studio", "operator"],
        path_params=[("sub_id", "subscription id", "s1")],
        responses=[
            (200, {"ok": True, "id": "s1", "status": "paused"}, ""),
            (
                404,
                {"ok": False, "error": "subscription not found"},
                "Also returned for <b>another principal's</b> id. Deliberately not 403 — a 403 "
                "would confirm the id exists, leaking it across tenants.",
            ),
        ],
    ),
    E(
        "POST",
        "/api/events/subscriptions/{sub_id}/resume",
        "flows",
        "Re-enable a paused flow in Activepieces.",
        tier="ui",
        callers=["studio", "operator"],
        path_params=[("sub_id", "subscription id", "s1")],
        responses=[
            (200, {"ok": True, "id": "s1", "status": "active"}, ""),
            (404, {"ok": False, "error": "subscription not found"}, ""),
        ],
    ),
    E(
        "DELETE",
        "/api/events/subscriptions/{sub_id}",
        "flows",
        "Delete a flow — removes it from Activepieces too.",
        tier="ui",
        callers=["studio", "operator", "tests"],
        path_params=[("sub_id", "subscription id", "s1")],
        responses=[
            (200, {"ok": True, "id": "s1", "deleted": True}, ""),
            (404, {"ok": False, "error": "subscription not found"}, ""),
        ],
        notes="The live harnesses call this to clean up after themselves — they delete only the "
        "subscriptions <b>they</b> created, tracked by diffing the list before and after.",
    ),
    E(
        "POST",
        "/api/events/subscriptions/{sub_id}/run",
        "flows",
        "DEBUG — fire an armed flow now, out of band of its own trigger.",
        tier="debug",
        callers=["operator", "tests"],
        auth="gateway",
        path_params=[("sub_id", "subscription id", "s1")],
        query=[
            (
                "wait",
                "<code>0</code> = return as soon as AP accepts the trigger; default polls for "
                "the run this call produced",
                "1",
            ),
            ("timeout", "seconds to wait for the run to finish (max 600)", "120"),
        ],
        body=[
            (
                "No body — a cron/poll flow",
                {},
                "A <b>schedule</b> flow's <code>/invoke</code> body (agent, prompt, thread_id, scope) is "
                "frozen at arm time, so nothing you POST changes what it does. Fire it as armed.",
            ),
            (
                "A synthetic event — a PUSH flow",
                {
                    "title": "Fix race in worker pool shutdown",
                    "html_url": "https://github.com/owner/repo/pull/9999",
                    "body": "Workers could exit before draining the queue, dropping in-flight jobs.",
                    "base": {"repo": {"full_name": "owner/repo"}},
                    "user": {"login": "octocat"},
                    "additions": 42,
                    "deletions": 7,
                    "changed_files": 3,
                },
                "A <b>push</b> flow's invoke body contains <code>{{trigger.title}}</code>-style "
                "templates, so the body you POST <i>becomes</i> the trigger output and the agent reasons "
                "over it. This is the way to exercise a Gmail/GitHub/Box watcher <b>without</b> opening a "
                "real pull request or sending a real email. Shape it like the piece's real trigger "
                "output — for github that is the <code>pull_request</code> object itself.",
            ),
            (
                "A marker body",
                {"note": "fired by hand"},
                "On a schedule flow this is inert; it only shows up as the trigger payload in the run "
                "log, which is a handy way to tell your run apart from a scheduled one.",
            ),
        ],
        responses=[
            (
                200,
                {
                    "ok": True,
                    "debug": True,
                    "subscription_id": "s1",
                    "ap_flow_id": "stttGLqX2TvgstQDLsboq",
                    "triggered": True,
                    "warning": "this fired the real flow: it delivered to its real sink",
                    "run": {
                        "id": "KRxqCBUsr2",
                        "status": "SUCCEEDED",
                        "started_at": "2026-07-09T09:00:00Z",
                        "finished_at": "2026-07-09T09:00:06Z",
                    },
                    "answer": "Bitcoin is currently priced at **$63,924 USD**.",
                    "trigger_payload": {},
                    "error": None,
                },
                "The whole chain ran: schedule → <code>/invoke</code> → sink. The answer is lifted out of "
                "the run's step tree.",
            ),
            (
                200,
                {
                    "ok": True,
                    "triggered": True,
                    "timed_out": True,
                    "run": {"id": "KRxqCBUsr2", "status": "RUNNING"},
                    "note": "AP accepted the trigger but no run finished within 120s. Poll "
                    "GET /api/events/runs, or retry with a larger ?timeout=.",
                },
                "Triggered but unfinished. Deliberately <b>not</b> an error: the flow is running.",
            ),
            (
                200,
                {"ok": True, "debug": True, "triggered": True, "ap_flow_id": "sttt…"},
                "<code>?wait=0</code>. Fire and forget — no answer, because nothing waited for it.",
            ),
            (401, {"ok": False, "error": "bad or missing X-Gateway-Token"}, ""),
            (
                403,
                {"ok": False, "error": "debug run endpoint disabled (EVENTS_DEBUG_RUN=0)"},
                "Turn it off in any deployment where an unattended trigger would be a problem.",
            ),
            (404, {"ok": False, "error": "subscription not found"}, "Also for another principal's id."),
            (
                409,
                {
                    "ok": False,
                    "error": "DANGLING: subscription points at AP flow X, which does not "
                    "exist in Activepieces. Nothing to run.",
                    "ap_flow_id": "X",
                },
                "Checked <i>before</i> triggering — otherwise the call would happily 'succeed' and run "
                "nothing at all.",
            ),
            (409, {"ok": False, "error": "subscription has no AP flow to run"}, ""),
            (501, {"ok": False, "error": "AP not configured"}, ""),
            (502, {"ok": False, "error": "Activepieces refused the trigger: HTTP 404 …"}, ""),
        ],
        notes="<b>Not every flow can be fired this way.</b> Verified 2026-07-10: the schedule piece and "
        "github's <code>trigger_pull_request</code> (a WEBHOOK trigger) both run, but gmail's "
        "<code>gmail_new_email_received</code> is an app-POLLING trigger — Activepieces accepts the "
        "trigger POST with <code>200</code> and produces no run at all. A <code>timed_out</code> "
        "response on a gmail/box watcher therefore means \"this trigger type cannot be fired out of "
        "band\", not \"the watcher is broken\". Check the piece's trigger <code>type</code> at "
        "<code>GET &lt;AP&gt;/api/v1/pieces/&lt;piece&gt;</code>.<br><br>"
        "<b>This is not a dry run.</b> The flow delivers wherever it was armed to deliver: it will "
        "post to your Slack channel and message your Telegram, and Activepieces' own log will not "
        "mark the run as a test. <br><br>Mechanically it POSTs to Activepieces' "
        "<code>/api/v1/webhooks/&lt;flowId&gt;</code>, which fires <i>any</i> flow whatever its "
        "trigger. The run executes with the flow's own AP connections, so <b>no credential is "
        "passed here and none is needed</b> — AP resolves them internally, exactly as on a real "
        "tick. <br><br><b>Security:</b> AP's webhook route takes no authentication of its own. "
        "Anyone who can reach Activepieces and knows a flow id can fire it. Keep AP off the public "
        "tunnel; the gate that matters is this endpoint's <code>X-Gateway-Token</code>.",
    ),
    E(
        "POST",
        "/api/events/synth-fire",
        "flows",
        "Synthetically fire ANY AP trigger with a piece-exact payload — no flow, no connection, no real event.",
        tier="debug",
        callers=["operator", "tests"],
        auth="gateway",
        body=[
            (
                "Fire a piece's default trigger with its built-in synth sample",
                {"source": "google_calendar"},
                "Uses the registry's <code>synth</code> sample for the source's default trigger and the "
                "supervisor <code>cuga</code> agent. The agent runs on the exact payload shape a REAL "
                "event delivers — the way to test calendar/pinterest/youtube/rss/gmail/box triggers "
                "<b>without</b> a live connection or an armed flow.",
            ),
            (
                "Deliver the result to a channel, like the real watcher",
                {
                    "source": "rss",
                    "event": "new_item",
                    "deliver_to": "slack",
                    "deliver_target": "C0123",
                    "prompt": "Summarize this feed item for the team.",
                },
                "With <code>deliver_to</code>+<code>deliver_target</code> the answer is delivered to that "
                "channel exactly as the armed watcher would.",
            ),
            (
                "Override the payload",
                {
                    "source": "youtube",
                    "payload": {
                        "title": "New video",
                        "link": "https://youtu.be/x",
                        "author": "Fireship",
                        "pubDate": "2026-07-21T09:00:00Z",
                    },
                },
                "Pass your own <code>payload</code> to exercise a specific case; it replaces the registry "
                "synth sample.",
            ),
        ],
        responses=[
            (
                200,
                {
                    "ok": True,
                    "source": "google_calendar",
                    "event": "new_event",
                    "delivered_to": None,
                    "answer": "**Event Summary** — Team Sync, Jul 22 10:00…",
                    "trace_id": "9e0c…",
                },
                "The agent ran on the synthetic event and returned its answer.",
            ),
            (
                400,
                {
                    "ok": False,
                    "error": "slack/new_reaction is a DIRECT trigger — fire it through its real transport",
                },
                "Direct triggers (slack/discord/telegram) arrive at CUGA over their own transport; this "
                "endpoint is for AP-backed triggers only.",
            ),
            (401, {"ok": False, "error": "bad or missing X-Gateway-Token"}, ""),
            (404, {"ok": False, "error": "no trigger for source='foo' event=None"}, ""),
            (
                422,
                {"ok": False, "error": "youtube/new_video has no synth sample; pass an explicit 'payload'"},
                "Only the default triggers ship a synth sample; pass your own <code>payload</code> for the rest.",
            ),
        ],
        notes="Unlike <code>/run</code>, this needs <b>no armed flow and no connection</b> — it injects the "
        "synthetic event straight at the <code>/invoke</code> seam, so it works for POLLING triggers "
        "that <code>/run</code> cannot fire. Same debug switch: <code>EVENTS_DEBUG_RUN=0</code> disables it.",
    ),
    E(
        "GET",
        "/api/events/subscriptions/{sub_id}/flow",
        "flows",
        "The CUGA model plus the live Activepieces flow JSON.",
        tier="ui",
        callers=["studio", "tests"],
        path_params=[("sub_id", "subscription id", "s1")],
        responses=[
            (
                200,
                {
                    "ok": True,
                    "subscription": {"id": "s1", "ap_flow_id": "lqfzMLeItA"},
                    "ap_flow": {
                        "id": "lqfzMLeItA",
                        "status": "ENABLED",
                        "version": {"trigger": {"name": "trigger", "nextAction": "…"}},
                    },
                },
                "Healthy: the flow exists in AP.",
            ),
            (
                200,
                {"ok": True, "subscription": {"id": "s1", "ap_flow_id": "lqfzMLeItA"}, "ap_flow": None},
                "<b>Dangling.</b> The subscription points at a flow that no longer exists — the watcher "
                "can never fire, yet the concierge keeps reporting it armed.",
            ),
            (404, {"ok": False, "error": "subscription not found"}, ""),
        ],
        notes="<b><code>ok</code> is <code>true</code> in both cases above.</b> Testing the truthiness "
        "of this response proves nothing; <code>ap_flow != null</code> is the only real signal. "
        "This is exactly the bug <code>flow_alive()</code> in the live harnesses exists to catch.",
    ),
    E(
        "GET",
        "/api/events/dashboard",
        "flows",
        "The LIVE Events Dashboard — a self-contained control-plane page: every watcher, run &amp; "
        "channel, pretty and readable, with pause/resume/delete/run + an inline dry-run.",
        tier="ops",
        callers=["operator"],
        try_it=False,
        responses=[
            (
                200,
                "&lt;html&gt;…&lt;/html&gt;",
                "Reads only the /api/events/* APIs; auto-refreshes. Open it at "
                "<code>/api/events/dashboard</code>.",
            )
        ],
    ),
    # ── runs ──────────────────────────────────────────────────────────────────
    E(
        "GET",
        "/api/events/runs",
        "runs",
        "The unified execution log — AP flow-runs + NOW answers + <b>native cron/poll fires</b>, each "
        "tagged with its trigger type and rich metadata. No params ⇒ ALL runs.",
        tier="ui",
        callers=["studio", "operator"],
        query=[
            ("mode", "CRON | POLL | PUSH | NOW", "CRON"),
            ("backend", "native | ap | direct", "native"),
            ("agent", "only this agent's runs", "pricebot"),
            ("status", "SUCCEEDED | FAILED", "SUCCEEDED"),
            ("kind", "flow | now", "flow"),
            ("source", "the source connector (integration)", "github"),
            ("subscription_id", "a single watcher's run history", "cuga-50993d"),
            ("limit", "max rows (default 150, cap 500)", "50"),
        ],
        responses=[
            (
                200,
                {
                    "scope": "default/default/local",
                    "count": 1,
                    "filters": {"mode": "CRON", "backend": "native"},
                    "runs": [
                        {
                            "id": "r1",
                            "status": "SUCCEEDED",
                            "started_at": "2026-07-09T09:00:00Z",
                            "finished_at": "2026-07-09T09:00:06Z",
                            "agent": "pricebot",
                            "mode": "CRON",
                            "backend": "native",
                            "integration": "—",
                            "channel": "telegram",
                            "utterance": "give me the bitcoin price",
                            "answer": "Bitcoin is about $64,010.",
                            "tools": ["get_crypto_price"],
                            "mcp": [],
                            "ms": 5200,
                            "event_kind": "tick",
                            "subscription_id": "cuga-50993d",
                            "flow_id": "",
                            "kind": "flow",
                        }
                    ],
                },
                "Each row carries <code>backend</code> (native/ap), the agent's <code>answer</code>, the "
                "<code>tools</code>/<code>mcp</code> it invoked, and <code>ms</code>. A native fire has no "
                "AP flow — its history lives here, not in Activepieces.",
            )
        ],
        notes="This is the log of runs that HAVE HAPPENED. For what is SCHEDULED / upcoming (each "
        "watcher's <code>next_fire</code> / <code>last_fire</code> / <code>fire_count</code>), "
        "use <code>GET /api/events/subscriptions</code> — the schedule lives on the watcher. "
        "Runs whose flow isn't yours are skipped, not 403'd.",
    ),
    E(
        "GET",
        "/api/events/inbox",
        "runs",
        "The <b>web channel's mailbox</b> — fires waiting for a browser to collect them. Slack and "
        "Discord get pushed into; a tab can only be drained, so a flow armed in a web chat delivers "
        "here and the chat surface polls this endpoint.",
        tier="ui",
        callers=["studio"],
        query=[
            ("thread_id", "the web conversation to read (required)", "web:studio"),
            ("since", "EXCLUSIVE epoch-seconds cursor; 0 ⇒ the whole backlog", "0"),
            (
                "max_age",
                "seconds — bound a FIRST load (ignored once <code>since</code> is set); "
                "cutoff uses the SERVER clock",
                "86400",
            ),
            ("limit", "max messages (default 50, cap 200)", "50"),
        ],
        responses=[
            (
                200,
                {
                    "thread_id": "web:studio",
                    "count": 1,
                    "scope": "default/default/local",
                    "cursor": 1785312000.4,
                    "messages": [
                        {
                            "id": "9f2c…",
                            "ts": 1785312000.4,
                            "scope": "default/default/local",
                            "thread_id": "web:studio",
                            "text": "⚡ flow fired · cron tick · IBM price\nIBM is trading at $291.40.",
                            "agent": "pricebot",
                            "subscription_id": "cuga-50993d",
                            "flow_name": "IBM price",
                            "event_kind": "tick",
                        }
                    ],
                },
                "Oldest first — the order a chat log appends them in. Send <code>cursor</code> back as the "
                "next <code>since</code> and a message is never rendered twice.",
            )
        ],
        notes="Why it exists: a cron armed at 09:00 fires at 09:05, with no request in flight and "
        "possibly no tab open. Before this the answer went to the runs log and nowhere else — "
        "the flow fired, the dashboard knew, and the chat that armed it never heard back. "
        "Messages are durable (same store as the subscription index), so a reloaded tab passing "
        "<code>since=0</code> recovers the fires it missed while closed. Channel-armed flows "
        "never land here (their thread resolves to a real channel), so there are no duplicates.",
    ),
    E(
        "GET",
        "/api/events/runs/{run_id}",
        "runs",
        "One run, with the agent's answer lifted out of the AP step tree.",
        tier="ui",
        callers=["studio"],
        path_params=[("run_id", "Activepieces run id", "r1")],
        responses=[
            (
                200,
                {
                    "ok": True,
                    "run": {
                        "id": "r1",
                        "status": "SUCCEEDED",
                        "started_at": "2026-07-09T09:00:00Z",
                        "finished_at": "2026-07-09T09:00:06Z",
                    },
                    "answer": "Your boss sent Q3 numbers; the ask is a revised forecast by Friday.",
                    "trigger_payload": {"subject": "Q3 numbers", "from": "boss@corp.com"},
                    "error": None,
                },
                "<code>answer</code> comes from <code>steps.&lt;n&gt;.output.body.answer</code> — the shape "
                "<code>/invoke</code> returns. <code>trigger_payload</code> is the raw event that fired it.",
            ),
            (
                200,
                {
                    "ok": True,
                    "run": {"id": "r1", "status": "FAILED"},
                    "answer": None,
                    "trigger_payload": None,
                    "error": "401 Bad credentials",
                },
                "A failed step's <code>errorMessage</code> surfaces here. This is where the GitHub "
                "stale-token failure is actually visible.",
            ),
            (404, {"ok": False, "error": "run not found"}, "Unknown, or not your flow."),
        ],
    ),
    # ── studio reads ──────────────────────────────────────────────────────────
    E(
        "GET",
        "/api/events/status",
        "studio",
        "What the events layer can do right now. The UI decides what to show from this.",
        tier="ui",
        callers=["studio", "tests"],
        responses=[
            (
                200,
                {
                    "ok": True,
                    "enabled": True,
                    "scope": "default/default/local",
                    "concierge_backend": "react",
                    "worker_backend": "cuga",
                    "backends": ["react", "cuga"],
                    "ap_configured": True,
                    "project_grain": "tenant",
                    "features": {
                        "now": True,
                        "cron": True,
                        "poll": True,
                        "push": True,
                        "channels_inbound": True,
                    },
                },
                "Every feature except <code>now</code> is gated on Activepieces being configured.",
            )
        ],
    ),
    E(
        "GET",
        "/api/events/channels",
        "studio",
        "Inbound chat channels and their connect state.",
        tier="ui",
        callers=["studio", "tests"],
        responses=[
            (
                200,
                {
                    "channels": [
                        {"name": "web", "status": "connected"},
                        {"name": "slack", "status": "connected"},
                        {"name": "discord", "status": "connected"},
                        {"name": "telegram", "status": "not_connected"},
                    ]
                },
                "",
            )
        ],
    ),
    E(
        "GET",
        "/api/events/integrations",
        "studio",
        "Box / GitHub / Gmail connection state, and which backend owns each.",
        tier="ui",
        callers=["studio", "tests"],
        responses=[
            (
                200,
                {
                    "integrations": [
                        {
                            "name": "box",
                            "status": "connected",
                            "backend": "direct",
                            "note": "DIRECT backend — CUGA polls Box with BOX_DEV_TOKEN (no AP, no OAuth).",
                        },
                        {
                            "name": "github",
                            "status": "auto_connect_pending",
                            "backend": "ap",
                            "note": "GITHUB_TOKEN is set — this auto-connects on startup. If it's still pending, "
                            "AP's github piece isn't installed yet (run `make ap-pieces`).",
                        },
                        {"name": "gmail", "status": "not_connected", "backend": "ap"},
                    ]
                },
                "Three distinct states, and the difference matters.",
            )
        ],
        notes="<code>auto_connect_pending</code> is neither connected nor broken: the credential is in "
        "<code>.env</code> but AP hasn't built the connection, almost always because the piece "
        "isn't installed on a fresh DB. Saying so beats a bare red dot. If AP is unreachable this "
        "returns <code>200</code> with <code>unknown</code> — never a 500.",
    ),
    E(
        "GET",
        "/api/events/examples",
        "studio",
        "The tagged catalog of example utterances.",
        tier="ui",
        callers=["studio", "tests"],
        responses=[
            (
                200,
                {
                    "examples": [
                        {
                            "id": "now-crypto",
                            "agent": "pricebot",
                            "utterance": "what is the current price of bitcoin?",
                            "trigger": "NOW",
                            "channel": "web",
                            "live": True,
                        }
                    ]
                },
                "Source of truth for the Studio's Examples tab and <code>events_docs/api/examples.html</code>.",
            )
        ],
    ),
    E(
        "GET",
        "/api/events/triggers",
        "studio",
        "The trigger registry — every (integration, event) the platform can watch.",
        tier="ui",
        callers=["studio", "tests"],
        responses=[
            (
                200,
                {
                    "apps": [
                        {
                            "app": "github",
                            "triggers": [
                                {
                                    "event": "new_pr",
                                    "title": "New Pull Request",
                                    "backend": "ap",
                                    "default": True,
                                    "fire": "synth",
                                    "piece": "github",
                                    "ap_trigger": "trigger_pull_request",
                                    "direct_kind": "",
                                    "slots": [
                                        {
                                            "name": "repo",
                                            "question": "Which repository (owner/repo) should I watch?",
                                            "required": True,
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                    "total": 33,
                    "kinds": ["channel_created", "new_pr", "…"],
                },
                "Grouped per app, the app's default trigger first.",
            )
        ],
        notes="Generated straight from <code>triggers.py</code> — drives the Studio agent editor's "
        "trigger-grain picker and the slides deck, so neither can drift from the code. "
        "<code>backend</code> says who receives the event (<code>ap</code> = an Activepieces "
        "flow, <code>direct</code> = CUGA itself); <code>fire</code> says how it can be "
        "verified (<code>synth</code> / <code>real</code> / <code>manual</code>).",
    ),
    E(
        "GET",
        "/api/events/setup-guides",
        "studio",
        "Per-connector setup guide + whether it is <i>actually</i> connected.",
        tier="ui",
        callers=["studio"],
        responses=[
            (
                200,
                {
                    "public_url": "https://your-domain.ngrok-free.app",
                    "guides": [
                        {
                            "app": "gmail",
                            "kind": "integration",
                            "connect": "oauth",
                            "creds": [
                                {"key": "EVENTS_OAUTH_GMAIL_CLIENT_ID", "present": True, "scope": "tenant"}
                            ],
                            "steps": ["Add https://…/api/events/connect/gmail/callback as a redirect URI"],
                            "conn_status": "not_connected",
                            "connected": False,
                            "connection_scope": "user",
                            "needs_connection": True,
                        }
                    ],
                },
                "",
            )
        ],
        notes="<code>present</code> (the credential is in <code>.env</code>) and <code>connected</code> "
        "(a real AP connection or direct token exists) are different questions, and confusing them "
        "is the single most common setup mistake.",
    ),
    # ── agents ────────────────────────────────────────────────────────────────
    E(
        "GET",
        "/api/events/agents",
        "agents",
        "READ-ONLY roster view: the sub-agents of the ONE agent ('cuga'), from supervisor_agents.yaml.",
        tier="ui",
        callers=["studio", "tests"],
        query=[("scope", "override the principal", "")],
        responses=[
            (
                200,
                {
                    "scope": "default/default",
                    "agents": [
                        {
                            "name": "pricebot",
                            "prompt": "You answer questions about asset prices…",
                            "backend": "cuga",
                            "mcp_servers": ["cuga-finance"],
                            "channels": ["web", "slack"],
                            "integrations": [],
                            "access": [],
                            "restricted": False,
                            "examples": ["what is the current price of bitcoin?"],
                            "can_use": True,
                        }
                    ],
                },
                "<code>can_use</code> applies per-agent access rules to <i>your</i> roles.",
            )
        ],
        notes="Agents are TENANT-shared: the scope here is the first two segments of the principal "
        "scope, not the full <code>tenant/instance/user</code>.",
    ),
    E(
        "POST",
        "/api/events/agents",
        "agents",
        "Create or upsert a worker agent. Idempotent by name.",
        tier="ui",
        callers=["studio"],
        auth="builder",
        body=[
            (
                "A new agent",
                {
                    "name": "webpage_summarizer",
                    "backend": "cuga",
                    "prompt": "Fetch the page the user names and summarise it in five bullets.",
                    "mcp_servers": ["cuga-web"],
                    "channels": ["web", "slack"],
                    "integrations": [],
                    "access": [],
                },
                "<code>mcp_servers</code> is validated against the live catalog "
                "(<code>GET /api/events/mcp-servers</code>) — an unknown name is a 400, not a silently "
                "broken agent.",
            ),
            (
                "An agent needing a per-user integration",
                {
                    "name": "resume_judge",
                    "backend": "cuga",
                    "prompt": "Judge the resume against the JD.",
                    "mcp_servers": ["cuga-text"],
                    "channels": ["web"],
                    "integrations": [
                        {"app": "box", "ownership": "per-user"},
                        {"app": "gmail", "ownership": "per-user"},
                    ],
                    "access": ["hiring"],
                },
                "Declaring two integrations means the connect gate asks for <b>both</b> — which is why "
                "\"watch my Box folder\" can legitimately answer \"connect your gmail\".",
            ),
            (
                "Restricted to a role",
                {
                    "name": "payroll_bot",
                    "backend": "cuga",
                    "prompt": "Answer payroll questions.",
                    "mcp_servers": ["cuga-text"],
                    "channels": ["web"],
                    "access": ["finance", "admin"],
                },
                "<code>access</code> is a role allow-list. It shows up as <code>restricted:true</code> and "
                "<code>can_use:false</code> for everyone else — the agent is still listed, just not usable.",
            ),
            (
                "The minimum viable agent",
                {"name": "echo", "prompt": "Repeat the user's message back."},
                "<code>backend</code> defaults to <code>cuga</code>; the rest default to empty. An agent "
                "with no <code>mcp_servers</code> can still reason — it just has no tools, which is exactly "
                "why <code>mailbot</code> cannot read your inbox.",
            ),
            (
                "A ReAct-backend agent",
                {
                    "name": "quickbot",
                    "backend": "react",
                    "prompt": "Answer briefly.",
                    "mcp_servers": ["cuga-web"],
                    "channels": ["web"],
                },
                "<code>react</code> is the lighter loop; <code>cuga</code> builds a full "
                "<code>DynamicAgentGraph</code> per agent. Anything else is a 400.",
            ),
        ],
        responses=[
            (200, {"ok": True, "name": "webpage_summarizer", "scope": "default/default"}, ""),
            (400, {"ok": False, "error": "unknown mcp_servers: ['cuga-magic'] (known: […])"}, ""),
            (403, {"ok": False, "error": "builder or admin only"}, ""),
            (
                410,
                {
                    "ok": False,
                    "error": "supervisor mode: sub-agents are defined in "
                    "supervisor_agents.yaml — edit + make reload",
                },
                "SINGLE-AGENT WORLD: registration through the API is retired; the roster YAML "
                "(canonical CUGA-main schema) is the source of truth. "
                "<code>GET /api/events/agents</code> stays as the read-only roster view.",
            ),
        ],
    ),
    E(
        "PUT",
        "/api/events/agents/{name}",
        "agents",
        "Replace an existing agent. Must already exist.",
        tier="ui",
        callers=["studio"],
        auth="builder",
        path_params=[("name", "agent name; must match the body", "webpage_summarizer")],
        body=[
            (
                "Edit the prompt",
                {
                    "name": "webpage_summarizer",
                    "backend": "cuga",
                    "prompt": "Summarise in three bullets, not five.",
                    "mcp_servers": ["cuga-web"],
                    "channels": ["web"],
                },
                "The body is a full replacement, not a patch: omit <code>channels</code> and the agent "
                "ends up with none.",
            ),
            (
                "Give an existing agent a tool it was missing",
                {
                    "name": "mailbot",
                    "backend": "cuga",
                    "prompt": "Summarise the email in the event payload.",
                    "mcp_servers": ["cuga-text", "cuga-web"],
                    "channels": ["web", "slack"],
                    "integrations": [{"app": "gmail", "ownership": "per-user"}],
                },
                "Note this still won't let <code>mailbot</code> <i>fetch</i> mail. Gmail is an "
                "integration — Activepieces holds the token and pushes the email in as "
                "<code>event.payload</code>. There is no Gmail <i>tool</i> for the agent to call.",
            ),
            (
                "Omit the name and let the URL supply it",
                {"backend": "cuga", "prompt": "Shorter.", "mcp_servers": ["cuga-web"]},
                "<code>name</code> defaults from the path. If you <i>do</i> send it and it disagrees "
                "with the URL, that's a 400 rather than a silent rename.",
            ),
        ],
        responses=[
            (200, {"ok": True, "name": "webpage_summarizer", "scope": "default/default"}, ""),
            (400, {"ok": False, "error": "name in body must match the URL"}, ""),
            (403, {"ok": False, "error": "builder or admin only"}, ""),
            (
                404,
                {"ok": False, "error": "no such agent 'ghost'"},
                "<code>PUT</code> updates; use <code>POST</code> to create.",
            ),
        ],
    ),
    E(
        "GET",
        "/api/events/docs/{page}",
        "studio",
        "Serve an API reference page so the Studio's API tab can embed it.",
        tier="ui",
        callers=["studio"],
        path_params=[("page", "api | spec | examples | slides | nlflow", "spec")],
        responses=[
            (
                200,
                "&lt;html&gt;…&lt;/html&gt;",
                "The requested page: <code>api</code>=api.html, <code>spec</code>=api_spec.html, "
                "<code>examples</code>=examples.html, <code>slides</code>=the event-driven-agents "
                "deck, <code>nlflow</code>=the NL→Flow explainer (the last two from "
                "<code>events_docs/</code>, one level up). Files resolve from "
                "<code>events_docs/api/</code> (override with <code>EVENTS_DOCS_DIR</code>).",
            ),
            (404, {"ok": False, "error": "unknown page"}, "Only those page names are served."),
            (
                404,
                {"ok": False, "error": "api_spec.html not found (set EVENTS_DOCS_DIR)"},
                "The file isn't where the server looked.",
            ),
        ],
        try_it=False,
    ),
    E(
        "GET",
        "/api/events/mcp-servers",
        "agents",
        "The tool servers a builder may attach to an agent.",
        tier="ui",
        callers=["studio"],
        responses=[
            (
                200,
                {
                    "servers": [
                        {"name": "cuga-finance", "hint": "crypto + stock quotes"},
                        {"name": "cuga-web", "hint": "web search, fetch, feeds, YouTube"},
                    ]
                },
                "Drives the agent-editor form, so the UI never hardcodes the catalog.",
            )
        ],
    ),
    # ── inbound ───────────────────────────────────────────────────────────────
    E(
        "GET",
        "/api/events/slack/events",
        "inbound",
        "Friendly probe for the Slack Request URL. Slack itself only ever POSTs here; this GET exists "
        "purely so that pasting the URL into a browser returns a useful explanation instead of a bare "
        "405, which reads as 'wrong host' and cost real debugging time. Returns the endpoint's health, "
        "a copy-pasteable curl that exercises the real handshake, and a reminder that the Request URL "
        "points at the EVENTING service (cuga-events-svc), never at CUGA (cuga-core).",
        tier="edge",
        callers=["browser", "operator"],
        auth="none",
        try_it=False,
    ),
    E(
        "POST",
        "/api/events/slack/events",
        "inbound",
        "Slack Events API receiver. This is CUGA, not Activepieces.",
        tier="edge",
        callers=["channel"],
        auth="slack",
        try_it=False,
        body=[
            (
                "URL verification handshake",
                {"type": "url_verification", "challenge": "3eZbrw1aB…"},
                "Answered with the bare challenge string as <code>text/plain</code>, before any "
                "signature check — Slack hasn't given you a secret to verify with yet.",
            ),
            (
                "A human message",
                {
                    "type": "event_callback",
                    "event": {
                        "type": "message",
                        "text": "what is bitcoin worth?",
                        "channel": "C0BEYJ9NATB",
                        "user": "U123",
                        "ts": "1783630758.450769",
                    },
                },
                "Acked in under 3 seconds (Slack's timeout); the agent runs in a background task and "
                "posts the reply into the thread. A root message with no <code>thread_ts</code> uses its "
                "own <code>ts</code>, so the bot's reply <i>starts</i> a thread.",
            ),
            (
                "A threaded reply",
                {
                    "type": "event_callback",
                    "event": {
                        "type": "message",
                        "text": "and ethereum?",
                        "channel": "C0BEYJ9NATB",
                        "user": "U123",
                        "ts": "1783630801.11",
                        "thread_ts": "1783630758.450769",
                    },
                },
                "<code>thread_ts</code> keys the conversation memory: one Slack thread = one topic. The "
                "delivery target is still the channel — <code>channel_native_id</code> strips the "
                "<code>#&lt;ts&gt;</code> suffix.",
            ),
            (
                "The bot's own message (ignored)",
                {
                    "type": "event_callback",
                    "event": {
                        "type": "message",
                        "text": "Bitcoin is $63,240.",
                        "channel": "C0BEYJ9NATB",
                        "bot_id": "B123",
                        "ts": "1783630760.0",
                    },
                },
                "<code>should_process</code> filters out bot messages, edits and joins. Without that the "
                "bot answers itself, forever.",
            ),
        ],
        responses=[
            (200, {"ok": True}, "The ack. The answer arrives in Slack, not in this response."),
            (200, "3eZbrw1aB…", "The handshake echo, as <code>text/plain</code>."),
            (400, {"ok": False, "error": "bad json"}, ""),
            (401, {"ok": False, "error": "bad signature"}, ""),
        ],
        notes="<b>With <code>SLACK_SIGNING_SECRET</code> unset, <code>verify_signature()</code> returns "
        "true</b> — the endpoint accepts unsigned requests from anyone who finds your public URL. "
        "The live harness exercises it unsigned on purpose, and prints a warning.",
    ),
    E(
        "POST",
        "/api/events/box/poll",
        "inbound",
        "Poll a Box folder and fire the watcher agent on each new item. <code>kind</code> selects "
        "WHICH box trigger: <code>new_file</code> (default), <code>new_folder</code>, or "
        "<code>new_box_comment</code> — the three box rows in the trigger registry, all served by the "
        "same CUGA-side poller (no Activepieces, no OAuth).",
        tier="edge",
        callers=["operator", "cuga"],
        auth="gateway",
        body=[
            (
                "Watch for new FOLDERS",
                {"folder_id": "0", "kind": "new_folder", "agent": "support_digest"},
                "The poller lists subfolders created after the watermark. (It used to list files only — "
                "<code>should_process</code> explicitly skipped folders.)",
            ),
            (
                "Watch for new COMMENTS on the folder's files",
                {"folder_id": "0", "kind": "new_box_comment", "agent": "incident_triage"},
                "Box has no folder-level comments feed, so the poller walks the folder's files and "
                "collects each file's comments. Fine at watched-folder scale; not a general Box crawler.",
            ),
            (
                "Server-tracked watermark (a standing poll)",
                {"folder_id": "0"},
                "Omitting <code>since</code> makes the server use its own last-seen watermark for that "
                "folder, so a scheduled poll only fires on genuinely new files. This is the call you put "
                "on a timer.",
            ),
            (
                "Manual poll from an explicit timestamp",
                {
                    "folder_id": "0",
                    "since": "2026-07-01T00:00:00-07:00",
                    "agent": "resume_judge",
                    "deliver_to": "slack",
                    "deliver_target": "C0BEYJ9NATB",
                },
                "<code>since</code> in the body wins and the watermark is <b>not</b> advanced — safe "
                "for tests, and re-runnable. Without <code>deliver_target</code> a direct sink has no "
                "destination and the answer only rides back in the response.",
            ),
            (
                "Replay everything in the folder",
                {"folder_id": "0", "since": "1970-01-01T00:00:00Z"},
                "Fires the agent once per file. Useful for backfilling; be aware it delivers to the sink "
                "each time.",
            ),
            (
                "A different folder and agent, answer returned inline",
                {"folder_id": "312094830", "agent": "webpage_summarizer"},
                "With no <code>deliver_to</code> the source becomes "
                "<code>{type:\"integration\", name:\"box\"}</code> and nothing is sent anywhere.",
            ),
            (
                "Poll on someone else's behalf",
                {"folder_id": "0", "scope": "acme/prod/alice"},
                "<code>scope</code> rides into the <code>/invoke</code> envelope, so the agent runs with "
                "Alice's isolation. Guard this endpoint with <code>GATEWAY_TOKEN</code>: it lets the "
                "caller choose whose scope to run as.",
            ),
        ],
        responses=[
            (
                200,
                {
                    "ok": True,
                    "folder": "0",
                    "processed": [{"id": "9", "name": "resume.pdf"}],
                    "newest": "2026-07-06T11:00:00-07:00",
                    "trace_id": "a1b2…",
                },
                "<code>newest</code> is the watermark to store as the next <code>since</code>.",
            ),
            (401, {"ok": False, "error": "bad or missing X-Gateway-Token"}, ""),
            (
                502,
                {"ok": False, "error": "401 Unauthorized"},
                "Box rejected the token. Loud, not silent — a stale <code>BOX_DEV_TOKEN</code> "
                "would otherwise look like \"no new files\".",
            ),
        ],
        notes="Opt-in, behind <code>EVENTS_BOX_BACKEND=direct</code>. It sidesteps AP's OAuth wall and "
        "Box's paid-app webhook requirement. Box otherwise defaults to an AP push trigger."
        "<br><br><b>The download step.</b> A watcher that learns only a filename cannot judge a "
        "resume, and the agent holds no Box credential to fetch it with. So CUGA downloads the "
        "bytes with the token it already has and hands the agent <i>contents</i>: decodable text "
        "is inlined into the prompt, anything else arrives base64 in "
        "<code>event.payload.file_base64</code> for <code>extract_text_from_bytes</code>. The "
        "credential never leaves the server. Capped by "
        "<code>EVENTS_BOX_MAX_DOWNLOAD_BYTES</code> (2 MB) and "
        "<code>EVENTS_BOX_MAX_INLINE_CHARS</code> (20k); disable with "
        "<code>EVENTS_BOX_DOWNLOAD=0</code>. A failed download never drops the event — the reason "
        "travels to the agent, which is told not to invent the contents.",
    ),
    E(
        "POST",
        "/api/events/hook/{name}",
        "inbound",
        "Generic inbound webhook: any system POSTs JSON, an agent triages it.",
        tier="edge",
        callers=["external"],
        auth="hookkey",
        path_params=[("name", "a label for this hook; free-form", "monitoring")],
        query=[
            ("agent", "PINNED mode: legacy explicit target (single-agent world: use 'cuga')", "cuga"),
            (
                "route",
                "ROUTED mode: 1/true/llm → the payload lands on THE one agent ('cuga'); "
                "its supervisor picks the specialist internally. Overrides ?agent",
                "1",
            ),
            ("deliver_to", "channel to post the result into", "slack"),
            ("target", "that channel's native id", "C0BEYJ9NATB"),
            ("key", "required iff EVENTS_WEBHOOK_KEY is set", ""),
        ],
        body=[
            (
                "A monitoring alert",
                {
                    "alert": "HighCPU",
                    "service": "checkout-api",
                    "value": 97,
                    "threshold": 85,
                    "severity": "page",
                },
                "The payload is JSON-dumped into the prompt (first 4000 chars). The agent sees the raw "
                "shape — no schema is imposed, so anything JSON works.",
            ),
            (
                "A CI failure (GitHub Actions)",
                {
                    "action": "completed",
                    "workflow_run": {
                        "name": "test",
                        "conclusion": "failure",
                        "head_branch": "feat/cuga-loops",
                        "html_url": "https://github.com/owner/repo/actions/runs/42",
                    },
                },
                "Point an Actions webhook straight at this URL with "
                "<code>?agent=incident_triage&amp;deliver_to=slack&amp;target=C…</code> and failures get "
                "triaged into a channel. No Activepieces piece needed.",
            ),
            (
                "A payment provider event",
                {
                    "type": "charge.failed",
                    "data": {
                        "object": {
                            "id": "ch_3P…",
                            "amount": 4900,
                            "currency": "usd",
                            "failure_message": "Your card has insufficient funds.",
                        }
                    },
                },
                "Deeply nested payloads are fine — the agent reads the JSON as text. Note this endpoint "
                "does <b>not</b> verify provider-specific signatures (Stripe's "
                "<code>Stripe-Signature</code>, say); only the shared <code>?key=</code>.",
            ),
            (
                "A form submission",
                {"name": "Ada", "email": "ada@corp.com", "message": "Interested in a demo next week."},
                "With <code>?agent=support_digest</code> this becomes a triaged lead in your channel.",
            ),
            (
                "ROUTED — no agent named (<code>?route=1</code>)",
                {"type": "charge.dispute.created", "amount": 48000, "reason": "fraudulent"},
                "With <code>?route=1</code> the caller names NO agent — the payload lands on THE one "
                "agent (<code>cuga</code>), whose supervisor picks the right specialist internally, "
                "per event. The response's <code>agent</code> field reports <code>cuga</code>. "
                "Decouples the external system from the roster; add or rename sub-agents in "
                "supervisor_agents.yaml and the URL never changes.",
            ),
            (
                "An empty body",
                {},
                "Accepted. The agent is told <code>(empty body)</code> rather than "
                "the request being rejected — a health-check ping still exercises the whole path.",
            ),
            (
                "A non-object body",
                [1, 2, 3],
                "Arrays and scalars are serialised into the prompt, but "
                "<code>event.payload</code> is only populated when the body is an object — so the agent "
                "sees the text, and structured consumers see <code>{}</code>.",
            ),
        ],
        responses=[
            (
                200,
                {
                    "ok": True,
                    "webhook": "monitoring",
                    "routed": False,
                    "agent": "incident_triage",
                    "answer": "P1 — checkout-api CPU 97% vs 85% threshold…",
                },
                "PINNED: <code>agent</code> is the one you named.",
            ),
            (
                200,
                {
                    "ok": True,
                    "webhook": "stripe",
                    "routed": True,
                    "agent": "incident_triage",
                    "answer": "P1 — payment dispute…",
                },
                "ROUTED (<code>?route=1</code>): <code>agent</code> is the concierge's pick.",
            ),
            (
                401,
                {"ok": False, "error": "bad or missing ?key"},
                "Compared with <code>hmac.compare_digest</code>.",
            ),
            (
                502,
                {"ok": False, "webhook": "monitoring", "error": "…"},
                "The internal <code>/invoke</code> call failed.",
            ),
        ],
        notes="No Activepieces, no piece, no connection — it is a thin wrapper over the "
        "<code>/invoke</code> seam. Two ways to choose the agent: <b>PINNED</b> "
        "(<code>?agent=</code>) or <b>ROUTED</b> (<code>?route=1</code>, the concierge picks it "
        "like chat). <b>Unset <code>EVENTS_WEBHOOK_KEY</code> leaves it open</b> to anyone who "
        "finds the public URL.",
    ),
    # ── connect ───────────────────────────────────────────────────────────────
    E(
        "GET",
        "/api/events/connect/{app}",
        "connect",
        "Begin connecting the caller's own account. OAuth → 302 to consent.",
        tier="ui",
        callers=["browser", "studio"],
        try_it=False,
        path_params=[("app", "box · gmail · github · slack · telegram · discord", "gmail")],
        query=[
            ("ownership", "<code>per-user</code> (default) or <code>tenant</code>", "per-user"),
            ("return", "URL to bounce back to after consent", ""),
        ],
        responses=[
            (
                302,
                "Location: https://accounts.google.com/o/oauth2/auth?…",
                "OAuth apps. The <code>state</code> carries your scope, so the callback knows who you are.",
            ),
            (
                200,
                {
                    "ok": True,
                    "app": "telegram",
                    "kind": "token",
                    "message": "POST your telegram token to /api/events/connect/telegram/token",
                },
                "Token apps (telegram · discord): nothing to redirect to. "
                "OAuth apps (gmail · box · github · slack) 302 instead.",
            ),
            (404, {"ok": False, "error": "unknown app 'myspace'"}, ""),
            (
                501,
                {
                    "ok": False,
                    "app": "gmail",
                    "kind": "oauth",
                    "error": "OAuth not configured — set EVENTS_OAUTH_GMAIL_CLIENT_ID / _CLIENT_SECRET",
                },
                "",
            ),
        ],
    ),
    E(
        "GET",
        "/api/events/connect/{app}/callback",
        "connect",
        "OAuth redirect target: exchange the code, create the AP connection.",
        tier="edge",
        callers=["browser"],
        try_it=False,
        path_params=[("app", "the app being connected", "gmail")],
        query=[
            ("code", "authorization code from the provider", ""),
            ("state", "opaque; carries scope + ownership + return", ""),
        ],
        responses=[
            (200, "&lt;h3&gt;✅ gmail connected&lt;/h3&gt;", "HTML, for a human in a browser."),
            (302, "Location: &lt;the ?return= URL&gt;", "When the flow began with a return URL."),
            (400, "&lt;h3&gt;Connect failed&lt;/h3&gt;&lt;p&gt;No authorization code.&lt;/p&gt;", ""),
            (500, "&lt;h3&gt;Connect failed&lt;/h3&gt;&lt;p&gt;…&lt;/p&gt;", "AP refused the exchange."),
        ],
        notes="<b>Activepieces does the code→token exchange itself.</b> Its OAuth2 connection schema "
        "wants the authorization <i>code</i>, which is why a pre-obtained access token can never "
        "be pasted in for an OAuth app. Gmail refresh tokens issued by an app in Google's "
        "\"Testing\" mode expire after 7 days — a reconnect is then required.",
    ),
    E(
        "POST",
        "/api/events/connect/{app}/token",
        "connect",
        "Paste a raw credential → a SECRET_TEXT Activepieces connection. Token apps only.",
        tier="ui",
        callers=["studio", "operator"],
        path_params=[("app", "github · telegram · discord", "github")],
        body=[
            (
                "A GitHub PAT",
                {"token": "ghp_xxxxxxxxxxxxxxxx"},
                "Needs <code>admin:repo_hook</code> to arm a PUSH watcher — otherwise the flow arms and "
                "GitHub answers <code>401 Bad credentials</code> when AP tries to create the webhook. "
                "That failure surfaces at <code>GET /api/events/runs/&lt;id&gt;</code>, not here.",
            ),
            (
                "Shared across the tenant",
                {"token": "ghp_xxx", "ownership": "tenant"},
                "Default is <code>per-user</code>: each user connects their own account. "
                "<code>tenant</code> makes one connection the whole tenant routes through — right for a "
                "bot token, wrong for a personal PAT.",
            ),
            (
                "A Telegram bot token",
                {"token": "8123456789:AAH…"},
                "Channels use the same path as integrations. One bot per tenant, so "
                "<code>ownership: \"tenant\"</code> is usually what you want.",
            ),
            (
                "A Discord bot token",
                {"token": "MTUyMj…", "ownership": "tenant"},
                "Only needed for the AP polling backend. The default Discord backend is a direct "
                "Gateway bot that reads <code>DISCORD_BOT_TOKEN</code> from the environment and needs no "
                "AP connection at all.",
            ),
            (
                "Explicit scope (admin acting for a user)",
                {"token": "ghp_xxx", "scope": "acme/prod/alice"},
                "Creates <code>ea::acme::alice::github</code>. Without <code>scope</code> the principal "
                "comes from the request headers.",
            ),
        ],
        responses=[
            (200, {"ok": True, "app": "github", "connection": "ea::default::local::github"}, ""),
            (400, {"ok": False, "error": "missing 'token'"}, ""),
            (
                400,
                {
                    "ok": False,
                    "app": "gmail",
                    "error": "'gmail' is an OAuth connector — AP can't build a connection from a "
                    "pasted token. Use the OAuth login: GET /api/events/connect/gmail",
                },
                "A clear refusal beats forwarding it to AP and surfacing a cryptic schema error.",
            ),
            (404, {"ok": False, "error": "unknown app 'myspace'"}, ""),
            (500, {"ok": False, "error": "connection_name_already_exists"}, ""),
            (501, {"ok": False, "error": "AP not configured"}, ""),
        ],
        notes="<b>Rotation works here, and only here.</b> This endpoint overwrites an existing "
        "connection (and, if Activepieces refuses the overwrite, deletes and recreates it), so "
        "pasting a fresh PAT genuinely replaces the dead one. <i>Fixed 2026-07-09; it used to "
        "return early, which is why a rotated GitHub token kept failing at run time with "
        "<code>401 Bad credentials</code> — nowhere near the paste that caused it.</i><br><br>"
        "Boot-time auto-connect from <code>.env</code> does <b>not</b> overwrite: it only creates "
        "what is missing, so a connection you authorized by hand is never clobbered by a stale "
        "environment variable. To rotate, POST here.",
    ),
    E(
        "GET",
        "/api/events/connections",
        "connect",
        "The caller's own Activepieces connections.",
        tier="ui",
        callers=["studio"],
        query=[("scope", "override the principal", "")],
        responses=[
            (
                200,
                {
                    "scope": "default/default/local",
                    "connections": [{"externalId": "ea::default::local::github"}],
                },
                "Filtered on <code>::&lt;user_id&gt;::</code> in the externalId — another user's "
                "connection in the same AP project is invisible.",
            )
        ],
    ),
    # ── identity ──────────────────────────────────────────────────────────────
    E(
        "GET",
        "/api/events/me",
        "identity",
        "The caller's profile: who they are, roles, linked channels, connections.",
        tier="ui",
        callers=["studio"],
        query=[("scope", "override the principal", "")],
        responses=[
            (
                200,
                {
                    "scope": "default/default/local",
                    "user_id": "local",
                    "email": "a@corp.com",
                    "roles": ["admin"],
                    "linked_channels": [{"channel": "telegram", "native_id": "8840265085"}],
                    "connections": [{"externalId": "ea::default::local::github"}],
                },
                "",
            )
        ],
        notes="If Activepieces is unreachable, <code>connections</code> comes back empty rather than "
        "500-ing the profile page.",
    ),
    E(
        "POST",
        "/api/events/link/{channel}",
        "identity",
        "Issue a link token binding a chat account to this profile.",
        tier="ui",
        callers=["studio"],
        path_params=[("channel", "telegram · slack · discord", "telegram")],
        body=[
            ("Issue a token", {}, "The body may be empty; the principal comes from headers."),
            (
                "Issue on behalf of another user",
                {"scope": "acme/prod/alice"},
                "The token binds whichever profile the scope names — so an admin can hand Alice a code "
                "to paste into her own Telegram.",
            ),
        ],
        responses=[
            (
                200,
                {
                    "ok": True,
                    "channel": "telegram",
                    "token": "4f3c1a…",
                    "how": "open https://t.me/mybot?start=4f3c1a…",
                },
                "Send that to the bot and the sender's native id binds to your account.",
            ),
            (501, {"ok": False, "error": "identity map not configured"}, ""),
        ],
        notes="Until a channel id is linked, an inbound message resolves to the fallback scope — which "
        "is why an unlinked Slack user can appear as <code>local</code>.",
    ),
    # ── admin ─────────────────────────────────────────────────────────────────
    E(
        "GET",
        "/api/events/admin/users",
        "admin",
        "List the tenant's users.",
        tier="ui",
        callers=["studio"],
        auth="admin",
        responses=[
            (200, {"users": [{"user_id": "local", "email": "a@corp.com", "roles": ["admin"]}]}, ""),
            (403, {"ok": False, "error": "admin only"}, ""),
        ],
        notes="With <b>no user store configured</b> the admin gate is open — a dev-mode default worth "
        "knowing before you expose the server.",
    ),
    E(
        "POST",
        "/api/events/admin/users",
        "admin",
        "Add a user.",
        tier="ui",
        callers=["studio"],
        auth="admin",
        body=[
            (
                "A regular user",
                {"user_id": "bob", "email": "b@corp.com", "roles": ["user"]},
                "<code>roles</code> defaults to <code>[\"user\"]</code> when omitted.",
            ),
            (
                "A builder",
                {"user_id": "carol", "email": "c@corp.com", "roles": ["builder"]},
                "Builders may create and edit agents (<code>POST/PUT /api/events/agents</code>).",
            ),
            (
                "Another admin",
                {"user_id": "dave", "email": "d@corp.com", "roles": ["admin"]},
                "Admins pass both the admin and the builder gate.",
            ),
            (
                "With a password",
                {
                    "user_id": "erin",
                    "email": "e@corp.com",
                    "roles": ["user"],
                    "password": "correct-horse-battery-staple",
                },
                "Optional. Stored by the user store; the events layer itself does no login.",
            ),
            (
                "Grant access to a restricted agent",
                {"user_id": "frank", "email": "f@corp.com", "roles": ["user", "finance"]},
                "Roles double as the allow-list for an agent's <code>access</code> field — Frank can now "
                "use <code>payroll_bot</code>.",
            ),
        ],
        responses=[
            (200, {"ok": True, "user": {"user_id": "bob", "email": "b@corp.com", "roles": ["user"]}}, ""),
            (400, {"ok": False, "error": "missing user_id"}, ""),
            (403, {"ok": False, "error": "admin only"}, ""),
            (501, {"ok": False, "error": "user store not configured"}, ""),
        ],
    ),
    E(
        "POST",
        "/api/events/admin/channels/{channel}/arm",
        "admin",
        "Arm a channel's inbound flow.",
        tier="ops",
        callers=["operator", "studio"],
        auth="admin",
        path_params=[("channel", "slack · discord · telegram", "telegram")],
        body=[
            ("Telegram (via AP)", {}, "No trigger input needed — the bot polls all its chats."),
            (
                "Discord (via AP, one channel)",
                {"channel": "1522408587958423675"},
                "Every body key except <code>scope</code> is passed straight through as the AP trigger's "
                "input, so this is how channel-specific trigger settings get set. Discord's AP piece "
                "polls exactly ONE channel.",
            ),
            (
                "Slack",
                {},
                "Returns the direct-backend answer with the events_url to paste into your Slack app. "
                "Nothing is armed in AP unless <code>EVENTS_SLACK_BACKEND=ap</code>.",
            ),
            (
                "Arm for a specific tenant",
                {"scope": "acme/prod/alice"},
                "<code>scope</code> is stripped from the trigger input and used to pick the AP project "
                "and the connection.",
            ),
        ],
        responses=[
            (200, {"ok": True, "channel": "telegram", "ap_flow_id": "URoIxzSflT"}, "The AP path."),
            (
                200,
                {
                    "ok": True,
                    "channel": "slack",
                    "backend": "direct",
                    "events_url": "https://…/api/events/slack/events",
                    "signature_verification": "OFF (set SLACK_SIGNING_SECRET)",
                    "note": "Set this events_url as your Slack app's Request URL…",
                },
                "Slack's default backend is direct — nothing to arm in AP.",
            ),
            (
                200,
                {
                    "ok": True,
                    "channel": "discord",
                    "backend": "direct",
                    "note": "Direct Gateway backend — the bot connects on server start…",
                },
                "Discord's default backend is a Gateway WebSocket bot; it needs no public URL.",
            ),
            (400, {"ok": False, "error": "set DISCORD_BOT_TOKEN"}, ""),
            (403, {"ok": False, "error": "admin only"}, ""),
            (501, {"ok": False, "error": "AP not configured"}, ""),
        ],
        notes="<code>make channels</code> calls this for every channel with a token in <code>.env</code>.",
    ),
    E(
        "GET",
        "/api/events/admin/oauth-apps",
        "admin",
        "Which OAuth providers have a client id/secret configured. No secrets returned.",
        tier="ui",
        callers=["studio"],
        auth="admin",
        responses=[
            (200, {"apps": [{"app": "gmail", "configured": True}, {"app": "box", "configured": False}]}, ""),
            (403, {"ok": False, "error": "admin only"}, ""),
        ],
    ),
    E(
        "POST",
        "/api/events/admin/oauth-apps",
        "admin",
        "Enter a provider's OAuth client id/secret once, from the UI instead of .env.",
        tier="ui",
        callers=["studio"],
        auth="admin",
        body=[
            (
                "Configure Gmail",
                {
                    "app": "gmail",
                    "client_id": "…apps.googleusercontent.com",
                    "client_secret": "GOCSPX-…",
                    "scopes": "https://www.googleapis.com/auth/gmail.readonly",
                },
                "Equivalent to setting <code>EVENTS_OAUTH_GMAIL_CLIENT_ID</code> / "
                "<code>_CLIENT_SECRET</code>, but per-tenant and without a restart.",
            ),
            (
                "Configure Box",
                {"app": "box", "client_id": "abc123", "client_secret": "s3cret", "scopes": "root_readwrite"},
                "",
            ),
            (
                "Omit scopes and take the provider default",
                {"app": "gmail", "client_id": "…", "client_secret": "…"},
                "<code>scopes</code> is optional; the provider table supplies its own.",
            ),
        ],
        responses=[
            (200, {"ok": True, "app": "gmail", "configured": True}, ""),
            (400, {"ok": False, "error": "need app, client_id, client_secret"}, ""),
            (403, {"ok": False, "error": "admin only"}, ""),
            (501, {"ok": False, "error": "oauth store not configured"}, ""),
        ],
    ),
    E(
        "POST",
        "/api/events/admin/credential",
        "admin",
        "Set/modify one connector credential (its .env variable) from the Studio — the universal "
        "'edit this credential' seam for channels + integrations. Persists to .env and updates the "
        "live process; whitelisted to the known connector cred keys so the UI can never inject "
        "arbitrary environment. Reports whether it applied live or needs <code>make reload</code>.",
        tier="ui",
        callers=["studio"],
        auth="admin",
        body=[
            (
                "Rotate the Slack bot token (applies live)",
                {"key": "SLACK_BOT_TOKEN", "value": "xoxb-…"},
                "Keys in the live set (Slack/Box tokens, OAuth app secrets) apply immediately.",
            ),
            (
                "Set the Telegram bot token (needs a reload)",
                {"key": "TELEGRAM_BOT_TOKEN", "value": "123456:ABC-…"},
                "Read at startup → the response says to run <code>make reload</code>.",
            ),
        ],
        responses=[
            (
                200,
                {
                    "ok": True,
                    "key": "SLACK_BOT_TOKEN",
                    "live": True,
                    "note": "Saved to .env and applied live — no restart needed.",
                },
                "",
            ),
            (400, {"ok": False, "error": "'FOO' is not an editable connector credential"}, ""),
            (403, {"ok": False, "error": "admin only"}, ""),
        ],
    ),
]

AUTH_CHIP = {
    "none": ("", ""),
    "gateway": (
        "gateway token",
        "Requires <code>X-Gateway-Token</code> when <code>GATEWAY_TOKEN</code> is set.",
    ),
    "admin": ("admin", "Admin role required. Open when no user store is configured."),
    "builder": ("builder", "Builder or admin role required."),
    "slack": (
        "slack signature",
        "Verified against <code>SLACK_SIGNING_SECRET</code>; <b>open when unset</b>.",
    ),
    "hookkey": ("?key=", "Required only when <code>EVENTS_WEBHOOK_KEY</code> is set."),
}


# ── route coverage: the reason this file can't rot ────────────────────────────
def routes_in_code() -> set[tuple[str, str]]:
    src = APP.read_text()
    return {(v.upper(), p) for v, p in re.findall(r'@app\.(get|post|delete|put)\("([^"]+)"\)', src)}


def check_coverage() -> list[str]:
    documented = {(e["verb"], e["path"]) for e in ENDPOINTS}
    actual = routes_in_code()
    problems = [f"UNDOCUMENTED: {v} {p}" for v, p in sorted(actual - documented)]
    problems += [f"DOCUMENTED BUT GONE: {v} {p}" for v, p in sorted(documented - actual)]
    return problems


# ── rendering ─────────────────────────────────────────────────────────────────
def e(t) -> str:
    return html.escape(str(t))


def j(obj) -> str:
    return obj if isinstance(obj, str) else e(json.dumps(obj, indent=2))


def slug(ep) -> str:
    return (ep["verb"] + ep["path"]).lower().replace("/", "-").replace("{", "").replace("}", "")


def render_endpoint(ep) -> str:
    a_label, a_help = AUTH_CHIP[ep["auth"]]
    chips = "".join(
        f'<span class="chip who" title="{e(ACTORS[c][1])}">{e(ACTORS[c][0])}</span>' for c in ep["callers"]
    )
    auth = f'<span class="chip auth" title="{a_help}">🔒 {a_label}</span>' if a_label else ""
    tlabel, _, thelp = TIERS[ep["tier"]]
    tier = (
        f'<span class="tier t-{ep["tier"]}" '
        f'title="{e(re.sub(chr(60) + "[^>]+" + chr(62), "", thelp))}">{tlabel}</span>'
    )

    params = ""
    if ep["path_params"] or ep["query"]:
        rows = "".join(
            f'<tr><td><code>{e(n)}</code></td><td class="k">path</td><td>{d}</td></tr>'
            for n, d, _ in ep["path_params"]
        )
        rows += "".join(
            f'<tr><td><code>{e(n)}</code></td><td class="k">query</td><td>{d}</td></tr>'
            for n, d, _ in ep["query"]
        )
        params = '<h4>Parameters</h4><table class="params"><tbody>' + rows + "</tbody></table>"

    bodies = ""
    if ep["body"]:
        tabs = "".join(
            f'<button class="btab{" on" if i == 0 else ""}" data-i="{i}">{e(t)}</button>'
            for i, (t, _, _) in enumerate(ep["body"])
        )
        panes = "".join(
            f'<div class="bpane{" on" if i == 0 else ""}" data-i="{i}">'
            f'<pre class="blk">{j(p)}</pre>'
            f'{f"<p class=note>{n}</p>" if n else ""}</div>'
            for i, (_, p, n) in enumerate(ep["body"])
        )
        bodies = f'<h4>Example payloads</h4><div class="tabs">{tabs}</div>{panes}'

    resp = ""
    if ep["responses"]:
        resp = "<h4>Responses</h4>" + "".join(
            f'<div class="resp"><span class="code c{str(c)[0]}">{c}</span>'
            f'<div class="rbody"><pre class="blk">{j(b)}</pre>'
            f'{f"<p class=note>{w}</p>" if w else ""}</div></div>'
            for c, b, w in ep["responses"]
        )

    notes = f'<div class="callout">{ep["notes"]}</div>' if ep["notes"] else ""

    tryit = ""
    if ep["try_it"]:
        pp = "".join(
            f'<label>{e(n)} <input class="pp" data-name="{e(n)}" value="{e(dv)}"></label>'
            for n, _, dv in ep["path_params"]
        )
        qp = "".join(
            f'<label>?{e(n)} <input class="qp" data-name="{e(n)}" value="{e(dv)}"></label>'
            for n, _, dv in ep["query"]
        )
        first = json.dumps(ep["body"][0][1], indent=2) if ep["body"] else ""
        ta = (
            f'<textarea class="tbody" rows="8" spellcheck="false">{e(first)}</textarea>' if ep["body"] else ""
        )
        tryit = f"""
    <details class="try">
      <summary>Try it</summary>
      <div class="tform">
        <div class="tparams">{pp}{qp}</div>
        {ta}
        <div class="tbtns">
          <button class="send">Send ▸</button>
          <button class="curl">Copy as curl</button>
          <span class="tstat"></span>
        </div>
        <pre class="tout blk"></pre>
      </div>
    </details>"""

    # <details> rather than a JS toggle: it collapses with scripting off, it is keyboard-operable,
    # and the browser's own find-in-page opens a closed card to reveal a hit.
    return f"""
  <details class="ep" id="{slug(ep)}" data-verb="{ep['verb']}" data-path="{e(ep['path'])}"
       data-auth="{ep['auth']}" data-tier="{ep['tier']}"
       data-search="{e((ep['verb'] + ' ' + ep['path'] + ' ' + ep['summary']).lower())}">
    <summary class="hd">
      <span class="verb v-{ep['verb'].lower()}">{ep['verb']}</span>
      <code class="path">{e(ep['path'])}</code>
      {tier}
      <span class="peek">{ep['summary']}</span>
      <a class="anchor" href="#{slug(ep)}" title="link to this endpoint">#</a>
    </summary>
    <div class="epbody">
      <p class="summary">{ep['summary']}</p>
      <div class="chips"><span class="lbl">called by</span>{chips}{auth}</div>
      {params}{bodies}{resp}{notes}{tryit}
    </div>
  </details>"""


CSS = """
:root{--bg:#fbfbfa;--panel:#fff;--ink:#1c1b1a;--muted:#6b6866;--line:#e6e3df;--accent:#2b5fd9;
 --get:#1a7f4b;--post:#2b5fd9;--put:#b06f10;--del:#c0392b;--code-bg:#f4f3f1;
 --c2:#1a7f4b;--c3:#b06f10;--c4:#c0392b;--c5:#7a3fa8;--chip:#eef1f7;}
@media(prefers-color-scheme:dark){:root{--bg:#16181c;--panel:#1d2025;--ink:#e8e6e3;--muted:#9a9793;
 --line:#2e3238;--accent:#7aa2f7;--get:#5ed19a;--post:#7aa2f7;--put:#f0b95c;--del:#ff8a80;
 --code-bg:#111317;--c2:#5ed19a;--c3:#f0b95c;--c4:#ff8a80;--c5:#c79bec;--chip:#252a33;}}
:root[data-theme=dark]{--bg:#16181c;--panel:#1d2025;--ink:#e8e6e3;--muted:#9a9793;--line:#2e3238;
 --accent:#7aa2f7;--get:#5ed19a;--post:#7aa2f7;--put:#f0b95c;--del:#ff8a80;--code-bg:#111317;
 --c2:#5ed19a;--c3:#f0b95c;--c4:#ff8a80;--c5:#c79bec;--chip:#252a33;}
:root[data-theme=light]{--bg:#fbfbfa;--panel:#fff;--ink:#1c1b1a;--muted:#6b6866;--line:#e6e3df;
 --accent:#2b5fd9;--get:#1a7f4b;--post:#2b5fd9;--put:#b06f10;--del:#c0392b;--code-bg:#f4f3f1;
 --c2:#1a7f4b;--c3:#b06f10;--c4:#c0392b;--c5:#7a3fa8;--chip:#eef1f7;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
 font:15px/1.65 ui-sans-serif,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.layout{display:grid;grid-template-columns:260px minmax(0,1fr);gap:0;max-width:1400px;margin:0 auto}
nav{position:sticky;top:0;align-self:start;max-height:100vh;overflow-y:auto;padding:24px 12px 40px;
 border-right:1px solid var(--line)}
nav h3{font-size:.7rem;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);
 margin:18px 0 6px;padding-left:8px}
nav a{display:block;padding:3px 8px;border-radius:6px;color:var(--ink);text-decoration:none;
 font-size:.83rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
nav a:hover{background:var(--chip)}
nav a .v{font:600 .65rem/1 ui-monospace,monospace;margin-right:6px;opacity:.75}
main{padding:32px 28px 100px;min-width:0}
h1{font-size:1.9rem;margin:0 0 4px;letter-spacing:-.02em}
h2{font-size:1.25rem;margin:52px 0 2px;letter-spacing:-.01em;scroll-margin-top:16px}
h4{font-size:.72rem;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);margin:18px 0 6px}
.sub{color:var(--muted);margin:0 0 8px}
a{color:var(--accent)}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.87em;
 background:var(--code-bg);padding:1px 5px;border-radius:4px}
pre.blk{background:var(--code-bg);border:1px solid var(--line);border-radius:8px;padding:10px 12px;
 overflow-x:auto;font:.8rem/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;margin:6px 0}
.ep{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:0;
 margin:10px 0;scroll-margin-top:64px;overflow:hidden}
.ep>summary{list-style:none;cursor:pointer;padding:12px 16px;user-select:none}
.ep>summary::-webkit-details-marker{display:none}
.ep>summary::before{content:"▸";color:var(--muted);font-size:.8rem;margin-right:2px;
 transition:transform .12s ease;display:inline-block}
.ep[open]>summary::before{transform:rotate(90deg)}
.ep>summary:hover{background:var(--chip)}
.ep[open]>summary{border-bottom:1px solid var(--line)}
.epbody{padding:4px 18px 18px}
div.ep{padding:16px 18px}   /* the static panels (glance, conventions) aren't collapsible */
.peek{color:var(--muted);font-size:.83rem;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;
 white-space:nowrap}
.ep[open] .peek{display:none}
.ep[open] .summary{margin-top:10px}
.ep.hit{outline:2px solid var(--accent);outline-offset:-1px}
.bulk{display:flex;gap:6px}
.bulk button{background:none;border:1px solid var(--line);color:var(--muted);border-radius:6px;
 padding:4px 9px;font-size:.72rem;cursor:pointer}
.hd{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.verb{font:700 .7rem/1 ui-monospace,monospace;padding:5px 8px;border-radius:6px;color:#fff}
.v-get{background:var(--get)}.v-post{background:var(--post)}.v-put{background:var(--put)}
.v-delete{background:var(--del)}
.path{font-size:.95rem;background:none;padding:0}
.tier{font:700 .62rem/1 ui-monospace,monospace;letter-spacing:.08em;padding:4px 7px;border-radius:5px;
 border:1px solid currentColor;cursor:help}
.t-core{color:var(--post)}.t-edge{color:var(--put)}.t-ui{color:var(--get)}.t-ops{color:var(--c5)}
.tfilter{display:flex;gap:5px}
.tfilter button{background:none;border:1px solid var(--line);color:var(--muted);border-radius:6px;
 padding:4px 10px;font:600 .7rem/1 ui-monospace,monospace;letter-spacing:.06em;cursor:pointer}
.tfilter button.on{border-color:currentColor}
.tfilter button[data-t=core].on{color:var(--post)}.tfilter button[data-t=edge].on{color:var(--put)}
.tfilter button[data-t=ui].on{color:var(--get)}.tfilter button[data-t=ops].on{color:var(--c5)}
.tfilter button[data-t=all].on{color:var(--ink)}
.glance td .tier{margin-right:8px}
.anchor{margin-left:auto;color:var(--muted);text-decoration:none;opacity:.4}
.anchor:hover{opacity:1}
.summary{margin:8px 0 10px}
.chips{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-bottom:4px}
.chips .lbl{font-size:.68rem;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);
 margin-right:2px}
.chip{font-size:.74rem;padding:2px 9px;border-radius:20px;background:var(--chip);color:var(--muted);
 cursor:help}
.chip.auth{color:var(--del)}
table.params{border-collapse:collapse;width:100%;font-size:.86rem}
table.params td{padding:5px 8px;border-bottom:1px solid var(--line);vertical-align:top}
table.params td.k{color:var(--muted);font-size:.75rem;width:56px}
.tabs{display:flex;gap:4px;flex-wrap:wrap;margin-bottom:4px}
.btab{background:none;border:1px solid var(--line);color:var(--muted);border-radius:6px;
 padding:3px 10px;font-size:.78rem;cursor:pointer}
.btab.on{border-color:var(--accent);color:var(--accent)}
.bpane{display:none}.bpane.on{display:block}
.resp{display:flex;gap:10px;align-items:flex-start;margin:8px 0}
.rbody{flex:1;min-width:0}
.code{font:700 .75rem/1 ui-monospace,monospace;padding:6px 8px;border-radius:6px;color:#fff;
 margin-top:8px}
.c2{background:var(--c2)}.c3{background:var(--c3)}.c4{background:var(--c4)}.c5{background:var(--c5)}
.note{color:var(--muted);font-size:.86rem;margin:4px 0 0}
.callout{border-left:3px solid var(--accent);padding:6px 0 6px 14px;margin:14px 0;
 color:var(--muted);font-size:.9rem}
details.try{margin-top:14px;border-top:1px solid var(--line);padding-top:10px}
details.try summary{cursor:pointer;font-size:.82rem;color:var(--accent);font-weight:600}
.tform{margin-top:10px}
.tparams{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:8px}
.tparams label{font-size:.75rem;color:var(--muted);display:flex;align-items:center;gap:5px}
input,textarea,.base{background:var(--code-bg);border:1px solid var(--line);color:var(--ink);
 border-radius:6px;padding:5px 8px;font:.8rem/1.4 ui-monospace,SFMono-Regular,Menlo,monospace}
textarea{width:100%;resize:vertical}
.tbtns{display:flex;gap:8px;align-items:center;margin:8px 0}
button.send,button.curl{background:var(--accent);color:#fff;border:0;border-radius:6px;
 padding:6px 14px;font-size:.8rem;cursor:pointer;font-weight:600}
button.curl{background:none;color:var(--muted);border:1px solid var(--line);font-weight:400}
.tstat{font:.78rem/1 ui-monospace,monospace;color:var(--muted)}
.tout{display:none;max-height:340px}
.tout.on{display:block}
.topbar{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line);
 padding:10px 28px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.topbar input{flex:1;min-width:180px}
.toggle{background:var(--panel);border:1px solid var(--line);color:var(--muted);border-radius:8px;
 padding:5px 10px;cursor:pointer}
.warn{border-left:3px solid var(--del);padding:6px 0 6px 14px;margin:14px 0}
.foot{margin-top:70px;padding-top:16px;border-top:1px solid var(--line);color:var(--muted);
 font-size:.85rem}
@media(max-width:820px){.layout{grid-template-columns:1fr}nav{display:none}main{padding:20px 16px 80px}}
"""

JS = r"""
const r=document.documentElement,K='cuga-api-theme';
const saved=localStorage.getItem(K); if(saved) r.dataset.theme=saved;
document.querySelector('.toggle').onclick=()=>{
  const cur=r.dataset.theme||(matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light');
  const nxt=cur==='dark'?'light':'dark'; r.dataset.theme=nxt; localStorage.setItem(K,nxt);};

const BK='cuga-api-base', TK='cuga-api-token';
const base=document.getElementById('base'), tok=document.getElementById('token');
base.value=localStorage.getItem(BK)||'http://localhost:7860';
tok.value=localStorage.getItem(TK)||'';
base.oninput=()=>localStorage.setItem(BK,base.value.trim());
tok.oninput=()=>localStorage.setItem(TK,tok.value.trim());

// file:// pages send `Origin: null`. The server echoes `Access-Control-Allow-Origin: null`, which
// some browsers honour and others refuse — so Try-it may or may not work here. Flag it up front
// rather than letting a Send fail with an opaque "Failed to fetch".
if(location.protocol==='file:'){
  document.getElementById('filewarn').style.display='block';
}

document.querySelectorAll('.btab').forEach(b=>b.onclick=()=>{
  const ep=b.closest('.ep'), i=b.dataset.i;
  ep.querySelectorAll('.btab').forEach(x=>x.classList.toggle('on',x===b));
  ep.querySelectorAll('.bpane').forEach(p=>p.classList.toggle('on',p.dataset.i===i));
  const ta=ep.querySelector('.tbody');
  if(ta) ta.value=ep.querySelector('.bpane.on pre').textContent;
});

function buildURL(ep){
  let p=ep.dataset.path;
  ep.querySelectorAll('.pp').forEach(i=>{p=p.replace('{'+i.dataset.name+'}',encodeURIComponent(i.value))});
  const qs=[...ep.querySelectorAll('.qp')].filter(i=>i.value.trim())
    .map(i=>encodeURIComponent(i.dataset.name)+'='+encodeURIComponent(i.value.trim()));
  return base.value.trim().replace(/\/$/,'')+p+(qs.length?'?'+qs.join('&'):'');
}
function headersFor(ep){
  const h={};
  const ta=ep.querySelector('.tbody');
  if(ta) h['content-type']='application/json';
  if(tok.value.trim() && ep.dataset.auth==='gateway') h['X-Gateway-Token']=tok.value.trim();
  return h;
}

document.querySelectorAll('.send').forEach(btn=>btn.onclick=async()=>{
  const ep=btn.closest('.ep'), out=ep.querySelector('.tout'), stat=ep.querySelector('.tstat');
  const ta=ep.querySelector('.tbody');
  let body=null;
  if(ta){
    try{ body=JSON.stringify(JSON.parse(ta.value||'{}')); }
    catch(e){ out.classList.add('on'); out.textContent='Body is not valid JSON: '+e.message; return; }
  }
  stat.textContent='…'; out.classList.add('on'); out.textContent='';
  const t0=performance.now();
  try{
    const res=await fetch(buildURL(ep),{method:ep.dataset.verb,headers:headersFor(ep),body});
    const ms=Math.round(performance.now()-t0);
    const txt=await res.text();
    let pretty=txt;
    try{ pretty=JSON.stringify(JSON.parse(txt),null,2); }catch(_){}
    stat.textContent=res.status+' '+res.statusText+' · '+ms+'ms';
    out.textContent=pretty||'(empty body)';
  }catch(err){
    stat.textContent='failed';
    out.textContent=err.message+'\n\nIs the server up (make status)? If this page is on file://, '+
      'CORS will block it — serve it with: make api-spec';
  }
});

document.querySelectorAll('.curl').forEach(btn=>btn.onclick=()=>{
  const ep=btn.closest('.ep'), ta=ep.querySelector('.tbody');
  let c="curl -X "+ep.dataset.verb+" '"+buildURL(ep)+"'";
  const h=headersFor(ep);
  for(const k in h) c+=" \\\n  -H '"+k+": "+(k==='X-Gateway-Token'?'$GATEWAY_TOKEN':h[k])+"'";
  if(ta) c+=" \\\n  -d '"+ta.value.replace(/\n\s*/g,'')+"'";
  navigator.clipboard.writeText(c);
  btn.textContent='copied'; setTimeout(()=>btn.textContent='Copy as curl',1200);
});

const q=document.getElementById('q');
let tierFilter='all';
function applyFilters(){
  const s=q.value.trim().toLowerCase();
  document.querySelectorAll('.ep').forEach(ep=>{
    const okT=(tierFilter==='all'||ep.dataset.tier===tierFilter);
    const okS=(!s||ep.dataset.search.includes(s));
    ep.style.display=(okT&&okS)?'':'none';
  });
  document.querySelectorAll('main section').forEach(sec=>{
    const any=[...sec.querySelectorAll('.ep')].some(ep=>ep.style.display!=='none');
    sec.style.display=any?'':'none';
  });
  document.querySelectorAll('nav a').forEach(a=>{
    // The href is already a '#id' selector. This used to call .replace('#','#') — replacing '#'
    // with itself, a no-op that read as if it were sanitising something (CodeQL flags it as
    // "replacement of a substring with itself"). Use the href directly and say so.
    const ep=document.querySelector(a.getAttribute('href'));
    if(ep) a.style.display=ep.style.display==='none'?'none':'';
  });
}
q.oninput=()=>{
  applyFilters();
  // A search that narrows to a handful of endpoints should show them, not make you click each one.
  const vis=[...document.querySelectorAll('.ep[data-tier]')].filter(x=>x.style.display!=='none');
  if(q.value.trim() && vis.length<=4) vis.forEach(x=>x.open=true);
};
document.querySelectorAll('.tfilter button').forEach(b=>b.onclick=()=>{
  tierFilter=b.dataset.t;
  document.querySelectorAll('.tfilter button').forEach(x=>x.classList.toggle('on',x===b));
  applyFilters();
});

const eps=()=>document.querySelectorAll('.ep[data-tier]');
document.getElementById('expand').onclick=()=>eps().forEach(x=>{if(x.style.display!=='none')x.open=true});
document.getElementById('collapse').onclick=()=>eps().forEach(x=>x.open=false);

// A deep link (#post-invoke) must OPEN the card, not scroll to a closed one. Runs on load and on
// every subsequent hash change, including clicks on the sidebar and the '#' anchors.
function openHash(){
  const id=decodeURIComponent(location.hash.slice(1));
  if(!id) return;
  const el=document.getElementById(id);
  if(!el||!el.dataset.tier) return;
  el.open=true;
  el.scrollIntoView({block:'start'});
  el.classList.add('hit'); setTimeout(()=>el.classList.remove('hit'),1400);
}
addEventListener('hashchange',openHash);
openHash();

// The '#' anchor lives inside the <summary>, so a click would ALSO toggle the card shut. Let it
// update the hash (openHash reopens it) without the summary seeing the click.
document.querySelectorAll('.anchor').forEach(a=>a.onclick=ev=>{ev.stopPropagation()});
"""


def render() -> str:
    nav, body = [], []
    for gid, gtitle, gblurb in GROUPS:
        eps = [x for x in ENDPOINTS if x["group"] == gid]
        if not eps:
            continue
        nav.append(f"<h3>{gtitle}</h3>")
        nav += [
            f'<a href="#{slug(x)}"><span class="v v-{x["verb"].lower()}">{x["verb"]}</span>'
            f'{e(x["path"].replace("/api/events", ""))}</a>'
            for x in eps
        ]
        body.append(
            f'<section id="g-{gid}"><h2>{gtitle}</h2><p class="sub">{gblurb}</p>'
            + "".join(render_endpoint(x) for x in eps)
            + "</section>"
        )

    actors = "".join(
        f'<tr><td><span class="chip who">{e(n)}</span></td><td>{e(d)}</td></tr>' for n, d in ACTORS.values()
    )
    n = len(ENDPOINTS)

    counts = {t: sum(1 for x in ENDPOINTS if x["tier"] == t) for t in TIERS}
    tfilter = (
        '<div class="tfilter"><button data-t="all" class="on">ALL</button>'
        + "".join(f'<button data-t="{t}">{lab} {counts[t]}</button>' for t, (lab, _, _) in TIERS.items())
        + "</div>"
    )
    glance = "".join(
        f'<tr><td style="white-space:nowrap"><span class="tier t-{t}">{lab}</span>'
        f'<b>{counts[t]}</b></td><td>{helptext}<div class="note">'
        + " · ".join(f'<code>{e(x["verb"])} {e(x["path"])}</code>' for x in ENDPOINTS if x["tier"] == t)
        + "</div></td></tr>"
        for t, (lab, _, helptext) in TIERS.items()
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Event-Driven CUGA — API Spec</title>
<style>{CSS}</style></head>
<body>
<div class="topbar">
  <strong style="font-size:.9rem">API spec</strong>
  <input id="q" placeholder="filter endpoints…" style="max-width:200px">
  {tfilter}
  <div class="bulk"><button id="expand">expand all</button><button id="collapse">collapse all</button></div>
  <label style="font-size:.75rem;color:var(--muted)">base
    <input id="base" class="base" size="22"></label>
  <label style="font-size:.75rem;color:var(--muted)">GATEWAY_TOKEN
    <input id="token" class="base" size="14" type="password"></label>
  <button class="toggle" title="light / dark">◐</button>
</div>
<div class="layout">
<nav>{''.join(nav)}</nav>
<main>
  <h1>Event-Driven CUGA — API Spec</h1>
  <p class="sub">{n} endpoints. Every one has example payloads, real responses (including the
  interesting failures), and the callers that use it in practice. Set a <b>base URL</b> above and hit
  <b>Try it</b> on any endpoint to run it against your own stack.</p>

  <div id="filewarn" class="warn" style="display:none">
    <b>This page is open from <code>file://</code>.</b> The browser sends <code>Origin: null</code>;
    the server echoes <code>Access-Control-Allow-Origin: null</code>, which some browsers honour and
    others refuse — so <b>Try it</b> may fail here with an opaque <i>Failed to fetch</i>. For a
    reliable round trip serve the page over HTTP: <code>make api-spec</code>. <b>Copy as curl</b>
    works either way.
  </div>

  <div class="callout">
    <b>The shape of the system.</b> <code>/invoke</code> is the one seam everything converges on: every
    armed Activepieces flow, every chat channel, the Box poller and the generic webhook all normalise
    their event into an <i>envelope</i> and POST it here. <code>/api/concierge</code> is the
    natural-language front door that turns a sentence into a standing flow. Everything under
    <code>/api/events/</code> exists so the Studio can stay dumb — it renders what the backend reports
    rather than deciding anything itself.
  </div>

  <h2 id="glance">Which endpoints actually matter to you</h2>
  <p class="sub">Not every endpoint is load-bearing. Two do the reasoning; four receive events from
  the outside world; twenty-five exist so the Studio can render a page. Use the tier buttons in the
  bar above to see one group at a time.</p>
  <div class="ep">
    <table class="params glance"><tbody>{glance}</tbody></table>
    <div class="callout">The <b>UI</b> column is not a judgement call — it was derived by grepping the
    shipped Studio bundle (<code>src/cuga/frontend/dist</code>) for every path. The console calls 19 of
    the 33 and never touches <code>/invoke</code>, the inbound receivers, the flows console, or channel
    arming. If you are integrating another system with CUGA, you can ignore everything but
    <b>CORE</b>, and possibly one <b>EDGE</b> receiver.</div>
  </div>

  <h2 id="conventions">Conventions</h2>
  <p class="sub">Things that are true of every endpoint below.</p>
  <div class="ep">
    <h4>Who calls these</h4>
    <table class="params"><tbody>{actors}</tbody></table>
    <h4>Isolation</h4>
    <p class="summary">Every endpoint resolves a <b>principal</b>
    (<code>tenant/instance/user</code>) from <code>X-Tenant-Id</code> / <code>X-Instance-Id</code> /
    <code>X-User-Id</code> headers, falling back to <code>default/default/local</code>. You only ever
    see your own subscriptions, runs and connections. Another principal's id returns
    <b><code>404</code>, never <code>403</code></b> — a 403 would confirm the id exists.</p>
    <h4>Failure, not silence</h4>
    <p class="summary">A missing optional dependency is a <code>501</code> naming it, not a
    <code>500</code>. An unreachable Activepieces degrades reads to <code>unknown</code>/empty rather
    than erroring. Anything that could silently do nothing — a stale Box token, a dangling flow —
    is made loud instead.</p>
    <h4>Auth, honestly</h4>
    <p class="summary">Three seams are protected only when their secret is set:
    <code>GATEWAY_TOKEN</code> for <code>/invoke</code> and the Box poll,
    <code>SLACK_SIGNING_SECRET</code> for the Slack receiver, and <code>EVENTS_WEBHOOK_KEY</code> for
    the generic hook. <b>Unset, each accepts anything that reaches it.</b> That is fine on localhost
    and dangerous behind a public tunnel.</p>
  </div>

  {''.join(body)}

  <div class="foot">
    Generated by <code>scripts/gen_api_spec.py</code> from the endpoint table in that file, checked
    against the routes registered in <code>src/cuga/backend/events/app.py</code>.
    <b>Do not hand-edit this page.</b> Add a route without describing it and
    <code>test_api_spec_is_golden</code> fails. Regenerate with
    <code>python scripts/gen_api_spec.py</code>.
  </div>
</main>
</div>
<script>{JS}</script>
</body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--check", action="store_true", help="exit 1 if the spec is stale or a route is undocumented"
    )
    a = ap.parse_args()

    problems = check_coverage()
    if problems:
        print("route coverage FAILED:", file=sys.stderr)
        for p in problems:
            print("  " + p, file=sys.stderr)
        print("\n  → describe it in ENDPOINTS in scripts/gen_api_spec.py", file=sys.stderr)
        return 1

    fresh = render()
    if a.check:
        current = OUT.read_text() if OUT.exists() else ""
        if current != fresh:
            print(f"{OUT.relative_to(REPO)} is STALE — run: python scripts/gen_api_spec.py", file=sys.stderr)
            return 1
        print(f"✓ {OUT.relative_to(REPO)} is in sync ({len(ENDPOINTS)} endpoints)")
        return 0

    OUT.write_text(fresh)
    print(
        f"✓ wrote {OUT.relative_to(REPO)} — {len(ENDPOINTS)} endpoints, "
        f"{sum(len(x['body']) for x in ENDPOINTS)} example payloads, "
        f"{sum(len(x['responses']) for x in ENDPOINTS)} documented responses"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
