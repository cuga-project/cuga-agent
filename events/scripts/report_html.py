#!/usr/bin/env python3
"""Render one test run as a self-contained HTML page.

`run_all_tests.py` calls `render_html(...)` and writes the result to both
`results/runs/<stamp>/report.html` and `results/index.html` (the latest run).

Everything here is derived from artefacts the run already produced — the per-harness `.log` files and
`steps.jsonl`. Nothing is hand-maintained, so the page cannot drift from what actually happened. That
is the whole reason it replaced the hand-written matrix: a curated results page is a claim about a run
that nobody can check.

The page is one file, no CDN, no fonts, no scripts beyond a theme toggle — so it survives being
emailed, committed, or opened from a `file://` URL.
"""

from __future__ import annotations

import html
import re

ANSI = re.compile(r"\x1b\[[0-9;]*m")

# live_suite.py:47 — one glyph per verdict
SUITE_VERDICT = {"✓": "pass", "✗": "fail", "x": "xfail", "★": "xpass", "–": "skip"}
SUITE_CASE = re.compile(r"^ {3}([✓✗x★–]) (\S+)\s+—\s+(.*)$")
SUITE_NOTE = re.compile(r"^ {6,}(\S.*)$")

# live_matrix.py:56 — ARMED, REUSED, NEEDS, CONNECT, ERROR, SKIPPED, STALE
MATRIX_CELL = re.compile(r"^ {4,6}([✓≡?⚠✗–!]) (\S.*?)\s+→ (\w+)\s*(.*)$")
MATRIX_MEANING = {
    "✓": ("armed", "pass", "a new Activepieces flow was created and verified to exist"),
    "≡": ("reused", "pass", "an equivalent flow already existed (dedup_key hit) — the plumbing works"),
    "?": ("needs-input", "xfail", "the concierge asked for a missing trigger input — correct behaviour"),
    "⚠": ("connect-needed", "xfail", "the gate asked you to connect an integration the agent needs"),
    "!": ("claims-existing", "xpass", "the model said a flow exists; none was found (stale thread memory)"),
    "✗": ("error", "fail", "HTTP error, no subscription, or an armed flow with no live ap_flow_id"),
    "–": ("skip", "skip", "surface not configured"),
}

CSS = """
:root{--bg:#fbfbfa;--panel:#fff;--ink:#1c1b1a;--muted:#6b6866;--line:#e6e3df;
  --pass:#1a7f4b;--pass-bg:#e8f5ee;--fail:#c0392b;--fail-bg:#fdecea;
  --xfail:#b06f10;--xfail-bg:#fdf3e2;--xpass:#7a3fa8;--xpass-bg:#f3ebfa;
  --skip:#8a8785;--skip-bg:#f2f1ef;--accent:#2b5fd9;}
@media (prefers-color-scheme:dark){:root{--bg:#16181c;--panel:#1d2025;--ink:#e8e6e3;--muted:#9a9793;
  --line:#2e3238;--pass:#5ed19a;--pass-bg:#153126;--fail:#ff8a80;--fail-bg:#3a1d1a;
  --xfail:#f0b95c;--xfail-bg:#372a15;--xpass:#c79bec;--xpass-bg:#2b1e3a;
  --skip:#8a8785;--skip-bg:#24262b;--accent:#7aa2f7;}}
:root[data-theme=dark]{--bg:#16181c;--panel:#1d2025;--ink:#e8e6e3;--muted:#9a9793;--line:#2e3238;
  --pass:#5ed19a;--pass-bg:#153126;--fail:#ff8a80;--fail-bg:#3a1d1a;--xfail:#f0b95c;--xfail-bg:#372a15;
  --xpass:#c79bec;--xpass-bg:#2b1e3a;--skip:#8a8785;--skip-bg:#24262b;--accent:#7aa2f7;}
:root[data-theme=light]{--bg:#fbfbfa;--panel:#fff;--ink:#1c1b1a;--muted:#6b6866;--line:#e6e3df;
  --pass:#1a7f4b;--pass-bg:#e8f5ee;--fail:#c0392b;--fail-bg:#fdecea;--xfail:#b06f10;--xfail-bg:#fdf3e2;
  --xpass:#7a3fa8;--xpass-bg:#f3ebfa;--skip:#8a8785;--skip-bg:#f2f1ef;--accent:#2b5fd9;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.6 ui-sans-serif,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:32px 20px 80px}
h1{font-size:1.9rem;margin:0 0 6px;letter-spacing:-.02em}
h2{font-size:1.15rem;margin:44px 0 4px;letter-spacing:-.01em}
h3{font-size:.95rem;margin:24px 0 2px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.sub{color:var(--muted);margin:0 0 18px}
p{margin:.6em 0}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.88em;
  background:var(--skip-bg);padding:1px 5px;border-radius:4px}
a{color:var(--accent)}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 18px;margin:14px 0}
.meta{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:6px 24px;font-size:.9rem}
.meta b{color:var(--muted);font-weight:600;text-transform:uppercase;font-size:.72rem;letter-spacing:.05em}
.cards{display:flex;flex-wrap:wrap;gap:10px;margin:18px 0 4px}
.card{flex:1 1 110px;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.card .v{font-size:1.7rem;font-weight:600;line-height:1.1}
.card .k{color:var(--muted);font-size:.78rem;text-transform:uppercase;letter-spacing:.05em}
.card.pass .v{color:var(--pass)}.card.fail .v{color:var(--fail)}
.card.xfail .v{color:var(--xfail)}.card.xpass .v{color:var(--xpass)}.card.skip .v{color:var(--skip)}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{border-collapse:collapse;width:100%;min-width:680px;font-size:.9rem}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-weight:600;color:var(--muted);font-size:.75rem;text-transform:uppercase;letter-spacing:.04em}
tbody tr:hover{background:var(--skip-bg)}
td.n{text-align:right;font-variant-numeric:tabular-nums}
td.u{color:var(--muted);font-size:.86rem}
td.got{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.8rem;max-width:340px}
td.utt{font-style:italic;max-width:230px;font-size:.85rem}
td.v{text-align:center;font-weight:700}
.b{display:inline-block;padding:1px 8px;border-radius:20px;font-size:.74rem;font-weight:600;white-space:nowrap}
.b.pass{color:var(--pass);background:var(--pass-bg)}.b.fail{color:var(--fail);background:var(--fail-bg)}
.b.xfail{color:var(--xfail);background:var(--xfail-bg)}.b.xpass{color:var(--xpass);background:var(--xpass-bg)}
.b.skip{color:var(--skip);background:var(--skip-bg)}
.b.crash{color:#fff;background:var(--fail)}
.g-pass{color:var(--pass)}.g-fail{color:var(--fail)}.g-xfail{color:var(--xfail)}
.g-xpass{color:var(--xpass)}.g-skip{color:var(--skip)}
.callout{border-left:3px solid var(--accent);padding:6px 0 6px 14px;margin:14px 0;color:var(--muted);font-size:.9rem}
.note{border-left:3px solid var(--accent);padding:2px 0 2px 14px;margin:14px 0;color:var(--muted)}
.warn{border-left:3px solid var(--fail);padding:2px 0 2px 14px;margin:14px 0}
.legend{display:flex;flex-wrap:wrap;gap:16px;color:var(--muted);font-size:.84rem;margin:10px 0}
.sn{color:var(--muted);font-size:.82rem;display:block;margin-top:3px}
details{margin:8px 0}summary{cursor:pointer;font-weight:600}
.toggle{position:fixed;top:14px;right:16px;background:var(--panel);border:1px solid var(--line);
  color:var(--muted);border-radius:8px;padding:5px 10px;cursor:pointer;font-size:.9rem}
.foot{margin-top:60px;color:var(--muted);font-size:.85rem;border-top:1px solid var(--line);padding-top:16px}
"""

TOGGLE_JS = """
const r=document.documentElement,K='cuga-report-theme';
const s=localStorage.getItem(K); if(s) r.dataset.theme=s;
document.querySelector('.toggle').onclick=()=>{
  const cur=r.dataset.theme||(matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light');
  const nxt=cur==='dark'?'light':'dark'; r.dataset.theme=nxt; localStorage.setItem(K,nxt);};
"""


def e(t) -> str:
    return html.escape(str(t if t is not None else ""))


def strip(t: str) -> str:
    return ANSI.sub("", t)


def badge(kind: str, label: str | None = None) -> str:
    return f'<span class="b {kind}">{e(label or kind.upper())}</span>'


def _secs(v) -> str:
    """A backfilled page has no durations. Render that as unknown, not as an instant run."""
    return f"{v:.0f}s" if v else "—"


# ── log parsers ───────────────────────────────────────────────────────────────
def parse_suite(log: str) -> list[dict]:
    """live_suite's per-case lines, with the indented reason line that may follow."""
    cases: list[dict] = []
    for line in strip(log).splitlines():
        m = SUITE_CASE.match(line)
        if m:
            glyph, cid, detail = m.groups()
            tools = ""
            tm = re.match(r"\[([^\]]+)\]\s*(.*)", detail)
            if tm:
                tools, detail = tm.groups()
            cases.append(
                {"verdict": SUITE_VERDICT[glyph], "id": cid, "tools": tools, "got": detail, "note": ""}
            )
            continue
        n = SUITE_NOTE.match(line)
        if n and cases and not cases[-1]["note"]:
            cases[-1]["note"] = n.group(1)
    return cases


# live_fire.py summary rows:  "  cron/pricebot  CRON  web  —  ✓ FIRED"  followed by
# indented "utterance: “…”" / "response : …" continuation lines.
FIRE_ROW = re.compile(r"^  (\S+)\s{2,}(\w+)\s+(\S+)\s+(\S+)\s+[✓◐·✗–] (FIRED|ARMED|NOFIRE|FAIL|SKIP)$")
FIRE_KIND = {"FIRED": "pass", "ARMED": "xfail", "NOFIRE": "xfail", "FAIL": "fail", "SKIP": "skip"}


def parse_fire(log: str) -> list[dict]:
    """The fire harness's own report block: one row per case, plus its utterance and response."""
    rows: list[dict] = []
    for line in strip(log).splitlines():
        m = FIRE_ROW.match(line)
        if m:
            case, trig, chan, integ, verdict = m.groups()
            rows.append(
                {
                    "case": case,
                    "trigger": trig,
                    "channel": "" if chan == "—" else chan,
                    "integration": "" if integ == "—" else integ,
                    "verdict": verdict,
                    "kind": FIRE_KIND[verdict],
                    "utterance": "",
                    "response": "",
                    "why": "",
                }
            )
            continue
        if not rows:
            continue
        t = line.strip()
        if t.startswith("utterance:"):
            rows[-1]["utterance"] = t.split(":", 1)[1].strip().strip("“”")
        elif t.startswith("response :"):
            rows[-1]["response"] = t.split(":", 1)[1].strip()
        elif (
            t
            and rows[-1]["utterance"]
            and not rows[-1]["why"]
            and not FIRE_ROW.match(line)
            and line.startswith("     ")
            and not t.startswith(("case", "─", "═", "RESULT"))
        ):
            rows[-1]["why"] = t
    return rows


def parse_matrix(log: str) -> list[dict]:
    cells = []
    for line in strip(log).splitlines():
        m = MATRIX_CELL.match(line)
        if m:
            glyph, trigger, sink, detail = m.groups()
            name, kind, _ = MATRIX_MEANING.get(glyph, ("?", "skip", ""))
            cells.append(
                {
                    "glyph": glyph,
                    "outcome": name,
                    "kind": kind,
                    "trigger": trigger.strip(),
                    "sink": sink,
                    "detail": detail.strip(),
                }
            )
    return cells


# ── sections ──────────────────────────────────────────────────────────────────
def _header(prov, st, subs_after) -> str:
    dirty = (
        '<span class="b fail">DIRTY</span> not reproducible from this commit'
        if prov["dirty"]
        else '<span class="b pass">CLEAN</span>'
    )
    integ = ", ".join(f"{k}=<code>{e(v)}</code>" for k, v in (st["integrations"] or {}).items())
    leak = "no leak" if st["subscriptions_before"] == subs_after else '<span class="b fail">LEAKED</span>'
    ghrepo = (
        f'<div><b>GitHub test repo</b><br><code>{e(prov["github_test_repo"])}</code> '
        f'— webhooks created by this run were deleted afterwards</div>'
        if prov["github_test_repo"]
        else '<div><b>GitHub test repo</b><br><span class="g-skip">unset — the github push row '
        'skipped. Set <code>GITHUB_TEST_REPO=owner/repo</code> to arm it.</span></div>'
    )
    warn = (
        ""
        if st["ap_up"]
        else '<div class="warn"><b>Activepieces was DOWN for this run.</b> cron/poll/push could not arm, '
        'and every CONNECT NEEDED below is a false negative. Do not trust the flow rows.</div>'
    )
    return f"""
<h1>CUGA events — test report</h1>
<p class="sub">{e(prov['local'])} &nbsp;·&nbsp; commit <code>{e(prov['commit'])}</code>
  on <code>{e(prov['branch'])}</code></p>
{warn}
<div class="panel"><div class="meta">
  <div><b>When (UTC)</b><br>{e(prov['utc'])}</div>
  <div><b>Commit</b><br><code>{e(prov['commit_full'][:12])}</code> on <code>{e(prov['branch'])}</code></div>
  <div><b>Tree</b><br>{dirty}</div>
  <div><b>Stack</b><br>{st['agents']} agents · AP {'up' if st['ap_up'] else 'DOWN'} ·
      worker <code>{e(st['worker_backend'])}</code></div>
  <div><b>Integrations</b><br>{integ or '—'}</div>
  <div><b>Subscriptions</b><br>{st['subscriptions_before']} before → {subs_after} after ({leak})</div>
  {ghrepo}
</div></div>"""


def _cards(results) -> str:
    tot = {k: sum(r.get(k, 0) for r in results) for k in ("passed", "failed", "xfail", "xpass", "skipped")}
    crashed = sum(1 for r in results if r.get("crashed"))
    cards = [
        ("pass", tot["passed"], "passed"),
        ("fail", tot["failed"], "failed"),
        ("xfail", tot["xfail"], "known gaps"),
        ("xpass", tot["xpass"], "xpass"),
        ("skip", tot["skipped"], "skipped"),
    ]
    if crashed:
        cards.append(("fail", crashed, "harnesses crashed"))
    return (
        '<div class="cards">'
        + "".join(
            f'<div class="card {c}"><div class="v">{n}</div><div class="k">{e(k)}</div></div>'
            for c, n, k in cards
        )
        + "</div>"
    )


def _harness_table(results, log_prefix) -> str:
    rows = []
    for r in results:
        if r.get("skipped_by_request"):
            rows.append(
                f'<tr><td><code>{e(r["key"])}</code></td>'
                f'<td class="u" colspan="8"><em>skipped by request</em></td></tr>'
            )
            continue
        log = f'{log_prefix}{r["key"]}.log'
        if r.get("crashed"):
            rows.append(
                f'<tr><td><code>{e(r["key"])}</code></td><td class="u">{e(r["question"])}</td>'
                f'<td class="v" colspan="5">{badge("crash", "CRASH")}</td>'
                f'<td class="n">{_secs(r["secs"])}</td>'
                f'<td><a href="{e(log)}">log</a></td></tr>'
                f'<tr><td></td><td class="u" colspan="8">{e(r["note"])}</td></tr>'
            )
            continue
        f = f'<b class="g-fail">{r["failed"]}</b>' if r["failed"] else '<span class="g-skip">0</span>'
        rows.append(
            f'<tr><td><code>{e(r["key"])}</code></td><td class="u">{e(r["question"])}</td>'
            f'<td class="n g-pass">{r["passed"]}</td><td class="n">{f}</td>'
            f'<td class="n g-xfail">{r["xfail"] or ""}</td>'
            f'<td class="n g-xpass">{r["xpass"] or ""}</td>'
            f'<td class="n g-skip">{r["skipped"] or ""}</td>'
            f'<td class="n">{_secs(r["secs"])}</td>'
            f'<td><a href="{e(log)}">log</a></td></tr>'
        )
    return f"""
<h2>Harnesses</h2>
<p class="sub">Each answers a different question. Only <b>Fail</b> is worth acting on immediately.</p>
<div class="scroll"><table><thead><tr>
  <th>Harness</th><th>Answers</th><th>Pass</th><th>Fail</th><th>XFail</th><th>XPass</th>
  <th>Skip</th><th>Secs</th><th>Raw</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>"""


DIMENSIONS = ("utterance", "channel", "integration", "trigger")


def _dims_present(rows) -> list:
    """Only render a dimension column where some row in this phase actually fills it in."""
    return [d for d in DIMENSIONS if any((r.get(d) or "").strip() for r in rows)]


def _walkthrough(steps) -> str:
    """The verbose e2e: what a person did, what we expected, what came back."""
    if not steps:
        return ""
    out = [
        """
<h2>End-to-end walkthrough</h2>
<p class="sub">Exactly what a person would do, and exactly what came back. Rows with no verdict are
scene-setting (posting the message), not assertions — only ✓/✗ rows are checked. The
<b>utterance</b>, <b>channel</b>, <b>integration</b> and <b>trigger</b> columns appear in whichever
phases they mean something.</p>"""
    ]
    for phase in dict.fromkeys(s["phase"] for s in steps):
        prows = [x for x in steps if x["phase"] == phase]
        dims = _dims_present(prows)
        out.append(f"<h3>{e(phase)}</h3>")
        heads = "".join(f"<th>{d.title()}</th>" for d in dims)
        out.append(
            f'<div class="scroll"><table><thead><tr><th>Surface</th>{heads}<th>Who</th>'
            "<th>Does what</th><th>Expected</th><th>Actually got</th><th></th>"
            "</tr></thead><tbody>"
        )
        for s in prows:
            mark = (
                ""
                if s["ok"] is None
                else ('<span class="g-pass">✓</span>' if s["ok"] else '<span class="g-fail">✗</span>')
            )
            note = f'<span class="sn">{e(s["note"])}</span>' if s["note"] else ""
            cells = ""
            for d in dims:
                v = (s.get(d) or "").strip()
                if not v:
                    cells += '<td class="u">—</td>'
                elif d == "utterance":
                    cells += f'<td class="utt">“{e(v)}”</td>'
                else:
                    cells += f'<td class="u"><code>{e(v)}</code></td>'
            out.append(
                f'<tr><td><code>{e(s["surface"])}</code></td>{cells}'
                f'<td>{e(s["actor"])}</td>'
                f'<td>{e(s["action"])}{note}</td><td class="u">{e(s["expect"])}</td>'
                f'<td class="got">{e(s["got"] or "—")}</td><td class="v">{mark}</td></tr>'
            )
        out.append("</tbody></table></div>")
    return "".join(out)


def _cases(title, blurb, cases) -> str:
    if not cases:
        return ""
    rows = []
    for c in cases:
        note = f'<span class="sn">{e(c["note"])}</span>' if c["note"] else ""
        rows.append(
            f'<tr><td><code>{e(c["id"])}</code></td>'
            f'<td class="v">{badge(c["verdict"])}</td>'
            f'<td class="u">{e(c["tools"]) or "—"}</td>'
            f'<td class="got">{e(c["got"])}{note}</td></tr>'
        )
    return f"""
<h2>{e(title)}</h2>
<p class="sub">{blurb}</p>
<div class="scroll"><table><thead><tr><th>Case</th><th>Verdict</th><th>MCP servers used</th>
  <th>What the agent actually said</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>"""


def _fire(rows) -> str:
    """The only table that answers "did it actually run?" — one row per fired case."""
    if not rows:
        return ""

    def row(r) -> str:
        utt = f'“{e(r["utterance"])}”' if r["utterance"] else "—"
        why = f'<span class="sn">{e(r["why"])}</span>' if r["why"] else ""
        return (
            f'<tr><td><code>{e(r["case"])}</code></td>'
            f'<td class="utt">{utt}</td>'
            f'<td class="u"><code>{e(r["channel"] or "—")}</code></td>'
            f'<td class="u"><code>{e(r["integration"] or "—")}</code></td>'
            f'<td class="u"><code>{e(r["trigger"])}</code></td>'
            f'<td class="v">{badge(r["kind"], r["verdict"])}</td>'
            f'<td class="got">{e(r["response"]) or "—"}{why}</td></tr>'
        )

    body = "".join(row(r) for r in rows)
    return f"""
<h2>Did the flow actually fire?</h2>
<p class="sub">Every other section stops at <em>armed</em>. This one types the utterance, waits for the
trigger to fire for real, and reads back the answer the agent produced. Cron and poll cases arm a
genuine one-minute Activepieces schedule and wait for a tick.</p>
<div class="legend">
  <span><b class="g-pass">FIRED</b> armed → fired → answered</span>
  <span><b class="g-xfail">ARMED</b> exists and is enabled, but nothing fired in the budget</span>
  <span><b class="g-xfail">NOFIRE</b> deliberately not fired (it would mutate a real repo or inbox)</span>
  <span><b class="g-fail">FAIL</b> should have fired and didn't, or errored</span>
  <span><b class="g-skip">SKIP</b> surface not configured</span>
</div>
<div class="scroll"><table><thead><tr><th>Case</th><th>Utterance</th><th>Channel</th>
  <th>Integration</th><th>Trigger</th><th>Verdict</th><th>What came back</th>
</tr></thead><tbody>{body}</tbody></table></div>
<div class="callout"><b>ARMED and NOFIRE are not passes.</b> They mean a flow exists and no answer was
observed. Only <b>FIRED</b> proves the loop closes.</div>"""


def _matrix(cells) -> str:
    if not cells:
        return ""
    triggers = list(dict.fromkeys(c["trigger"] for c in cells))
    sinks = list(dict.fromkeys(c["sink"] for c in cells))
    by = {(c["trigger"], c["sink"]): c for c in cells}
    head = "".join(f"<th>{e(s)}</th>" for s in sinks)
    rows = []
    for t in triggers:
        tds = []
        for s in sinks:
            c = by.get((t, s))
            if not c:
                tds.append('<td class="v g-skip">·</td>')
            else:
                tds.append(
                    f'<td class="v g-{c["kind"]}" title="{e(c["outcome"])}: {e(c["detail"])}">'
                    f'{c["glyph"]}</td>'
                )
        rows.append(f'<tr><td><code>{e(t)}</code></td>{"".join(tds)}</tr>')
    legend = " ".join(
        f'<span><b class="g-{k}">{g}</b> {e(n)}</span>' for g, (n, k, _) in MATRIX_MEANING.items()
    )
    detail = "".join(
        f'<tr><td><code>{e(c["trigger"])}</code></td><td><code>{e(c["sink"])}</code></td>'
        f'<td class="v">{badge(c["kind"], c["outcome"])}</td>'
        f'<td class="got">{e(c["detail"]) or "—"}</td></tr>'
        for c in cells
    )
    return f"""
<h2>Trigger × sink matrix</h2>
<p class="sub">Every trigger mode against every delivery channel. Hover a cell for its detail.
<code>?</code> and <code>⚠</code> are <em>correct</em> behaviours — the concierge asking for something
it genuinely needs. Only <code>✗</code> fails the run.</p>
<div class="legend">{legend}</div>
<div class="scroll"><table><thead><tr><th>Trigger</th>{head}</tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>
<details><summary>Every cell, with its detail</summary>
<div class="scroll"><table><thead><tr><th>Trigger</th><th>Sink</th><th>Outcome</th><th>Detail</th>
</tr></thead><tbody>{detail}</tbody></table></div></details>"""


def _footer(results, outdir) -> str:
    notes = [r for r in results if r.get("note")]
    n = "".join(f'<li><code>{e(r["key"])}</code> — {e(r["note"])}</li>' for r in notes)
    n = f"<h2>How to read this</h2><ul>{n}</ul>" if notes else ""
    return f"""{n}
<h2>Verdict vocabulary</h2>
<div class="panel"><table><tbody>
<tr><td>{badge('fail')}</td><td>Expected to work, broke. The only thing worth acting on immediately.</td></tr>
<tr><td>{badge('xfail')}</td><td>A known gap, with its reason printed next to the case. Not a regression.</td></tr>
<tr><td>{badge('xpass')}</td><td>A known gap started passing. Re-sample before believing it, then delete the expectation.</td></tr>
<tr><td>{badge('skip')}</td><td>Surface not configured. Never counted as a pass.</td></tr>
<tr><td>{badge('crash', 'CRASH')}</td><td>The harness died before reporting. Silence is not success.</td></tr>
</tbody></table></div>
<div class="note">Only <code>live_suite</code>, <code>live_e2e</code> and <code>live_matrix</code> verify
that an armed flow <b>really exists in Activepieces</b>. A bare <code>ap_flow_id</code> proves nothing:
<code>find_or_create_flow</code> de-duplicates on <code>dedup_key</code> without re-checking that the
flow survived (<code>concierge.py:285-289</code>).</div>
<div class="warn"><b>None of these harnesses fire real data through an armed watcher.</b> They prove a
flow is created correctly, not that it behaves correctly when a real event lands. For that, run
<code>live_gmail_e2e.py</code>, <code>live_box_e2e.py</code> or <code>live_github_e2e.py</code>.</div>
<div class="foot">Generated by <code>events/scripts/report_html.py</code> from
<code>results/{e(outdir.name)}/</code>. Regenerate with <code>make test-report</code> — never hand-edit,
the next run overwrites it.</div>"""


def render_html(prov, st, subs_after, results, outdir, steps, logs, log_prefix="") -> str:
    """`log_prefix` makes the raw-log links resolve from wherever the page is written:
    "" next to the logs in the run dir, "runs/<stamp>/" for the copy at results/index.html."""
    suite_now = parse_suite(logs.get("now", ""))
    suite_flows = parse_suite(logs.get("flows", ""))
    cells = parse_matrix(logs.get("matrix", ""))
    fire = parse_fire(logs.get("fire", ""))
    body = "".join(
        [
            _header(prov, st, subs_after),
            _cards(results),
            _harness_table(results, log_prefix),
            _walkthrough(steps),
            _fire(fire),
            _cases(
                "Agents answering NOW",
                "Every seeded agent, invoked directly on <code>/invoke</code> "
                "with no channel and no concierge. The verdict asserts on <code>meta.mcp</code> — that "
                "the agent reached the tool it claims to use, not merely that it produced prose.",
                suite_now,
            ),
            _cases(
                "English sentence → Activepieces flow",
                "cron, poll and push. The verdict is whether the "
                "utterance armed the right kind of flow, delivered to the channel it came from.",
                suite_flows,
            ),
            _matrix(cells),
            _footer(results, outdir),
        ]
    )
    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>CUGA events — test report {e(prov["commit"])}</title>'
        f"<style>{CSS}</style></head><body>"
        f'<button class="toggle" title="light / dark">◐</button>'
        f'<div class="wrap">{body}</div>'
        f"<script>{TOGGLE_JS}</script></body></html>"
    )
