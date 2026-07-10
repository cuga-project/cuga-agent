#!/usr/bin/env python3
"""Generate the architecture SVGs — one system overview + one sequence diagram per flow shape.

    python architecture/gen_diagrams.py

Everything is hand-derived from the code under src/cuga/backend/events/, so the diagrams are a
statement about the system as it actually is, not a whiteboard sketch. When the flow shapes change,
edit the SCENARIOS / SYSTEM data here and regenerate — never hand-edit the SVGs.

No external deps: the SVGs are plain strings, theme-neutral (readable on white), self-contained.

The one load-bearing fact the diagrams exist to teach: **`/invoke` is the single seam.** Every
trigger, channel, and integration normalises its event into an envelope and POSTs it to `/invoke`.
Activepieces calls back over the podman-internal `HOST_CALLBACK_URL` (NOT the public tunnel — the
tunnel is inbound only: webhooks + OAuth callbacks). Delivery is either an AP send-step or CUGA's own
direct adapter, decided by `delivery.is_direct(channel)`.
"""
from __future__ import annotations

import pathlib

OUT = pathlib.Path(__file__).resolve().parent

# palette — colour by role, consistent across every diagram
C = {
    "you": "#6b6866", "ext": "#8a8785", "cuga": "#2b5fd9", "ap": "#b06f10",
    "agent": "#1a7f4b", "ink": "#1c1b1a", "line": "#c9c6c1", "bg": "#ffffff",
    "note": "#7a3fa8", "faint": "#f4f3f1",
}


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ── sequence-diagram engine ───────────────────────────────────────────────────
# A scenario is a list of actors (id,label,colorkey) and messages. Each message:
#   (from, to, text, style)   style ∈ solid | dashed | self | note
def seq_svg(title: str, subtitle: str, actors: list, messages: list) -> str:
    LM, TOP = 30, 96          # left margin, top of lifelines
    col_w = 172
    head_h = 46
    row_h = 52
    xs = {a[0]: LM + col_w // 2 + i * col_w for i, a in enumerate(actors)}
    width = LM * 2 + col_w * len(actors)
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

    # lifelines + heads
    for a in actors:
        x = xs[a[0]]
        col = C[a[2]]
        p.append(f'<line x1="{x}" y1="{TOP + head_h}" x2="{x}" y2="{height - 24}" '
                 f'stroke="{C["line"]}" stroke-width="1.4"/>')
        p.append(f'<rect x="{x - col_w // 2 + 8}" y="{TOP}" width="{col_w - 16}" height="{head_h}" '
                 f'rx="9" fill="{col}"/>')
        # label may wrap into two lines on \n
        lines = a[1].split("\n")
        ly = TOP + (26 if len(lines) == 2 else 29)
        for ln in lines:
            p.append(f'<text x="{x}" y="{ly}" font-size="12.5" font-weight="600" fill="#fff" '
                     f'text-anchor="middle">{esc(ln)}</text>')
            ly += 15

    # messages
    y = body_top
    for m in messages:
        frm, to, text, style = m
        if style == "note":
            # a full-width note band
            p.append(f'<rect x="{LM}" y="{y - 16}" width="{width - LM * 2}" height="30" rx="6" '
                     f'fill="{C["faint"]}" stroke="{C["note"]}" stroke-opacity="0.4"/>')
            p.append(f'<text x="{width // 2}" y="{y + 4}" font-size="12" fill="{C["note"]}" '
                     f'text-anchor="middle" font-style="italic">{esc(text)}</text>')
            y += row_h - 8
            continue
        x1, x2 = xs[frm], xs[to]
        dash = ' stroke-dasharray="5 4"' if style == "dashed" else ""
        marker = "arwo" if style == "dashed" else "arw"
        if style == "self" or frm == to:
            # self-loop
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


A = {  # actor palette shortcuts
    "you": ("you", "You", "you"),
    "chan": lambda n: (n.lower(), n, "ext"),
    "ext": lambda n: (n.lower().replace(" ", ""), n, "ext"),
    "cuga": ("cuga", "CUGA\n/invoke", "cuga"),
    "concierge": ("concierge", "Concierge\n(NL→flow)", "cuga"),
    "ap": ("ap", "Activepieces", "ap"),
    "agent": lambda n: ("agent", n, "agent"),
}


SCENARIOS = {
"seq-01-now": dict(
    title="NOW — a one-shot question",
    subtitle="No Activepieces, no flow armed. The answer IS the HTTP response. (make test-suite-now)",
    actors=[A["you"], A["cuga"], A["agent"]("pricebot\n(worker)")],
    messages=[
        ("you", "cuga", 'POST /invoke  {agent, text, source:time/runonce}', "solid"),
        ("cuga", "agent", "runtime.run(agent, worker_input)", "solid"),
        ("agent", "cuga", "answer + meta.mcp (tools used)", "dashed"),
        ("cuga", "you", '200 {answer, meta:{mcp,tools,ms}}', "dashed"),
        (None, None, "meta.mcp proves a real tool ran — not memory", "note"),
    ]),

"seq-02-concierge": dict(
    title="Concierge — English sentence → an armed flow",
    subtitle="POST /api/concierge. ?flow=1 also returns the AP flow it built.",
    actors=[A["you"], A["concierge"], A["ap"]],
    messages=[
        ("you", "concierge", 'POST /api/concierge {text:"every 9am send BTC price"}', "solid"),
        ("concierge", "concierge", "classify → CRON/POLL/PUSH + pick agent", "self"),
        ("concierge", "ap", "find_or_create_flow → build + LOCK_AND_PUBLISH", "solid"),
        ("ap", "concierge", "ap_flow_id (ENABLED)", "dashed"),
        ("concierge", "you", '200 {reply, flows:[{ap_flow_id, steps}]}  (?flow=1)', "dashed"),
    ]),

"seq-03-cron-poll": dict(
    title="CRON / POLL — a scheduled flow fires",
    subtitle="AP owns the trigger. Callback is podman-internal (HOST_CALLBACK_URL), NOT the tunnel.",
    actors=[A["ap"], A["cuga"], A["agent"]("worker"), A["chan"]("Sink")],
    messages=[
        ("ap", "ap", "schedule tick (every N min / cron)", "self"),
        ("ap", "cuga", "POST /invoke {agent, source:time/tick, deliver}", "solid"),
        ("cuga", "agent", "runtime.run(...)", "solid"),
        ("agent", "cuga", "answer", "dashed"),
        ("cuga", "ap", '200 {answer}', "dashed"),
        ("ap", "sink", "AP send-step  {{step_1.body.answer}}", "solid"),
        (None, None, "direct sink (Slack)? no send-step — CUGA delivers itself", "note"),
    ]),

"seq-04-push-github": dict(
    title="PUSH · GitHub — a webhook trigger",
    subtitle="AP registers a real repo webhook (needs OAuth conn + admin:repo_hook). Inbound via tunnel.",
    actors=[A["ext"]("GitHub"), A["ap"], A["cuga"], A["agent"]("pr_reviewer")],
    messages=[
        ("github", "ap", "PR opened → webhook POST (via AP tunnel)", "solid"),
        ("ap", "cuga", "POST /invoke {source:github, payload:{{trigger.title,body,…}}}", "solid"),
        ("cuga", "agent", "worker_input = PR title + body + diff stats", "solid"),
        ("agent", "cuga", "MATCH/summary", "dashed"),
        ("cuga", "ap", "200 {answer}", "dashed"),
        ("ap", "ap", "AP send-step → deliver to sink", "self"),
        (None, None, "piece-github takes OAUTH2 only — a PAT (SECRET_TEXT) is unusable", "note"),
    ]),

"seq-05-push-gmail": dict(
    title="PUSH · Gmail — a polling trigger",
    subtitle="AP polls Gmail on its own clock (no push). The armed watcher fires per new email.",
    actors=[A["ext"]("Gmail"), A["ap"], A["cuga"], A["agent"]("mailbot")],
    messages=[
        ("ap", "gmail", "poll: any new mail? (every ~1-2 min)", "solid"),
        ("gmail", "ap", "new email {subject, from, body}", "dashed"),
        ("ap", "cuga", "POST /invoke {source:gmail, payload}", "solid"),
        ("cuga", "agent", "summarize the email", "solid"),
        ("agent", "cuga", "summary", "dashed"),
        ("cuga", "ap", "200 {answer} → AP send-step / direct sink", "dashed"),
        (None, None, "polling trigger ⇒ /run can't fire it out of band", "note"),
    ]),

"seq-06-push-box": dict(
    title="PUSH · Box — direct poll + the download step",
    subtitle="EVENTS_BOX_BACKEND=direct. CUGA polls Box itself (no AP), and downloads file CONTENT.",
    actors=[A["ext"]("Box"), A["cuga"], A["agent"]("resume_judge"), A["chan"]("Slack")],
    messages=[
        ("cuga", "cuga", "POST /api/events/box/poll (scheduled/manual)", "self"),
        ("cuga", "box", "list folder since watermark", "solid"),
        ("box", "cuga", "new files [ids]", "dashed"),
        ("cuga", "box", "download bytes (server holds the token)", "solid"),
        ("box", "cuga", "file content", "dashed"),
        ("cuga", "cuga", "inline text / base64; attach JD", "self"),
        ("cuga", "agent", "POST /invoke {text: prompt+resume, jd}", "solid"),
        ("agent", "slack", "MATCH/SKIP + reasoning (direct send)", "solid"),
    ]),

"seq-07-channel-slack": dict(
    title="Channel · Slack — direct backend (default)",
    subtitle="No AP. Slack Events API → CUGA (signature-verified) → concierge → reply in thread.",
    actors=[A["you"], A["ext"]("Slack"), A["cuga"], A["agent"]("worker")],
    messages=[
        ("you", "slack", "type a message in the channel", "solid"),
        ("slack", "cuga", "POST /api/events/slack/events (signed)", "solid"),
        ("cuga", "cuga", "verify_signature(SLACK_SIGNING_SECRET); ack <3s", "self"),
        ("cuga", "agent", "background: /invoke via concierge", "solid"),
        ("agent", "cuga", "answer", "dashed"),
        ("cuga", "slack", "chat.postMessage in-thread (bot token)", "solid"),
        (None, None, "secret unset ⇒ ANY request accepted (spoofable)", "note"),
    ]),

"seq-08-channel-telegram": dict(
    title="Channel · Telegram — Activepieces backend",
    subtitle="Telegram always runs via AP: a polling trigger and an AP send-step. (Discord = direct WS bot.)",
    actors=[A["you"], A["ext"]("Telegram"), A["ap"], A["cuga"]],
    messages=[
        ("you", "telegram", "DM the bot", "solid"),
        ("ap", "telegram", "poll new_message", "solid"),
        ("telegram", "ap", "message {chat_id, text}", "dashed"),
        ("ap", "cuga", "POST /invoke {source:channel, thread_id:gw:telegram:<id>}", "solid"),
        ("cuga", "ap", "200 {answer}", "dashed"),
        ("ap", "telegram", "AP send-step → reply to chat", "solid"),
    ]),

"seq-09-webhook": dict(
    title="Generic webhook — any system → an agent",
    subtitle="POST /api/events/hook/<name>. No AP, no piece. ?key= guards it (unset = open).",
    actors=[("src", "Monitoring\n/ CI / form", "ext"), A["cuga"],
            A["agent"]("incident_triage"), A["chan"]("Slack")],
    messages=[
        ("src", "cuga", "POST /api/events/hook/alert?agent=…&key=…", "solid"),
        ("cuga", "cuga", "check ?key vs EVENTS_WEBHOOK_KEY", "self"),
        ("cuga", "agent", "/invoke {text: JSON payload}", "solid"),
        ("agent", "cuga", "triage (P1/P2 …)", "dashed"),
        ("cuga", "src", "200 {answer}", "dashed"),
        ("cuga", "slack", "optional: ?deliver_to → direct send", "solid"),
    ]),
}


# ── system overview (hand-built) ──────────────────────────────────────────────
def system_svg() -> str:
    W, H = 1120, 720
    def box(x, y, w, h, fill, title, lines, tcol="#fff", r=12):
        s = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{fill}"/>',
             f'<text x="{x + 14}" y="{y + 26}" font-size="15" font-weight="700" fill="{tcol}">{esc(title)}</text>']
        yy = y + 46
        for ln in lines:
            s.append(f'<text x="{x + 14}" y="{yy}" font-size="12" fill="{tcol}" '
                     f'fill-opacity="0.92">{esc(ln)}</text>')
            yy += 18
        return "\n".join(s)

    def arrow(x1, y1, x2, y2, label="", dash=False, col=C["ink"], lx=None, ly=None):
        d = ' stroke-dasharray="5 4"' if dash else ""
        s = [f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{col}" stroke-width="1.6"{d} '
             f'marker-end="url(#a)"/>']
        if label:
            s.append(f'<text x="{lx or (x1 + x2) // 2}" y="{ly or (y1 + y2) // 2 - 6}" '
                     f'font-size="11.5" fill="{col}" text-anchor="middle">{esc(label)}</text>')
        return "\n".join(s)

    p = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
         f'font-family="ui-sans-serif,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">',
         f'<rect width="{W}" height="{H}" fill="{C["bg"]}"/>',
         '<defs><marker id="a" markerWidth="11" markerHeight="11" refX="8" refY="3.5" orient="auto">'
         f'<path d="M0,0 L9,3.5 L0,7 Z" fill="{C["ink"]}"/></marker></defs>',
         f'<text x="40" y="42" font-size="24" font-weight="700" fill="{C["ink"]}">'
         'CUGA event-driven agents — system architecture</text>',
         f'<text x="40" y="66" font-size="13.5" fill="{C["ext"]}">'
         'Every trigger, channel and integration normalises its event into an envelope and POSTs it to '
         'the one seam: /invoke.</text>']

    # LEFT: sources (channels / integrations / time / external)
    p.append(box(40, 100, 232, 120, C["ext"], "Channels (converse)",
                 ["web · slack* · discord* · telegram", "*direct backend (no AP)",
                  "inbound: chat message → reply"]))
    p.append(box(40, 236, 232, 120, C["ext"], "Integrations (watch/act)",
                 ["gmail (AP poll) · github (AP webhook)", "box (direct poll + download)",
                  "generic webhook (no AP)"]))
    p.append(box(40, 372, 232, 92, C["ext"], "Time",
                 ["cron / poll schedules", "(Activepieces schedule piece)"]))

    # CENTER-TOP: concierge
    p.append(box(430, 100, 250, 96, C["cuga"], "Concierge  (NL → flow)",
                 ["POST /api/concierge", "classify + find_or_create_flow",
                  "?flow=1 → returns the armed flow"]))
    # CENTER: the seam
    p.append(box(430, 250, 250, 120, C["cuga"], "/invoke   ← the one seam",
                 ["envelope: {agent, source, event}", "X-Gateway-Token auth",
                  "runtime.run(agent) · deliver", "meta.mcp = tools actually used"]))
    # CENTER-BOTTOM: worker fleet
    p.append(box(430, 420, 250, 96, C["agent"], "Worker fleet (18 agents)",
                 ["pricebot · mailbot · resume_judge …", "each = prompt + MCP tools + access",
                  "runs its own DynamicAgentGraph"]))
    # MCP
    p.append(box(430, 552, 250, 84, C["agent"], "MCP tool servers",
                 ["finance · geo · web · knowledge", "code · text  (the agents' hands)"]))

    # RIGHT: Activepieces
    p.append(box(812, 150, 268, 200, C["ap"], "Activepieces (AP)",
                 ["owns triggers + all credentials", "schedule · gmail · github · telegram pieces",
                  "connections: OAUTH2 / SECRET_TEXT", "calls back → HOST_CALLBACK_URL",
                  "(podman-internal, not the tunnel)", "public tunnel = inbound webhooks only",
                  "⚠ cloudflared tunnel is ephemeral"]))
    # RIGHT-BOTTOM: delivery
    p.append(box(812, 400, 268, 116, C["cuga"], "Delivery",
                 ["direct adapter (Slack/Discord/Box)", "  delivery.is_direct(channel)",
                  "OR AP send-step {{step_1.body.answer}}", "sink parsed from thread_id origin"]))

    # arrows
    p.append(arrow(272, 160, 430, 148, "converse", lx=350, ly=140))
    p.append(arrow(272, 300, 430, 300, "events", lx=350, ly=292))
    p.append(arrow(272, 410, 812, 250, "arm schedule", dash=True, col=C["ap"], lx=560, ly=372))
    p.append(arrow(680, 148, 812, 210, "arm / reuse flow", col=C["ap"], lx=760, ly=176))
    p.append(arrow(812, 300, 680, 300, "POST /invoke (callback)", col=C["ap"], lx=746, ly=292))
    p.append(arrow(555, 196, 555, 250, "", ))          # concierge → invoke
    p.append(arrow(555, 370, 555, 420, "", ))          # invoke → fleet
    p.append(arrow(555, 516, 555, 552, "", ))          # fleet → mcp
    p.append(arrow(680, 470, 812, 460, "answer", col=C["cuga"], lx=750, ly=452))
    p.append(arrow(946, 400, 946, 356, "", col=C["cuga"]))   # delivery → AP send path (up)

    # legend
    lx, ly = 40, 520
    p.append(f'<text x="{lx}" y="{ly}" font-size="12" font-weight="700" fill="{C["ink"]}">Legend</text>')
    for i, (col, lab) in enumerate([(C["ext"], "external / source"), (C["cuga"], "CUGA"),
                                    (C["agent"], "agents / tools"), (C["ap"], "Activepieces")]):
        yy = ly + 22 + i * 22
        p.append(f'<rect x="{lx}" y="{yy - 11}" width="14" height="14" rx="3" fill="{col}"/>')
        p.append(f'<text x="{lx + 22}" y="{yy}" font-size="12" fill="{C["ink"]}">{esc(lab)}</text>')

    p.append("</svg>")
    return "\n".join(p)


def main():
    OUT.mkdir(exist_ok=True)
    (OUT / "system.svg").write_text(system_svg())
    n = 1
    for name, sc in SCENARIOS.items():
        (OUT / f"{name}.svg").write_text(seq_svg(sc["title"], sc["subtitle"], sc["actors"], sc["messages"]))
        n += 1
    print(f"wrote system.svg + {len(SCENARIOS)} sequence diagrams to {OUT}")


if __name__ == "__main__":
    main()
