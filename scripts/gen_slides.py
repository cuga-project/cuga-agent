#!/usr/bin/env python
"""Generate events_docs/slides.html — the event-driven-agents deck — from the code itself.

Sources of truth (nothing in the deck is hand-typed twice):
  * src/cuga/backend/events/triggers.py — the registry: every (integration, event), backend, slots
  * src/cuga/backend/events/catalog.py  — the example utterances shown per trigger

    python scripts/gen_slides.py            # rewrite in place
    python scripts/gen_slides.py --check    # exit 1 if out of date (used by the consistency test)

The deck is a single self-contained HTML file: ← → / space to navigate, `p` for print view
(all slides), deep-linkable (#7). Served in the Studio via GET /api/events/docs/slides.
"""
from __future__ import annotations

import html
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "events_docs" / "slides.html"

CHANNELS = [
    ("web", "the built-in chat — always on, zero setup"),
    ("slack", "direct backend: CUGA receives the Events API itself (signed HMAC)"),
    ("discord", "direct backend: CUGA holds the Gateway websocket"),
    ("telegram", "bot long-poll via the events layer"),
]

FIRE_LABEL = {"synth": "machine-fireable", "real": "fireable with a real local action",
              "manual": "needs a real external event"}
FIRE_BADGE = {"synth": "synth", "real": "real", "manual": "manual"}


def _sources():
    sys.path.insert(0, str(ROOT))
    from src.cuga.backend.events import triggers, catalog  # noqa: E402
    return triggers, catalog


def _bench_stats():
    """The NL→Flow benchmark scorecard, computed from the REAL bench — the deck can't overstate."""
    sys.path.insert(0, str(ROOT / "tests" / "events"))
    from src.cuga.backend.events import flowspec  # noqa: E402
    from test_flowspec_bench import BENCH  # noqa: E402
    push = [(u, w) for u, w in BENCH if w["kind"] == "push" and w["source"]]
    high = right = 0
    for u, w in push:
        s = flowspec.resolve(u)
        if s.confidence == "high":
            high += 1
            right += (s.source, s.event) == (w["source"], w["event"])
    return {"cases": len(BENCH), "push": len(push), "high": high, "right": right}


def _esc(s) -> str:
    return html.escape(str(s or ""))


def _examples_by_row(tr, catalog) -> dict[tuple[str, str], list[str]]:
    """catalog example utterances keyed by the registry row they exercise (best-effort match on
    the app + a normalized ap_trigger/event name; unmatched examples stay on the app pool)."""
    out: dict[tuple[str, str], list[str]] = {}
    for e in catalog.EXAMPLES:
        app = (e.get("integration") or "").lower()
        raw = (e.get("ap_trigger") or "").lower().replace("-", "_")
        if not app or not raw:
            continue
        row = tr.get(app, raw)
        if row is None:  # the catalog sometimes carries the AP piece trigger name instead
            row = next((t for t in tr.events_for(app) if t.ap_trigger == raw), None)
        if row is not None and e.get("utterance"):
            out.setdefault(row.key, []).append(e["utterance"])
    return out


def _slide(title: str, body: str, klass: str = "") -> str:
    return f'<section class="slide {klass}"><h2>{title}</h2>{body}</section>'


def build() -> str:
    tr, catalog = _sources()
    rows = tr.rows()
    apps = tr.apps()
    by_row = _examples_by_row(tr, catalog)
    n_ap = sum(1 for t in rows if t.backend == "ap")
    n_direct = len(rows) - n_ap
    starred = [e for e in catalog.EXAMPLES if e.get("star") and e.get("utterance")]

    slides: list[str] = []

    # 1 — title
    slides.append(f"""
<section class="slide title-slide">
  <div class="kicker">CUGA · the events layer</div>
  <h1>Event-Driven Agents</h1>
  <p class="sub">Agents that don't wait to be asked — they watch, and act.</p>
  <div class="stats">
    <div><b>{len(apps)}</b><span>integrations</span></div>
    <div><b>{len(rows)}</b><span>triggers</span></div>
    <div><b>{len(CHANNELS)}</b><span>channels</span></div>
    <div><b>{len(catalog.EXAMPLES)}</b><span>examples</span></div>
  </div>
</section>""")

    # 2 — why (the pitch)
    slides.append(_slide("Why — the human shouldn't be the trigger", f"""
<p class="big">Today most agents only act when a human asks. Event-driven agents act when
   <b>time</b> says so or when <b>the world changes</b> — a tool you operate becomes a teammate
   that works while you sleep.</p>
<ul class="tight">
  <li><b>Request-response caps the value</b> — nothing happens unless someone remembers to ask,
      and insights expire when the chat closes.</li>
  <li><b>The market is flooded with smart replies.</b> The durable moat is <b>autonomy</b> —
      agents that watch, wait, and act.</li>
  <li>The customer ask is shifting from <em>“answer my question”</em> to
      <em>“keep an eye on this and tell me when it matters.”</em></li>
</ul>
<table class="tbl">
  <tr><th>Wake source</th><th>Example</th><th>Status</th></tr>
  <tr><td class="k">You ask it</td><td class="ex">“what is the price of bitcoin?”</td>
      <td><span class="badge fire-synth">live — 4 channels</span></td></tr>
  <tr><td class="k">The clock</td><td class="ex">“every morning at 8, send me the weather”</td>
      <td><span class="badge fire-synth">live — CRON</span></td></tr>
  <tr><td class="k">The world changes</td><td class="ex">“ping me only when this PR / price / file
      changes”</td>
      <td><span class="badge fire-synth">live — {len(rows)} triggers, POLL + PUSH</span></td></tr>
</table>"""))

    # 3 — the idea
    slides.append(_slide("From ask → answer to watch → act", """
<div class="cols">
  <div class="col">
    <h3>A chat agent</h3>
    <p>You ask, it answers, the conversation ends. Nothing happens unless a human types.</p>
    <p class="dim">“summarize this PR” — once.</p>
  </div>
  <div class="col accent">
    <h3>An event-driven agent</h3>
    <p>You arm a <b>standing flow</b> once. From then on the <em>world</em> triggers the agent —
       a PR opens, an email lands, a reaction is added — and the answer is delivered where you asked
       from.</p>
    <p class="dim">“review every new PR on my repo” — forever.</p>
  </div>
</div>
<p>One sentence in any channel is enough: the concierge turns natural language into an armed,
   standing flow — no console, no YAML.</p>"""))

    # 4 — the three kinds, with real catalog examples per kind (starred first)
    kind_ex: dict[str, list[str]] = {"cron": [], "poll": [], "push": []}
    for e in sorted(catalog.EXAMPLES, key=lambda x: not x.get("star")):
        k = e.get("trigger")
        if k in kind_ex and e.get("utterance") and len(kind_ex[k]) < 3:
            kind_ex[k].append(e["utterance"])

    def _exs(k):
        return "".join(f"<div class='ex'>“{_esc(u)}”</div>" for u in kind_ex[k])

    slides.append(_slide("Three kinds of standing flow", f"""
<table class="tbl kinds">
  <tr><th>Kind</th><th>Fires when…</th><th>Say it like this</th></tr>
  <tr><td class="k cron">CRON</td><td>a schedule ticks — no external state, pure time</td>
      <td>{_exs('cron')}</td></tr>
  <tr><td class="k poll">POLL</td><td>a watched value changes — CUGA samples it and compares
      against the last sample (a price, a feed, a page)</td>
      <td>{_exs('poll')}</td></tr>
  <tr><td class="k push">PUSH</td><td>an external system emits an event — PR opened, email received,
      reaction added, webhook called. No sampling: the world calls us</td>
      <td>{_exs('push')}</td></tr>
</table>
<p>PUSH is where the trigger registry lives — everything on the next slides is a PUSH trigger.</p>"""))

    # 4 — architecture
    svg = (ROOT / "events_docs" / "architecture" / "system.svg")
    svg_markup = svg.read_text() if svg.exists() else "<p class='dim'>architecture/system.svg</p>"
    slides.append(_slide("Architecture — one seam, two backends", f"""
<div class="arch">{svg_markup}</div>
<ul class="tight">
  <li><b>/invoke</b> is the single seam: every trigger, from every backend, arrives as the same
      envelope <code>{{agent, source, event}}</code>.</li>
  <li><b>AP backend</b> — an Activepieces flow holds the piece trigger; AP holds the credentials.
      The agent never sees a token.</li>
  <li><b>Direct backend</b> — CUGA already receives Slack Events / the Discord Gateway, so a watcher
      is just a subscription row. No AP flow at all.</li>
</ul>"""))

    # 5 — the registry
    slides.append(_slide("The trigger registry — one table rules everything", f"""
<p><code>triggers.py</code> holds <b>{len(rows)} triggers</b> — one row per
   <code>(integration, event)</code>: the AP piece trigger <em>or</em> the direct event kind, the
   curated payload map, the config slots, the classifier phrases, and a synthetic fire payload.</p>
<ul class="tight">
  <li>flow building, classification, arm-time validation, the docs and the tests all
      <b>derive</b> from it — nothing is wired twice</li>
  <li>an unknown trigger <b>fails loudly at build time</b> (the old code silently armed a flow that
      could never publish)</li>
  <li>{n_ap} triggers run via Activepieces · {n_direct} run direct</li>
</ul>
<p class="dim">Adding a trigger = adding one row (+ an agent that declares it). The parametrized test
   suite picks it up automatically.</p>"""))

    # 6 — channels
    ch_rows = "".join(f"<tr><td class='k chan'>{_esc(n)}</td><td>{_esc(d)}</td></tr>"
                      for n, d in CHANNELS)
    slides.append(_slide("Channels — where you talk to it (and where answers land)", f"""
<table class="tbl">{ch_rows}</table>
<p>The <b>sink follows the origin</b>: arm a watcher from Slack and the answers arrive in Slack;
   arm it from web chat and they ride back over HTTP. Same agent, same flow.</p>"""))

    # 7 — integrations overview
    over = []
    for app in apps:
        ts = tr.events_for(app)
        backends = sorted({t.backend for t in ts})
        b = " + ".join("Activepieces" if x == "ap" else "direct" for x in backends)
        default = next((t.event for t in ts if t.default), ts[0].event)
        over.append(f"<tr><td class='k'>{_esc(app)}</td><td>{len(ts)}</td>"
                    f"<td>{_esc(b)}</td><td><code>{_esc(default)}</code></td></tr>")
    slides.append(_slide(f"Integrations — {len(apps)} apps, {len(rows)} triggers", f"""
<table class="tbl">
  <tr><th>Integration</th><th>Triggers</th><th>Backend</th><th>Default trigger</th></tr>
  {''.join(over)}
</table>"""))

    # 8..N — one slide per integration
    for app in apps:
        ts = sorted(tr.events_for(app), key=lambda t: (not t.default, t.event))
        trs = []
        for t in ts:
            req = [n for n in t.slots if tr.SLOTS[n].required]
            opt = [n for n in t.slots if not tr.SLOTS[n].required]
            slot = (" · ".join([f"needs <code>{_esc(n)}</code>" for n in req]
                               + [f"<span class='dim'>{_esc(n)}?</span>" for n in opt]))
            exs = by_row.get(t.key, [])[:1]
            ex = f"<div class='ex'>“{_esc(exs[0])}”</div>" if exs else ""
            trs.append(
                f"<tr><td class='k'><code>{_esc(t.event)}</code>{' ★' if t.default else ''}</td>"
                f"<td>{_esc(t.title)}{ex}</td>"
                f"<td><span class='badge {t.backend}'>{'AP' if t.backend == 'ap' else 'direct'}</span> "
                f"<span class='badge fire-{t.fire}' title='{_esc(FIRE_LABEL[t.fire])}'>"
                f"{FIRE_BADGE[t.fire]}</span></td>"
                f"<td class='slots'>{slot}</td></tr>")
        slides.append(_slide(f"{app} — {len(ts)} trigger{'s' if len(ts) > 1 else ''}", f"""
<table class="tbl triggers">
  <tr><th>Trigger</th><th>What it watches (+ say it like this)</th><th>Backend · fire</th><th>Config</th></tr>
  {''.join(trs)}
</table>""", klass="integration"))

    # creating a flow
    slides.append(_slide("Creating a flow — one sentence, or one command", """
<div class="cols">
  <div class="col">
    <h3>Natural language (any channel)</h3>
    <p class="ex">“when a PR is opened on psf/requests, review it and post a summary”</p>
    <p>The concierge classifies the sentence → resolves the trigger in the registry → validates the
       config (asks for missing slots like the repo) → arms the flow. Re-asking <b>reuses</b> the
       existing flow (dedup), never duplicates it.</p>
  </div>
  <div class="col">
    <h3>Slash commands</h3>
    <p><code>/automate &lt;what&gt;</code> — the router picks cron / poll / push for you.<br>
       <code>/cron</code> · <code>/poll</code> · <code>/push</code> · <code>/watch</code> — force a
       specific kind.</p>
    <p><code>POST /api/events/hook/{name}</code> — external systems fire a webhook; <b>routed</b>
       mode picks the agent by capability, like chat does.</p>
  </div>
</div>"""))

    # NL→Flow — how it's engineered
    b = _bench_stats()
    slides.append(_slide("NL→Flow — engineered, not vibes", f"""
<p><b>The contract: a sentence becomes exactly the right flow, or a question — never silently the
wrong flow.</b> Two fuzzy hops, everything else deterministic:</p>
<div class="cols">
  <div class="col accent">
    <h3>1 · A deterministic pre-router first</h3>
    <p>Registry-generated phrases classify the trigger; slots (repo, label, folder…) are extracted
       from the sentence. <b>HIGH confidence</b> demands a standing-flow marker, ONE distinct app,
       and no foreign vocabulary — then it arms <em>without an LLM call</em>, instantly.</p>
    <p><b>Ask-till-legit:</b> a missing required slot becomes ONE question; the next message fills
       the blank (<span class="ex">“acme/api”</span> → armed). A topic-change reply is never
       crammed into the slot.</p>
  </div>
  <div class="col">
    <h3>2 · The LLM for genuine ambiguity</h3>
    <p>Anything the pre-router won't claim goes to the concierge LLM, whose prompt carries the
       registry's full trigger vocabulary. <b>Its proposal still passes the same validation gate</b>
       — an unknown trigger or missing slot comes back as a question, never a broken flow.
       Both doors arm through one code path: connect gate → dedup (DB unique index) → build.</p>
  </div>
</div>
<p><b>Benchmarked in CI:</b> {b['cases']} hand-labelled cases (utterance → expected FlowSpec) —
   fast-path {b['high']}/{b['push']} push cases ({100 * b['high'] // max(b['push'], 1)}%),
   correct-at-high {b['right']}/{b['high']}, gated on <b>zero wrong-at-high</b>. It caught two real
   bugs on its first run. Full walkthrough: <code>events_docs/nl_to_flow.html</code>.</p>"""))

    # the receipts — concrete, verifiable engineering wins
    slides.append(_slide("The receipts — why you can trust this", f"""
<ul class="tight">
  <li><b>One registry, zero drift.</b> All {len(rows)} triggers live in one table; flows,
      classifier, validation, docs, this deck and the tests <em>derive</em> from it — and five
      golden gates fail the build if any generated page goes stale.</li>
  <li><b>Live-fired, not just armed.</b> All 14 GitHub triggers arm as real AP flows with real repo
      webhooks and fire with real agent answers (91s harness). A real human's 🐛 reaction in Slack
      fired the incident agent end to end. The harnesses distinguish ARMED from FIRED — and say so.</li>
  <li><b>We read the vendor's compiled source.</b> Two bugs in Activepieces' own GitHub piece
      (sample data its own filters would discard) — found by reading the bundled JS, pinned by
      tests so an upgrade can't silently regress us.</li>
  <li><b>Security by construction.</b> OAuth tokens live only in AP's encrypted store; CUGA passes
      connection <em>names</em>. OAuth state is HMAC-signed and expiring. Every CUGA-held secret
      resolves through <code>vault:// aws:// db://</code> URIs.</li>
  <li><b>Races are refereed by the database.</b> Flow identity is a canonical dedup key under a
      unique index — two simultaneous asks produce one flow, deterministically.</li>
  <li><b>Honest verdicts everywhere.</b> The test vocabulary separates PASS / XFAIL / ARMED /
      NOFIRE / SKIP — a green that means “exists” is never sold as “works”.</li>
</ul>"""))

    # credentials
    slides.append(_slide("Credentials — the agent never holds a token", """
<ul class="tight">
  <li><b>Activepieces is the vault</b>: OAuth tokens live encrypted in AP's own store; flows resolve
      them inside AP's sandbox. CUGA passes a connection <em>name</em>, never a secret.</li>
  <li><b>Per-user vs shared</b>: each integration on an agent declares its credential ownership —
      per-user (everyone logs in) or shared (one service account).</li>
  <li><b>The secret seam</b>: every CUGA-held credential (bot tokens, AP password, gateway token)
      resolves through <code>vault:// aws:// db:// env://</code> URIs — plaintext still works in dev.</li>
  <li><b>Signed OAuth state</b>: connect callbacks carry an HMAC-signed, expiring state — a forged or
      replayed callback is a hard reject.</li>
</ul>"""))

    # tested
    slides.append(_slide("Proven end-to-end — live, not mocked", """
<ul class="tight">
  <li><b>Offline gate</b> (<code>make test</code>) — every registry row parametrized: flow building,
      classification (a labelled utterance per trigger), validation, dedup, signed state.</li>
  <li><b><code>live_github_triggers.py</code></b> — arms all 14 GitHub triggers as real AP flows with
      real repo webhooks, fires each synthetically, asserts a real agent answer, cleans up.</li>
  <li><b><code>live_direct_watchers.py</code></b> — fires HMAC-signed Slack Events API callbacks
      through the same verification a genuine delivery passes.</li>
  <li><b>Real events</b> — a real Slack reaction, a real Box upload and comment, real Gmail arms:
      verified through the full loop, answer delivered back into the channel.</li>
  <li><b><code>make test-fire</code></b> — the only harness that waits for a genuine schedule tick:
      <em>ARMED is not FIRED</em>, and the verdicts say which.</li>
</ul>"""))

    # roadmap (mirrors events_docs/ROADMAP.md — that file is the source of truth)
    slides.append(_slide("Roadmap — where this goes next", """
<div class="cols">
  <div class="col accent">
    <h3>Finish the MVP (P3, ~75%)</h3>
    <ul class="tight">
      <li><b>NL→flow rigor</b> — shipped: a typed FlowSpec + deterministic pre-router (arm
          without the LLM when unambiguous, <em>ask till legit</em> when a slot is missing) and a
          47-case CI benchmark gated on zero-wrong-at-high. Next: the LLM seam scored the same
          way + a concierge model bake-off.</li>
      <li><b>Webhook-OUT</b> — deliver an answer to any HTTP endpoint → flow-to-flow chaining.</li>
      <li><b>Email as a sink</b> — “…email me the brief”.</li>
    </ul>
  </div>
  <div class="col">
    <h3>Then breadth (P4) → cloud (P5)</h3>
    <ul class="tight">
      <li><b>P4</b> — WhatsApp · email-as-a-channel · Google Calendar · Drive/Sheets · Notion/Jira ·
          RSS. Each is <em>one afternoon</em>: registry rows + an OAuth entry — the gates force the
          rest, the Studio and this deck update themselves.</li>
      <li><b>P5</b> — multi-tenant cloud: real isolation, IdP/OIDC, a secrets vault replacing
          <code>.env</code>, observability, scale.</li>
    </ul>
  </div>
</div>
<p class="dim">Full sequenced list: events_docs/ROADMAP.md · phase definitions: PHASES.md</p>"""))

    # examples closer
    ex_lis = "".join(f"<li>“{_esc(e['utterance'])}”<span class='dim'> — {_esc(e.get('agent', ''))}"
                     f"</span></li>" for e in starred[:8])
    slides.append(_slide("Try it — the examples board", f"""
<ul class="tight examples">{ex_lis}</ul>
<p class="dim">All {len(catalog.EXAMPLES)} examples (with feasibility notes) live on the examples
   board — <code>events_docs/api/examples.html</code>, also in the Studio's Examples tab. This deck
   and that board are both generated from the code, so neither can drift.</p>"""))

    body = "\n".join(slides)
    return f"""<!DOCTYPE html>
<!-- GENERATED by scripts/gen_slides.py — do not hand-edit. Sources: triggers.py + catalog.py -->
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Event-Driven Agents — CUGA</title>
<style>
  :root {{ --bg:#0f1117; --card:#171a23; --ink:#e8eaf0; --dim:#8b93a7; --line:#262b38;
           --acc:#4f8cff; --cron:#a56eff; --poll:#08bdba; --push:#ff7eb6; --ok:#42be65; }}
  * {{ box-sizing: border-box; margin: 0; }}
  body {{ background: var(--bg); color: var(--ink);
         font: 16px/1.55 -apple-system, "IBM Plex Sans", Segoe UI, sans-serif; }}
  .slide {{ display: none; min-height: 100vh; padding: 7vh 8vw; flex-direction: column;
            justify-content: center; }}
  .slide.active {{ display: flex; }}
  body.print .slide {{ display: flex; min-height: auto; padding: 40px 8vw;
                       border-bottom: 1px solid var(--line); page-break-after: always; }}
  h1 {{ font-size: clamp(2.4rem, 6vw, 4.2rem); letter-spacing: -.02em; }}
  h2 {{ font-size: clamp(1.5rem, 3.4vw, 2.2rem); margin-bottom: .9em; letter-spacing: -.01em; }}
  h3 {{ margin-bottom: .4em; }}
  p {{ margin: .5em 0; max-width: 62rem; }}
  code {{ background: var(--card); border: 1px solid var(--line); border-radius: 5px;
          padding: .08em .35em; font-size: .92em; }}
  .kicker {{ color: var(--acc); text-transform: uppercase; letter-spacing: .18em;
             font-size: .8rem; margin-bottom: 1em; }}
  .sub {{ color: var(--dim); font-size: 1.25rem; }}
  .dim {{ color: var(--dim); }}
  .big {{ font-size: 1.2rem; max-width: 58rem; }}
  .stats {{ display: flex; gap: 3.5rem; margin-top: 3rem; }}
  .stats b {{ display: block; font-size: 2.6rem; color: var(--acc); }}
  .stats span {{ color: var(--dim); }}
  .cols {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1.2rem; margin: .8em 0; }}
  .col {{ background: var(--card); border: 1px solid var(--line); border-radius: 10px;
          padding: 1.1rem 1.3rem; }}
  .col.accent {{ border-color: var(--acc); }}
  .tbl {{ border-collapse: collapse; width: 100%; max-width: 68rem; margin: .4em 0; }}
  .tbl th {{ text-align: left; color: var(--dim); font-weight: 600; font-size: .82rem;
             text-transform: uppercase; letter-spacing: .06em; }}
  .tbl th, .tbl td {{ padding: .45em .8em; border-bottom: 1px solid var(--line);
                      vertical-align: top; }}
  .tbl .k {{ white-space: nowrap; font-weight: 600; }}
  .kinds .cron {{ color: var(--cron); }} .kinds .poll {{ color: var(--poll); }}
  .kinds .push {{ color: var(--push); }} .chan {{ color: var(--acc); }}
  .ex {{ color: var(--poll); font-style: italic; }}
  .triggers .ex {{ font-size: .88em; margin-top: .15em; }}
  .triggers td {{ font-size: .95em; }}
  .slots {{ font-size: .88em; white-space: nowrap; }}
  .badge {{ display: inline-block; border-radius: 99px; padding: .05em .6em; font-size: .74rem;
            border: 1px solid var(--line); white-space: nowrap; }}
  .badge.ap {{ color: var(--acc); border-color: var(--acc); }}
  .badge.direct {{ color: var(--poll); border-color: var(--poll); }}
  .badge.fire-synth {{ color: var(--ok); border-color: var(--ok); }}
  .badge.fire-real {{ color: var(--push); border-color: var(--push); }}
  .badge.fire-manual {{ color: var(--dim); }}
  ul.tight {{ margin: .4em 0 .4em 1.2em; max-width: 64rem; }}
  ul.tight li {{ margin: .45em 0; }}
  ul.examples li {{ font-style: italic; }}
  .arch {{ background: #fff; border-radius: 10px; padding: 12px; max-width: 62rem;
           margin: .4em 0 1em; }}
  .arch svg {{ width: 100%; height: auto; display: block; }}
  #hud {{ position: fixed; bottom: 14px; right: 18px; color: var(--dim); font-size: .85rem;
          user-select: none; }}
  #bar {{ position: fixed; top: 0; left: 0; height: 3px; background: var(--acc);
          transition: width .2s; }}
  body.print #hud, body.print #bar {{ display: none; }}
  @media print {{ body {{ background: #fff; color: #111; }} }}
</style></head>
<body>
{body}
<div id="bar"></div><div id="hud"></div>
<script>
  const slides = [...document.querySelectorAll('.slide')], n = slides.length;
  let i = Math.min(Math.max((parseInt(location.hash.slice(1)) || 1) - 1, 0), n - 1);
  function show() {{
    slides.forEach((s, j) => s.classList.toggle('active', j === i));
    document.getElementById('hud').textContent = (i + 1) + ' / ' + n;
    document.getElementById('bar').style.width = (100 * (i + 1) / n) + '%';
    history.replaceState(null, '', '#' + (i + 1));
  }}
  addEventListener('keydown', (e) => {{
    if (e.key === 'ArrowRight' || e.key === ' ' || e.key === 'PageDown') {{ i = Math.min(i + 1, n - 1); show(); }}
    if (e.key === 'ArrowLeft' || e.key === 'PageUp') {{ i = Math.max(i - 1, 0); show(); }}
    if (e.key === 'Home') {{ i = 0; show(); }}
    if (e.key === 'End') {{ i = n - 1; show(); }}
    if (e.key === 'p') {{ document.body.classList.toggle('print'); }}
  }});
  addEventListener('click', (e) => {{
    if (document.body.classList.contains('print') || e.target.closest('a, details, summary')) return;
    i = (e.clientX > innerWidth / 3) ? Math.min(i + 1, n - 1) : Math.max(i - 1, 0); show();
  }});
  show();
</script>
</body></html>
"""


def main() -> int:
    check = "--check" in sys.argv
    fresh = build()
    current = OUT.read_text() if OUT.exists() else ""
    if current == fresh:
        print(f"✓ slides.html up to date ({fresh.count('class=\"slide')} slides)")
        return 0
    if check:
        print("✗ slides.html is stale — run: python scripts/gen_slides.py", file=sys.stderr)
        return 1
    OUT.write_text(fresh)
    print(f"✓ wrote events_docs/slides.html — {fresh.count('class=\"slide')} slides "
          f"from triggers.py + catalog.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
