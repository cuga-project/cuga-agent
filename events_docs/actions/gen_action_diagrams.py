#!/usr/bin/env python3
"""Generate the ACTION-layer diagrams (architecture + sequences) as SVG + PNG.

    python events_docs/actions/gen_action_diagrams.py

Hand-derived from src/cuga/backend/events/{actions,flows,concierge,ap_engine}.py — a statement about
the action half as it actually is. Edit the data here and regenerate; never hand-edit the SVGs.

Reuses the proven sequence engine from events_docs/architecture/gen_diagrams.py (copied here so this
script is self-contained). PNGs are rasterised via rsvg-convert or cairosvg if present.

Diagrams:
  architecture   — the whole action path: NL → concierge (trigger gate + ACTION gate) → registries →
                   AP flow (trigger ▸ /invoke ▸ agent ▸ ACTION step) → connector.
  seq-arm        — arm-time: an utterance becomes a standing flow whose last step is a Gmail action.
  seq-fire       — run-time: an email arrives, the agent answers, AP runs the action (draft created).
  seq-ask        — the safety path: an ambiguous/underspecified request returns ONE question.
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess

OUT = pathlib.Path(__file__).resolve().parent

C = {
    "you": "#6b6866", "ext": "#8a8785", "cuga": "#2b5fd9", "ap": "#b06f10",
    "agent": "#1a7f4b", "ink": "#1c1b1a", "line": "#c9c6c1", "bg": "#ffffff",
    "note": "#7a3fa8", "cred": "#c0392b", "faint": "#f4f3f1", "credbg": "#fdecea",
    "reg": "#5a3fa8", "regbg": "#f3effb",
}


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ── sequence engine (copied from architecture/gen_diagrams.py) ──────────────────
def seq_svg(title: str, subtitle: str, actors: list, messages: list) -> str:
    LM, TOP = 30, 96
    col_w = 190
    head_h = 46
    row_h = 52
    xs = {a[0]: LM + col_w // 2 + i * col_w for i, a in enumerate(actors)}
    last_id = actors[-1][0]
    self_pad = max((len(t) for f, tt, t, s in messages if s == "self" and f == last_id), default=0)
    width = LM * 2 + col_w * len(actors) + (min(self_pad, 46) * 7 + 50 if self_pad else 0)
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


# ── architecture (box diagram) ──────────────────────────────────────────────────
def architecture_svg() -> str:
    W, H = 1160, 620
    def box(x, y, w, h, fill, stroke, title, subs, tcol="#fff"):
        out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="11" fill="{fill}" '
               f'stroke="{stroke}" stroke-width="1.5"/>',
               f'<text x="{x + w/2}" y="{y + 22}" font-size="14" font-weight="700" '
               f'fill="{tcol}" text-anchor="middle">{esc(title)}</text>']
        ly = y + 41
        for s in subs:
            out.append(f'<text x="{x + w/2}" y="{ly}" font-size="11.5" fill="{tcol}" '
                       f'fill-opacity="0.92" text-anchor="middle">{esc(s)}</text>')
            ly += 15
        return "\n".join(out)

    def arrow(x1, y1, x2, y2, label=""):
        out = [f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{C["ink"]}" '
               f'stroke-width="1.6" marker-end="url(#a)"/>']
        if label:
            out.append(f'<text x="{(x1+x2)/2}" y="{(y1+y2)/2 - 6}" font-size="11" '
                       f'fill="{C["ink"]}" text-anchor="middle">{esc(label)}</text>')
        return "\n".join(out)

    p = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
         f'font-family="ui-sans-serif,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">',
         f'<rect width="{W}" height="{H}" fill="{C["bg"]}"/>',
         '<defs><marker id="a" markerWidth="11" markerHeight="11" refX="8" refY="3" orient="auto">'
         f'<path d="M0,0 L8,3 L0,6 Z" fill="{C["ink"]}"/></marker></defs>',
         f'<text x="30" y="36" font-size="21" font-weight="700" fill="{C["ink"]}">'
         'CUGA Events — the ACTION half</text>',
         f'<text x="30" y="58" font-size="13" fill="{C["ext"]}">trigger ▸ /invoke ▸ agent ▸ '
         'ACTION step — the agent reasons, Activepieces acts, the agent holds no credentials</text>']

    # arm-time band (top)
    p.append(f'<text x="30" y="92" font-size="12" font-weight="700" fill="{C["note"]}">'
             'ARM-TIME  (natural language → a standing flow)</text>')
    p.append(box(30, 104, 176, 74, "#fff", C["you"], "Utterance", ["“when I get an email,", "draft a reply”"], C["ink"]))
    p.append(box(250, 104, 250, 74, C["cuga"], C["cuga"], "Concierge", ["trigger gate + ACTION gate", "verb-align · ask-till-legit"]))
    p.append(box(548, 104, 250, 74, C["regbg"], C["reg"], "Registries (DATA)",
                 ["triggers.py · actions.py", "generated from live AP"], C["reg"]))
    p.append(box(846, 104, 284, 74, "#fff", C["ap"], "AP flow (JSON)",
                 ["trigger ▸ /invoke ▸ [action]", "built by flows.py / ap_engine.py"], C["ink"]))
    p.append(arrow(206, 141, 250, 141))
    p.append(arrow(500, 141, 548, 141, "validate"))
    p.append(arrow(798, 141, 846, 141, "arm"))

    # fire-time band (bottom)
    p.append(f'<text x="30" y="250" font-size="12" font-weight="700" fill="{C["ap"]}">'
             'FIRE-TIME  (the trigger fires → the action runs)</text>')
    yb = 262
    p.append(box(30, yb, 170, 78, "#fff", C["ext"], "Trigger", ["gmail new_email", "(or github/box/…)"], C["ink"]))
    p.append(box(240, yb, 210, 78, C["ap"], C["ap"], "Activepieces", ["fires the flow;", "resolves connections"]))
    p.append(box(490, yb, 210, 78, C["cuga"], C["cuga"], "POST /invoke", ["the one seam;", "normalised envelope"]))
    p.append(box(740, yb, 180, 78, C["agent"], C["agent"], "Agent (cuga)", ["reasons over the event", "→ answer text"]))
    p.append(box(960, yb, 170, 78, C["ap"], C["ap"], "ACTION step", ["gmail reply/draft/send", "{{step_1.body.answer}}"]))
    p.append(arrow(200, yb + 39, 240, yb + 39))
    p.append(arrow(450, yb + 39, 490, yb + 39))
    p.append(arrow(700, yb + 39, 740, yb + 39, "text"))
    p.append(arrow(920, yb + 39, 960, yb + 39, "answer"))

    # resolve_action callout
    p.append(box(360, 430, 440, 66, C["regbg"], C["reg"], "resolve_action  (tool-first, AP-fallback)",
                 ["agent already has a matching tool? → it acts in-run.",
                  "else → append this AP action step (the default)."], C["reg"]))
    p.append(arrow(1045, yb + 78, 1045, 500))
    p.append(arrow(1045, 500, 800, 463))

    # credential note
    p.append(f'<rect x="30" y="540" width="{W-60}" height="52" rx="9" fill="{C["credbg"]}" '
             f'stroke="{C["cred"]}" stroke-opacity="0.5"/>')
    p.append(f'<text x="{W/2}" y="562" font-size="12.5" fill="{C["cred"]}" text-anchor="middle" '
             'font-weight="700">🔑  The agent never holds a credential.</text>')
    p.append(f'<text x="{W/2}" y="580" font-size="11.5" fill="{C["cred"]}" text-anchor="middle">'
             'AP owns the trigger AND the action connection ({{connections[…]}}), resolved inside '
             'AP’s sandbox at fire time. Adding a piece = regenerate registry rows, no new code.</text>')
    p.append("</svg>")
    return "\n".join(p)


A = lambda i, l, c: (i, l, c)  # noqa: E731

SEQ = {
"seq-arm": dict(
    title="Arm-time — an utterance becomes a flow that ACTS",
    subtitle="find_or_create_flow: trigger gate, then the symmetric ACTION gate, then the engine arms it",
    actors=[A("u", "You\n(chat)", "you"), A("c", "Concierge", "cuga"),
            A("r", "Registries\ntriggers/actions", "reg"), A("e", "AP engine", "ap"),
            A("ap", "Activepieces", "ap")],
    messages=[
        ("u", "c", "“when I get an email, reply to the sender”", "solid"),
        ("c", "r", "validate trigger (gmail/new_email)", "solid"),
        ("r", "c", "ok — armable", "dashed"),
        ("c", "r", "validate action (gmail/reply_to_email)", "solid"),
        (None, None, "verb-alignment: “reply” matches reply_to_email ✓ (a mismatch → ask, never guess)", "note"),
        ("r", "c", "ok — message_id from trigger, body from answer", "dashed"),
        ("c", "c", "render_params + resolve_action → AP step", "self"),
        ("c", "e", "create_push_flow(actions=[reply_to_email])", "solid"),
        ("e", "ap", "trigger ▸ /invoke ▸ ACTION step; publish", "solid"),
        (None, None, "action carries the gmail connection auth ({{connections[…]}})", "cred"),
        ("e", "c", "flow id", "dashed"),
        ("c", "u", "“ARMED … then gmail/reply_to_email” (names the action)", "dashed"),
    ]),

"seq-fire": dict(
    title="Fire-time — the email arrives, the action runs",
    subtitle="one seam (/invoke); the agent answers; AP runs the Gmail action step with that answer",
    actors=[A("g", "Gmail", "ext"), A("ap", "Activepieces", "ap"),
            A("iv", "POST /invoke", "cuga"), A("ag", "Agent (cuga)", "agent")],
    messages=[
        ("g", "ap", "new email (polling trigger)", "solid"),
        ("ap", "iv", "envelope: event.payload (+_raw)", "solid"),
        ("iv", "ag", "run once on this event", "solid"),
        ("ag", "iv", "answer text (the reply body)", "dashed"),
        ("iv", "ap", "answer → {{step_1.body.answer}}", "dashed"),
        ("ap", "ap", "run reply_to_email step", "self"),
        (None, None, "AP resolves the gmail connection in its sandbox — the agent saw no token", "cred"),
        ("ap", "g", "create the reply / draft", "solid"),
    ]),

"seq-ask": dict(
    title="The safety path — ambiguous request returns ONE question",
    subtitle="never silently the wrong action: unknown / missing-slot / can't-infer all become a question",
    actors=[A("u", "You\n(chat)", "you"), A("c", "Concierge", "cuga"),
            A("r", "actions.py\nvalidate/gate", "reg")],
    messages=[
        ("u", "c", "“when I get an email, email me about it”", "solid"),
        ("c", "r", "action gmail/send_email — receiver?", "solid"),
        (None, None, "‘me’ has no address here; the trigger sender is only usable for gmail 'from'", "note"),
        ("r", "c", "required slot unfilled", "dashed"),
        ("c", "u", "“Who should I send the email to? Give an address.”", "dashed"),
        (None, None, "nothing armed — build stops until the answer arrives (ask-till-legit)", "note"),
    ]),
}


def _png(stem: str):
    svg, png = OUT / f"{stem}.svg", OUT / f"{stem}.png"
    if shutil.which("rsvg-convert"):
        subprocess.run(["rsvg-convert", "-z", "1.5", str(svg), "-o", str(png)], check=False)
    elif shutil.which("cairosvg"):
        subprocess.run(["cairosvg", str(svg), "-o", str(png), "-s", "1.5"], check=False)


def main():
    OUT.mkdir(exist_ok=True)
    (OUT / "architecture.svg").write_text(architecture_svg())
    _png("architecture")
    for name, sc in SEQ.items():
        (OUT / f"{name}.svg").write_text(seq_svg(sc["title"], sc["subtitle"], sc["actors"], sc["messages"]))
        _png(name)
    print(f"wrote architecture + {len(SEQ)} sequence diagrams (svg + png) → {OUT}")


if __name__ == "__main__":
    main()
