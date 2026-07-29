#!/usr/bin/env python3
"""Generate the architecture SVGs (+ PNGs) — a system overview and one sequence diagram per step.

    python events_docs/architecture/gen_diagrams.py

Everything is hand-derived from the code under src/cuga/backend/events/, so the diagrams are a
statement about the system as it actually is, not a whiteboard sketch. Edit the SCENARIOS / SYSTEM
data here and regenerate — never hand-edit the SVGs.

The architecture has **two pieces**, and the diagrams are grouped by them:

  CREATE (arm-time)  — set up a credential, then turn an English sentence into a standing flow.
  FIRE   (run-time)  — a trigger fires, /invoke runs the agent, the answer is delivered.

Two threads run through both, and the diagrams make them explicit:

  * `/invoke` is the single seam. Every fire path normalises its event into one envelope and POSTs it.
  * Credentials never touch the agent. Three models:
      1. OAuth via AP   (gmail/github/box-AP) — consent → AP exchanges the code → AP stores an OAUTH2
                          connection. A flow references it as `{{connections['ea::…::app']}}`; AP
                          resolves the token inside its own sandbox at fire time.
      2. Token via AP   (telegram/discord-AP) — paste a secret → AP stores SECRET_TEXT. Same reference.
      3. Direct env     (box-direct/slack/discord) — the token lives in CUGA's .env; CUGA's own adapter
                          uses it. Still never handed to the agent.
"""
from __future__ import annotations

import pathlib

OUT = pathlib.Path(__file__).resolve().parent

# palette — colour by role, consistent across every diagram
C = {
    "you": "#6b6866", "ext": "#8a8785", "cuga": "#2b5fd9", "ap": "#b06f10",
    "agent": "#1a7f4b", "ink": "#1c1b1a", "line": "#c9c6c1", "bg": "#ffffff",
    "note": "#7a3fa8", "cred": "#c0392b", "faint": "#f4f3f1", "credbg": "#fdecea",
}


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ── sequence-diagram engine ───────────────────────────────────────────────────
# A scenario is a list of actors (id,label,colorkey) and messages. Each message:
#   (from, to, text, style)   style ∈ solid | dashed | self | note | cred
# `cred` is a note band, but red — a credential fact worth stopping on.
def seq_svg(title: str, subtitle: str, actors: list, messages: list) -> str:
    LM, TOP = 30, 96
    col_w = 178
    head_h = 46
    row_h = 52
    xs = {a[0]: LM + col_w // 2 + i * col_w for i, a in enumerate(actors)}
    # a self-loop on the RIGHTMOST lifeline draws its label to the right — reserve room so it can't
    # clip off the canvas edge.
    last_id = actors[-1][0]
    self_pad = max((len(t) for f, tt, t, s in messages if s == "self" and f == last_id), default=0)
    width = LM * 2 + col_w * len(actors) + (min(self_pad, 42) * 7 + 50 if self_pad else 0)
    body_top = TOP + head_h + 18
    height = body_top + row_h * len(messages) + 40

    p = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
         f'viewBox="0 0 {width} {height}" font-family="ui-sans-serif,-apple-system,Segoe UI,Roboto,'
         f'Helvetica,Arial,sans-serif">',
         f'<rect width="{width}" height="{height}" fill="{C["bg"]}"/>',
         f'<text x="{LM}" y="34" font-size="20" font-weight="700" fill="{C["ink"]}">{esc(title)}</text>',
         f'<text x="{LM}" y="58" font-size="13" fill="{C["ext"]}">{esc(subtitle)}</text>',
         '<defs><marker id="arw" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto">'
         f'<path d="M0,0 L8,3 L0,6 Z" fill="{C["ink"]}"/></marker>'
         '<marker id="arwo" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto">'
         f'<path d="M0,0 L8,3 L0,6" fill="none" stroke="{C["ink"]}" stroke-width="1.2"/></marker></defs>']

    for a in actors:
        x = xs[a[0]]
        col = C[a[2]]
        p.append(f'<line x1="{x}" y1="{TOP + head_h}" x2="{x}" y2="{height - 24}" '
                 f'stroke="{C["line"]}" stroke-width="1.4"/>')
        p.append(f'<rect x="{x - col_w // 2 + 8}" y="{TOP}" width="{col_w - 16}" height="{head_h}" '
                 f'rx="9" fill="{col}"/>')
        lines = a[1].split("\n")
        ly = TOP + (26 if len(lines) == 2 else 29)
        for ln in lines:
            p.append(f'<text x="{x}" y="{ly}" font-size="12.5" font-weight="600" fill="#fff" '
                     f'text-anchor="middle">{esc(ln)}</text>')
            ly += 15

    y = body_top
    for m in messages:
        frm, to, text, style = m
        if style in ("note", "cred"):
            band = C["credbg"] if style == "cred" else C["faint"]
            edge = C["cred"] if style == "cred" else C["note"]
            pre = "🔑 " if style == "cred" else ""
            p.append(f'<rect x="{LM}" y="{y - 16}" width="{width - LM * 2}" height="30" rx="6" '
                     f'fill="{band}" stroke="{edge}" stroke-opacity="0.55"/>')
            p.append(f'<text x="{width // 2}" y="{y + 4}" font-size="12" fill="{edge}" '
                     f'text-anchor="middle" font-style="italic">{esc(pre + text)}</text>')
            y += row_h - 8
            continue
        x1, x2 = xs[frm], xs[to]
        dash = ' stroke-dasharray="5 4"' if style == "dashed" else ""
        marker = "arwo" if style == "dashed" else "arw"
        if style == "self" or frm == to:
            p.append(f'<path d="M{x1},{y - 6} h34 v20 h-34" fill="none" stroke="{C["ink"]}" '
                     f'stroke-width="1.4" marker-end="url(#arw)"/>')
            p.append(f'<text x="{x1 + 40}" y="{y}" font-size="12" fill="{C["ink"]}">{esc(text)}</text>')
            y += row_h
            continue
        mid = (x1 + x2) / 2
        p.append(f'<text x="{mid}" y="{y - 8}" font-size="12" fill="{C["ink"]}" '
                 f'text-anchor="middle">{esc(text)}</text>')
        p.append(f'<line x1="{x1}" y1="{y}" x2="{x2 + (7 if x2 < x1 else -7)}" y2="{y}" '
                 f'stroke="{C["ink"]}" stroke-width="1.4"{dash} marker-end="url(#{marker})"/>')
        y += row_h
    p.append("</svg>")
    return "\n".join(p)


def act(aid, label, col):
    return (aid, label, col)


N = (None, None)   # placeholder for note/cred rows


# ══ CREATE (arm-time) ═════════════════════════════════════════════════════════
# ══ FIRE   (run-time) ═════════════════════════════════════════════════════════
SCENARIOS = {

# ── CREATE 1 — set up a credential ────────────────────────────────────────────
"create-1-connect-credential": dict(
    title="CREATE ① — connect a credential",
    subtitle="OAuth (gmail/github/box). CUGA hands AP the authorization CODE; AP owns the token forever.",
    actors=[act("you", "You", "you"), act("br", "Browser", "ext"),
            act("cuga", "CUGA", "cuga"), act("prov", "Provider\n(Google/GitHub)", "ext"),
            act("ap", "Activepieces", "ap")],
    messages=[
        ("you", "cuga", "Studio → Connect  (GET /api/events/connect/github)", "solid"),
        ("cuga", "br", "302 → provider consent (client_id, scopes, redirect_uri)", "solid"),
        ("br", "prov", "you approve  (scopes: repo, admin:repo_hook)", "solid"),
        ("prov", "cuga", "redirect → /connect/github/callback?code=…", "dashed"),
        (*N, "CUGA receives an authorization CODE, never a token", "cred"),
        ("cuga", "ap", "ensure_oauth_connection(code, client_id/secret, redirect)", "solid"),
        ("ap", "prov", "exchange code → access + refresh token", "solid"),
        ("ap", "ap", "store OAUTH2 connection  ea::tenant::user::github", "self"),
        (*N, "the token lives in AP only — CUGA and the agent never see it", "cred"),
        (*N, "token apps (telegram/discord): paste a secret → AP SECRET_TEXT connection", "note"),
    ]),

# ── CREATE 2 — arm a flow ─────────────────────────────────────────────────────
"create-2-arm-flow": dict(
    title="CREATE ② — arm a flow from a sentence",
    subtitle="The concierge builds the flow and bakes in a REFERENCE to the connection, not the token.",
    actors=[act("you", "You", "you"), act("con", "Concierge\n(NL→flow)", "cuga"),
            act("ap", "Activepieces", "ap"), act("ext", "GitHub", "ext")],
    messages=[
        ("you", "con", 'POST /api/concierge  "watch repo X for new PRs, summarize"', "solid"),
        ("con", "con", "classify → PUSH · target = 'cuga' (the ONE agent) · resolve connection id", "self"),
        ("con", "ap", "build flow: trigger.auth = {{connections['ea::…::github']}}", "solid"),
        (*N, "the flow stores a REFERENCE to the connection — never the secret", "cred"),
        ("ap", "ext", "register repo webhook (AP resolves the token to do this)", "solid"),
        ("ap", "ap", "LOCK_AND_PUBLISH → ENABLED", "self"),
        ("ap", "con", "ap_flow_id", "dashed"),
        ("con", "you", "reply + (?flow=1) the built flow: trigger + steps", "dashed"),
    ]),

# ── CREATE 3 — NL→Flow resolution (the pre-router in front of the LLM) ────────
"create-3-nl-to-flow": dict(
    title="CREATE ②a — NL→Flow: fill the blanks, or ask till legit",
    subtitle="A deterministic FlowSpec resolver fronts the LLM. Both doors arm through the SAME "
             "tool + registry gate — a sentence becomes the right flow or a question, never the wrong flow.",
    actors=[act("you", "You", "you"), act("pre", "Pre-router\n(flowspec.py)", "cuga"),
            act("llm", "Concierge LLM\n(ReAct)", "cuga"),
            act("gate", "Registry gate\n(triggers.py)", "cuga"), act("ap", "Activepieces", "ap")],
    messages=[
        ("you", "pre", '"when a PR is opened, review it"', "solid"),
        ("pre", "pre", "classify kind=PUSH · match trigger phrases · extract slots", "self"),
        (*N, "HIGH confidence needs: a standing-flow marker + ONE distinct app + no foreign vocabulary", "note"),
        ("pre", "gate", "validate(github, new_pr, {})", "solid"),
        ("gate", "pre", "missing required slot: repo", "dashed"),
        ("pre", "you", '"Which repository (owner/repo) should I watch?"  — spec PARKED on this thread', "dashed"),
        ("you", "pre", '"acme/api"   (the next message fills the blank)', "solid"),
        ("pre", "gate", "validate(github, new_pr, {repo: acme/api}) → armable", "solid"),
        ("pre", "ap", "find_or_create_flow — the SAME tool the LLM calls (connect gate · dedup · build)", "solid"),
        (*N, "AMBIGUOUS (several apps hit / no marker / NOW question) → falls to the LLM, which sees", "note"),
        (*N, "the full trigger vocabulary and calls the same tool; the gate disposes of every proposal", "note"),
        (*N, "a topic-change reply is never crammed into the slot — the parked spec is dropped", "note"),
        (*N, "benchmarked: 47 labelled cases in CI, gated on ZERO wrong-at-high (test_flowspec_bench.py)", "note"),
    ]),

# ── FIRE 1 — NOW ──────────────────────────────────────────────────────────────
"fire-1-now": dict(
    title="FIRE ① — NOW (no flow, no AP)",
    subtitle="A one-shot question. The answer IS the HTTP response. No integration credential involved.",
    actors=[act("you", "You", "you"), act("cuga", "CUGA\n/invoke", "cuga"),
            act("ag", "cuga → pricebot\n(supervisor picks)", "agent"), act("mcp", "MCP tool\nserver", "agent")],
    messages=[
        ("you", "cuga", "POST /invoke {agent, text, source:time/runonce}", "solid"),
        ("cuga", "ag", "runtime.run(agent, worker_input)", "solid"),
        ("ag", "mcp", "call get_crypto_price(...)", "solid"),
        (*N, "the tool authenticates at the MCP server (its own key) — not via AP", "cred"),
        ("mcp", "ag", "result", "dashed"),
        ("ag", "cuga", "answer + meta.mcp (tools that actually ran)", "dashed"),
        ("cuga", "you", "200 {answer, meta:{mcp,tools,ms}}", "dashed"),
    ]),

# ── FIRE 2 — CRON / POLL ──────────────────────────────────────────────────────
"fire-2-schedule": dict(
    title="FIRE ② — CRON / POLL (a scheduled flow)",
    subtitle="AP owns the trigger. The callback is podman-internal (HOST_CALLBACK_URL), not the tunnel.",
    actors=[act("ap", "Activepieces", "ap"), act("cuga", "CUGA\n/invoke", "cuga"),
            act("ag", "cuga\n(the one agent)", "agent"), act("sink", "Sink\n(channel)", "ext")],
    messages=[
        ("ap", "ap", "schedule tick (every N min / cron)", "self"),
        ("ap", "cuga", "POST /invoke {agent, source:time/tick}  (HOST_CALLBACK_URL)", "solid"),
        ("cuga", "ag", "runtime.run(...)", "solid"),
        ("ag", "cuga", "answer", "dashed"),
        ("cuga", "ap", "200 {answer}", "dashed"),
        ("ap", "sink", "AP send-step  {{step_1.body.answer}}", "solid"),
        (*N, "direct sink (Slack)? no send-step — CUGA delivers it itself", "note"),
    ]),

# ── FIRE 3 — PUSH · GitHub (webhook trigger, AP-held OAuth) ────────────────────
"fire-3-push-github": dict(
    title="FIRE ③ — PUSH · GitHub (webhook trigger)",
    subtitle="A repo event arrives via AP's tunnel. AP resolves the OAuth token in its sandbox; the agent gets content.",
    actors=[act("gh", "GitHub", "ext"), act("ap", "Activepieces", "ap"),
            act("cuga", "CUGA\n/invoke", "cuga"), act("ag", "cuga (supervisor)\n→ specialist", "agent")],
    messages=[
        ("gh", "ap", "PR opened → webhook POST (to AP's public tunnel)", "solid"),
        (*N, "AP resolves {{connections['ea::…::github']}} → the token, in its sandbox", "cred"),
        ("ap", "cuga", "POST /invoke {agent: cuga, source:github, payload: title/body/diff}", "solid"),
        (*N, "the agent receives PR CONTENT — never the credential", "cred"),
        ("cuga", "ag", "the supervisor picks the specialist (per wake-up) · sub-agent runs", "solid"),
        ("ag", "cuga", "summary", "dashed"),
        ("cuga", "ap", "200 → AP send-step delivers", "dashed"),
    ]),

# ── FIRE 4 — PUSH · Gmail (polling trigger, AP-held OAuth) ─────────────────────
"fire-4-push-gmail": dict(
    title="FIRE ④ — PUSH · Gmail (polling trigger)",
    subtitle="AP polls on its own clock, using the stored OAuth token. Same credential model as GitHub.",
    actors=[act("gm", "Gmail", "ext"), act("ap", "Activepieces", "ap"),
            act("cuga", "CUGA\n/invoke", "cuga"), act("ag", "cuga → mailbot\n(supervisor picks)", "agent")],
    messages=[
        (*N, "AP resolves {{connections['ea::…::gmail']}} → the token, in its sandbox", "cred"),
        ("ap", "gm", "poll: any new mail? (every ~1–2 min)", "solid"),
        ("gm", "ap", "new email {subject, from, body}", "dashed"),
        ("ap", "cuga", "POST /invoke {source:gmail, payload}", "solid"),
        ("cuga", "ag", "summarize the email (agent never sees the token)", "solid"),
        ("ag", "cuga", "summary → deliver", "dashed"),
        (*N, "polling trigger ⇒ /run can't fire it out of band (only a real email)", "note"),
    ]),

# ── FIRE 5 — Box direct (CUGA-held token + the download step) ──────────────────
"fire-5-box-direct": dict(
    title="FIRE ⑤ — PUSH · Box (direct poll + download)",
    subtitle="EVENTS_BOX_BACKEND=direct: no AP. CUGA holds the token in .env and fetches file CONTENT.",
    actors=[act("box", "Box", "ext"), act("cuga", "CUGA\n/invoke", "cuga"),
            act("ag", "cuga → resume_judge\n(supervisor picks)", "agent"), act("slack", "Slack", "ext")],
    messages=[
        (*N, "CUGA uses BOX_DEV_TOKEN from .env — the direct credential model, no AP", "cred"),
        ("cuga", "cuga", "POST /api/events/box/poll (scheduled/manual)", "self"),
        ("cuga", "box", "list folder since watermark, then download bytes", "solid"),
        ("box", "cuga", "file content", "dashed"),
        ("cuga", "cuga", "inline text / base64; attach the JD", "self"),
        ("cuga", "ag", "POST /invoke {text: prompt + resume, jd}", "solid"),
        ("ag", "slack", "MATCH/SKIP + reasoning (direct send, SLACK_BOT_TOKEN)", "solid"),
    ]),

# ── FIRE 6 — Channel · Slack (direct, signed) ─────────────────────────────────
"fire-6-channel-slack": dict(
    title="FIRE ⑥ — Channel · Slack (direct backend)",
    subtitle="No AP. Slack's Events API → CUGA, signature-verified → concierge → reply in-thread.",
    actors=[act("you", "You", "you"), act("sl", "Slack", "ext"),
            act("cuga", "CUGA", "cuga"), act("ag", "cuga\n(the one agent)", "agent")],
    messages=[
        ("you", "sl", "type a message in the channel", "solid"),
        ("sl", "cuga", "POST /api/events/slack/events  (signed)", "solid"),
        (*N, "CUGA verifies X-Slack-Signature with SLACK_SIGNING_SECRET; ack <3s", "cred"),
        ("cuga", "ag", "background: /invoke via the concierge", "solid"),
        ("ag", "cuga", "answer", "dashed"),
        ("cuga", "sl", "chat.postMessage in-thread  (SLACK_BOT_TOKEN)", "solid"),
    ]),

# ── FIRE 7 — Channel · Telegram (via AP) ──────────────────────────────────────
"fire-7-channel-telegram": dict(
    title="FIRE ⑦ — Channel · Telegram (Activepieces backend)",
    subtitle="Telegram always runs via AP: a polling trigger + an AP send-step, both using the bot-token connection.",
    actors=[act("you", "You", "you"), act("tg", "Telegram", "ext"),
            act("ap", "Activepieces", "ap"), act("cuga", "CUGA\n/invoke", "cuga")],
    messages=[
        ("you", "tg", "DM the bot", "solid"),
        ("ap", "tg", "poll new_message  (AP bot-token connection)", "solid"),
        ("tg", "ap", "message {chat_id, text}", "dashed"),
        ("ap", "cuga", "POST /invoke {source:channel, thread_id:gw:telegram:<id>}", "solid"),
        ("cuga", "ap", "200 {answer}", "dashed"),
        ("ap", "tg", "AP send-step → reply to chat", "solid"),
    ]),

# ── FIRE 8 — Generic webhook ──────────────────────────────────────────────────
"fire-8-webhook": dict(
    title="FIRE ⑧ — Generic webhook (any system → agent)",
    subtitle="POST /api/events/hook/<name>. No AP, no piece. ?key= guards it (unset = open).",
    actors=[act("src", "Monitoring\n/ CI / form", "ext"), act("cuga", "CUGA\n/invoke", "cuga"),
            act("ag", "cuga → incident_triage\n(supervisor picks)", "agent"), act("slack", "Slack", "ext")],
    messages=[
        ("src", "cuga", "POST /api/events/hook/alert?agent=…&key=…", "solid"),
        (*N, "CUGA checks ?key against EVENTS_WEBHOOK_KEY (unset ⇒ open to anyone)", "cred"),
        ("cuga", "ag", "/invoke {text: the JSON payload}", "solid"),
        ("ag", "cuga", "triage (P1/P2 …)", "dashed"),
        ("cuga", "src", "200 {answer}", "dashed"),
        ("cuga", "slack", "optional: ?deliver_to → direct send", "solid"),
    ]),

# ── FIRE 9 — the ACTION half · AP-push trigger ▸ agent ▸ action step ───────────
"fire-9-push-action": dict(
    title="FIRE ⑨ — the ACTION half (trigger ▸ agent ▸ ACTION)",
    subtitle="Not just notify — ACT. After the agent answers, a connector ACTION runs as a step in the SAME AP flow. Gmail is the live pilot.",
    actors=[act("gm", "Gmail", "ext"), act("ap", "Activepieces", "ap"),
            act("cuga", "CUGA\n/invoke", "cuga"), act("ag", "cuga → mailbot\n(supervisor picks)", "agent")],
    messages=[
        ("ap", "gm", "poll: new mail?  (AP-held OAuth)", "solid"),
        ("gm", "ap", "new email {subject, from, body}", "dashed"),
        ("ap", "cuga", "POST /invoke {source:gmail, payload}", "solid"),
        ("cuga", "ag", "draft a reply to this email", "solid"),
        ("ag", "cuga", "200 {answer: the reply text}", "dashed"),
        (*N, "the flow's NEXT step is a gmail ACTION — AP runs it with the SAME connection", "cred"),
        ("ap", "gm", "action step: reply_to_email {body:{{step_1.body.answer}}}", "solid"),
        (*N, "3 gates guard the arm: deterministic build · AP validity gate · LLM verifier", "note"),
    ]),

# ── FIRE 10 — direct trigger ▸ action via the executor (Option A) ──────────────
"fire-10-direct-action-executor": dict(
    title="FIRE ⑩ — direct trigger → ACTION (the executor · Option A)",
    subtitle="A direct trigger (slack/discord/telegram) owns no AP flow, so its Gmail action runs via a reusable EXECUTOR flow CUGA fires after the agent answers.",
    actors=[act("sl", "Slack", "ext"), act("cuga", "CUGA\n/invoke", "cuga"),
            act("ag", "cuga\n(agent)", "agent"), act("ap", "Activepieces\n(executor + creds)", "ap")],
    messages=[
        ("sl", "cuga", "message in #alerts → Events API (direct, no AP flow)", "solid"),
        ("cuga", "ag", "/invoke: summarize it", "solid"),
        ("ag", "cuga", "answer", "dashed"),
        (*N, "no AP flow to hold an action step — so CUGA fires a reusable executor flow", "note"),
        ("cuga", "ap", "POST executor webhook  exec-gmail-send_email {body:answer, to}", "solid"),
        (*N, "AP resolves {{connections['…gmail']}} in its sandbox — agent still holds no token", "cred"),
        ("ap", "cuga", "runs gmail/send_email  (arm+validity live-verified; run-level fix pending)", "dashed"),
    ]),
}


# ── system overview (hand-built) ──────────────────────────────────────────────
def system_svg() -> str:
    W, H = 1160, 760
    def box(x, y, w, h, fill, title, lines, tcol="#fff", r=12):
        s = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}"/>',
             f'<text x="{x + 14}" y="{y + 25}" font-size="14.5" font-weight="700" fill="{tcol}">{esc(title)}</text>']
        yy = y + 44
        for ln in lines:
            s.append(f'<text x="{x + 14}" y="{yy}" font-size="11.5" fill="{tcol}" '
                     f'fill-opacity="0.92">{esc(ln)}</text>')
            yy += 17
        return "\n".join(s)

    def band(x, y, w, h, label, col):
        return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="none" '
                f'stroke="{col}" stroke-width="1.6" stroke-dasharray="7 5"/>'
                f'<text x="{x + 14}" y="{y + 22}" font-size="13" font-weight="700" '
                f'fill="{col}" letter-spacing="0.5">{esc(label)}</text>')

    def arrow(x1, y1, x2, y2, label="", dash=False, col=C["ink"], lx=None, ly=None):
        d = ' stroke-dasharray="5 4"' if dash else ""
        s = [f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{col}" stroke-width="1.6"{d} '
             f'marker-end="url(#a)"/>']
        if label:
            s.append(f'<text x="{lx or (x1 + x2) // 2}" y="{ly or (y1 + y2) // 2 - 6}" '
                     f'font-size="11" fill="{col}" text-anchor="middle">{esc(label)}</text>')
        return "\n".join(s)

    p = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
         f'font-family="ui-sans-serif,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">',
         f'<rect width="{W}" height="{H}" fill="{C["bg"]}"/>',
         '<defs><marker id="a" markerWidth="11" markerHeight="11" refX="8" refY="3.5" orient="auto">'
         f'<path d="M0,0 L9,3.5 L0,7 Z" fill="{C["ink"]}"/></marker></defs>',
         f'<text x="40" y="40" font-size="23" font-weight="700" fill="{C["ink"]}">'
         'CUGA event-driven agents — the two pieces</text>',
         f'<text x="40" y="63" font-size="13" fill="{C["ext"]}">'
         'CREATE a flow (connect a credential, then arm it) · FIRE a flow (a trigger runs the agent, '
         'which can then ACT). /invoke is the single seam; AP owns the tokens.</text>']

    # CREATE band (top)
    p.append(band(30, 84, W - 60, 168, "CREATE  (arm-time)", C["cuga"]))
    p.append(box(56, 120, 250, 108, C["ext"], "You / Admin",
                 ["1. Connect an integration", "   (OAuth consent, or paste a token)",
                  "2. Say what to automate", '   "watch my repo for PRs…"']))
    p.append(box(430, 120, 250, 108, C["cuga"], "Concierge (NL→flow)",
                 ["POST /api/concierge", "classify → build flow (targets cuga)",
                  "bakes in {{connections[…]}} —", "a reference, never the token"]))
    p.append(box(804, 120, 300, 108, C["ap"], "Activepieces — the credential vault",
                 ["OAuth: exchange code → store OAUTH2", "token: store SECRET_TEXT",
                  "flows + triggers live here", "the agent never sees a token"]))
    p.append(arrow(306, 150, 430, 150, "connect / ask", lx=368, ly=142))
    p.append(arrow(680, 165, 804, 165, "build + publish flow", col=C["ap"], lx=742, ly=157))

    # FIRE band (bottom)
    p.append(band(30, 276, W - 60, 452, "FIRE  (run-time)", C["ap"]))
    # sources
    p.append(box(56, 312, 232, 150, C["ext"], "A trigger fires",
                 ["channel msg (slack/discord/tg/web)", "schedule tick (cron/poll)",
                  "integration event:", "  github webhook · gmail poll", "  box file · generic webhook"]))
    # the seam
    p.append(box(430, 322, 250, 128, C["cuga"], "/invoke — the one seam",
                 ["envelope {agent, source, event}", "X-Gateway-Token auth",
                  "runtime.run(agent)", "meta.mcp = tools that ran",
                  "content in — never a token"]))
    # AP (fire side) — resolves creds
    p.append(box(804, 300, 300, 92, C["ap"], "AP at fire time",
                 ["resolves {{connections[…]}} → token", "polls / receives the webhook",
                  "the token stays inside AP's sandbox"]))
    # worker fleet + mcp
    p.append(box(430, 500, 250, 92, C["agent"], "The one agent: cuga",
                 ["EVENTS_SUPERVISOR=1 → 27 sub-agents", "(supervisor_agents.yaml); picks one",
                  "per wake-up · one DynamicAgentGraph"]))
    p.append(box(430, 624, 250, 80, C["agent"], "MCP tool servers",
                 ["finance · geo · web · knowledge", "code · local · text (the agents' hands)"]))
    # delivery
    p.append(box(804, 500, 300, 108, C["cuga"], "Delivery",
                 ["direct adapter (Slack/Discord/Box):", "  CUGA-held .env token",
                  "OR AP send-step {{step_1.body.answer}}", "sink from the thread_id origin"]))
    # post-agent ACTION — the "act" half (runs in AP: a flow step, or the direct-trigger executor)
    p.append(box(804, 626, 300, 84, C["ap"], "Post-agent ACTION  (the act half)",
                 ["PUSH: an ACTION step in the AP flow", "DIRECT: a reusable executor flow CUGA fires",
                  "reply/draft/send · Gmail live · AP holds creds"]))
    p.append(arrow(288, 380, 430, 386, "event → envelope", lx=360, ly=372))
    p.append(arrow(804, 346, 680, 380, "AP callback", col=C["ap"], lx=742, ly=336))
    p.append(arrow(555, 450, 555, 500, "", ))
    p.append(arrow(555, 592, 555, 624, "", ))
    p.append(arrow(680, 540, 804, 545, "answer", col=C["cuga"], lx=744, ly=532))
    p.append(arrow(954, 608, 954, 626, "then: act", col=C["ap"], lx=985, ly=620))

    # legend
    lx, ly = 56, 636
    p.append(f'<text x="{lx}" y="{ly}" font-size="12" font-weight="700" fill="{C["ink"]}">Legend</text>')
    for i, (col, lab) in enumerate([(C["ext"], "external / source"), (C["cuga"], "CUGA"),
                                    (C["agent"], "agents / tools"), (C["ap"], "Activepieces (creds)")]):
        yy = ly + 20 + i * 20
        p.append(f'<rect x="{lx}" y="{yy - 11}" width="13" height="13" rx="3" fill="{col}"/>')
        p.append(f'<text x="{lx + 20}" y="{yy}" font-size="11.5" fill="{C["ink"]}">{esc(lab)}</text>')

    p.append("</svg>")
    return "\n".join(p)


def _png(stem: str):
    """Rasterise <stem>.svg → <stem>.png if a converter is on PATH. SVG is the source; PNGs embed
    everywhere. Silently skips if neither rsvg-convert nor cairosvg is installed."""
    import shutil
    import subprocess
    svg, png = OUT / f"{stem}.svg", OUT / f"{stem}.png"
    if shutil.which("rsvg-convert"):
        subprocess.run(["rsvg-convert", "-z", "1.4", str(svg), "-o", str(png)], check=False)
    elif shutil.which("cairosvg"):
        subprocess.run(["cairosvg", str(svg), "-o", str(png), "-s", "1.4"], check=False)


def main():
    OUT.mkdir(exist_ok=True)
    (OUT / "system.svg").write_text(system_svg())
    _png("system")
    for name, sc in SCENARIOS.items():
        (OUT / f"{name}.svg").write_text(seq_svg(sc["title"], sc["subtitle"], sc["actors"], sc["messages"]))
        _png(name)
    print(f"wrote system + {len(SCENARIOS)} diagrams (svg + png): "
          f"{sum(1 for k in SCENARIOS if k.startswith('create'))} CREATE, "
          f"{sum(1 for k in SCENARIOS if k.startswith('fire'))} FIRE → {OUT}")


if __name__ == "__main__":
    main()
