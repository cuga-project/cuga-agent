"""LIVE suite — the full behavioural sweep: NOW → channels → CRON → POLL → PUSH.

This is the "does the product work" test, as opposed to `live_e2e.py` ("does each surface work once")
and `live_matrix.py` ("is every mode × sink plumbed"). It exists to be run repeatedly while the known
gaps get fixed, so it distinguishes four outcomes rather than two:

    PASS   it worked, and we expected it to
    FAIL   it broke, and we expected it to work          ← the only thing that fails the run
    XFAIL  it broke, and we KNEW it would (a logged gap) ← expected; the reason is printed
    XPASS  it worked, but we expected it to break        ← a gap just closed. Update `expect`!

XPASS is deliberately loud. A suite whose expectations silently drift out of date is worse than no
suite, so when a known gap starts working the run tells you to delete the expectation.

PHASES
  now       every seeded agent, invoked DIRECTLY (no concierge, no channel) via the runonce envelope
  channels  the same probe over slack / discord / telegram, incl. a real Slack round trip
  cron      scheduled flows, mostly from the web endpoint, some armed from a channel
  poll      watch-on-change flows
  push      box · gmail · github integration watchers, plus the generic inbound webhook

THE NOW ENVELOPE. `source.type` accepts only `channel | integration | time`, so a channel-less direct
call to a named agent is `type: "time"` + `kind: "runonce"`. The response carries `meta.mcp` — the MCP
servers the agent actually reached — which we assert on, because "the answer contained a digit" does
not prove the agent used its tools rather than the model's memory.

Run:  make test-suite
      make test-suite ARGS="--only now"
      GITHUB_TEST_REPO=owner/repo make test-suite      # arms the github push row (creates a webhook)
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from live_e2e import (  # noqa: E402
    BASE, RUN, connect_needed_app, env, flow_alive, gw_headers, http, srv,
)

PASS, FAIL, XFAIL, XPASS, SKIP = "PASS", "FAIL", "XFAIL", "XPASS", "SKIP"
ICON = {PASS: "\033[32m✓\033[0m", FAIL: "\033[31m✗\033[0m", XFAIL: "\033[33mx\033[0m",
        XPASS: "\033[35m★\033[0m", SKIP: "\033[90m–\033[0m"}

DEADLINE = time.monotonic() + float(os.environ.get("SUITE_BUDGET_SECS", "1500"))


def left() -> float:
    return DEADLINE - time.monotonic()


# ── assertions ────────────────────────────────────────────────────────────────
def contains(*words, all_of=False):
    """Answer must contain any (or all) of these, case-insensitively."""
    def check(ans, meta):
        low = (ans or "").lower()
        hits = [w for w in words if w.lower() in low]
        return (len(hits) == len(words)) if all_of else bool(hits)
    return check


def has_digit(ans, meta):
    return any(c.isdigit() for c in ans or "")


def used(*servers):
    """The agent actually reached these MCP servers. Guards against the model answering from memory."""
    def check(ans, meta):
        got = set((meta or {}).get("mcp") or [])
        return set(servers).issubset(got)
    return check


def longer_than(n):
    return lambda ans, meta: len(ans or "") > n


def all_of(*checks):
    return lambda ans, meta: all(c(ans, meta) for c in checks)


# Two sharply separated predicates. A first cut used a single `declines()` that matched the bare word
# "provide" — which appears inside FABRICATED content ("…provide a prioritized digest…"), so a
# hallucinating agent scored as an honest refusal. Anchor on phrases, never on a lone verb.
def asks_for_input(ans, meta=None):
    """The agent asked the user to hand it the content — an honest 'I have no source'."""
    return contains("could you provide", "please provide", "provide the text", "provide the resume",
                    "share the", "provide the email", "provide the job", "if you provide",
                    "paste the", "attach the", "could you let me know", "let me know what",
                    "what specific information", "could you clarify")(ans, meta)


def declines_capability(ans, meta=None):
    """The agent said outright that it cannot reach the data."""
    return contains("cannot access", "can't access", "no access", "do not have access",
                    "don't have access", "unable to access", "not connected", "no data source",
                    "i cannot", "i can't")(ans, meta)


def refuses_honestly(ans, meta=None):
    """Either asked for the content or said it cannot reach it — anything but inventing an answer."""
    return asks_for_input(ans, meta) or declines_capability(ans, meta)


# ── the NOW catalogue ─────────────────────────────────────────────────────────
# (id, agent, utterance, check, expect, why)  — `why` is required whenever expect is XFAIL.
NOW_CASES = [
    # ── Tier A: tool-backed Q&A ────────────────────────────────────────────────
    ("now/pricebot/crypto", "pricebot", "what is the current price of bitcoin in usd? just the number",
     all_of(has_digit, used("cuga-finance")), PASS, ""),
    ("now/pricebot/stock", "pricebot", "what's IBM stock trading at right now?",
     all_of(has_digit, used("cuga-finance")), PASS, ""),
    ("now/geobot/capital", "geobot", "what is the capital of Peru?",
     contains("lima"), PASS, ""),
    ("now/geobot/population", "geobot", "what's the population of Portugal, and which region is it in?",
     all_of(has_digit, contains("europe")), PASS, ""),
    ("now/weatherbot/city", "weatherbot", "what's the weather in Tokyo right now?",
     all_of(has_digit, used("cuga-web")), PASS, ""),
    ("now/papers/arxiv", "papers", "find recent arXiv papers on mixture of experts",
     all_of(longer_than(80), used("cuga-knowledge")), PASS, ""),
    ("now/research_compass/topic", "research_compass",
     "research retrieval-augmented generation: name the key papers and what to read next",
     all_of(longer_than(200), used("cuga-knowledge")), PASS, ""),
    ("now/city_briefing/lisbon", "city_briefing", "give me a briefing on Lisbon",
     all_of(longer_than(200), used("cuga-geo")), PASS, ""),
    ("now/code_auditor/snippet", "code_auditor",
     "analyze this snippet: def f(x): return x/0",
     all_of(contains("zero", "division", "divide"), used("cuga-code")), PASS, ""),
    ("now/github_trending/repos", "github_trending", "what are the top trending GitHub repos right now?",
     all_of(longer_than(100), used("cuga-web")), PASS, ""),
    # ── Tier A (new): the cuga-web tools beyond web_search ────────────────────
    ("now/webpage_summarizer/url", "webpage_summarizer", "summarize https://example.com",
     all_of(contains("example domain", "illustrative", "placeholder"), used("cuga-web")), PASS, ""),
    ("now/video_qa/youtube", "video_qa",
     "what is this video about? https://www.youtube.com/watch?v=dQw4w9WgXcQ",
     all_of(longer_than(50), used("cuga-web")), PASS, ""),
    ("now/feed_watcher/rss", "feed_watcher", "what is new on https://hnrss.org/frontpage ?",
     all_of(longer_than(80), used("cuga-web")), PASS, ""),
    ("now/trip_planner/boulder", "trip_planner", "plan an outdoor day near Boulder, Colorado",
     all_of(longer_than(150), used("cuga-geo")), PASS, ""),

    # ── Tier B: payload-driven workers (hand them the content) ────────────────
    ("now/incident_triage/alert", "incident_triage",
     "triage this alert: HighCPU on checkout-api, 97% against an 85% threshold",
     contains("P1", "P2", "P3", "sever"), PASS, ""),
    ("now/pr_reviewer/diff", "pr_reviewer",
     "review this diff:  - if (x = 1) { ... }  + if (x == 1) { ... }",
     contains("assign", "comparison", "==", "equality"), PASS, ""),
    ("now/resume_judge/pasted", "resume_judge",
     "judge this resume against this JD. RESUME: 8 years Python, built distributed job schedulers "
     "at scale, led a team of 4. JD: senior python engineer, 5y+, distributed systems.",
     contains("fit", "strong", "match", "recommend", "suitable"), PASS, ""),

    # ── Tier C: integration-backed agents asked to FETCH. AP owns the credential, so none of these
    #    agents has a tool for its own integration. The only question is whether it fails honestly. ──
    ("now/resume_judge/box_fetch", "resume_judge", "judge the latest resume in my Box folder",
     refuses_honestly,          # it cannot read Box — asking for the file is the CORRECT behaviour
     PASS, ""),
    ("now/mailbot/inbox", "mailbot", "summarize my unread emails from today",
     lambda a, m: not refuses_honestly(a, m),   # desired: it actually summarizes the inbox
     XFAIL, "mailbot has mcp_servers=['cuga-text'] only. Gmail is an INTEGRATION (AP owns the token), "
            "not a tool, so it has nothing to fetch mail with. It asks for the content instead — an "
            "HONEST failure. Closing this means giving mailbot a Gmail read tool."),
    ("now/support_digest/digest", "support_digest", "give me the overnight support digest",
     refuses_honestly,          # desired: refuse, since it has no ticket source
     XFAIL, "support_digest has cuga-web but NO source of support tickets, so it Tavily-searches the "
            "phrase and dresses up whatever it finds as a digest — observed returning a marketing "
            "blog TEMPLATE, complete with a source URL, as if it were your overnight tickets. It "
            "FABRICATES rather than refusing: the dangerous failure, and the one worth fixing first. "
            "NON-DETERMINISTIC: ~5 of 7 sampled runs fabricate, ~2 ask for detail — so this case can "
            "XPASS by luck. Do not read a single XPASS as 'fixed'; re-sample before believing it."),
]

# ── channel probes ────────────────────────────────────────────────────────────
PROBE = "what is the current price of bitcoin in usd? just the number"

# ── standing-flow cases: (id, mode, origin_channel, utterance, expect, why) ───
CRON_CASES = [
    ("cron/papers/web", "CRON", "web",
     "every day at 9am send me new arxiv papers on mixture of experts", PASS, ""),
    ("cron/feed_watcher/web", "CRON", "web",
     "every morning at 8am tell me what's new on https://hnrss.org/frontpage", PASS, ""),
    ("cron/pricebot/telegram", "CRON", "telegram",
     "every day at 9am send me the price of bitcoin", PASS, ""),
    ("cron/github_trending/slack", "CRON", "slack",
     "every hour post the top trending GitHub repos in this channel", PASS, ""),
]

POLL_CASES = [
    ("poll/pricebot/web", "POLL", "web",
     "watch bitcoin every 2 minutes and ping me on any move", PASS, ""),
    ("poll/weatherbot/web", "POLL", "web",
     "check the New York weather every hour and ping me only if rain is forecast", PASS, ""),
    # XFAIL: the NL→flow gap (GAPS.md) in one line. The planner deterministically routes
    # "check <URL> every 15 minutes and tell me only about new items" to CRON, not POLL — "check … every N"
    # reads as a schedule and the "only about new items" change-signal isn't weighted enough to flip it.
    # The other two POLL cases ("watch … on any move", "… only if rain") classify correctly, so the POLL
    # machinery is fine; this is classification, not plumbing. A typed FlowSpec + validation gate is the fix.
    ("poll/feed_watcher/discord", "POLL", "discord",
     "check https://hnrss.org/frontpage every 15 minutes and tell me only about new items", XFAIL,
     "NL→flow gap: 'check <URL> every N minutes' is deterministically classified CRON, not POLL, even "
     "with a 'only new items' change-signal (verified 3/3 on the dry-run planner 2026-07-10). POLL "
     "itself works — the pricebot/weatherbot POLL cases pass. Fix is the typed FlowSpec + validation "
     "gate tracked in ROADMAP.md, not a plumbing bug."),
]


# ── reporting ─────────────────────────────────────────────────────────────────
class Report:
    def __init__(self):
        self.rows = []           # (phase, id, status, detail, why)

    def add(self, phase, cid, status, detail="", why=""):
        self.rows.append((phase, cid, status, detail, why))
        line = f"   {ICON[status]} {cid}"
        if detail:
            line += f"  — {detail}"
        print(line, flush=True)
        if status == XFAIL and why:
            print(f"       known gap: {why[:150]}", flush=True)
        if status == XPASS:
            print("       \033[35mthis was expected to FAIL and it PASSED — remove the expectation\033[0m",
                  flush=True)
        return status

    def record(self, phase, cid, ok, expect, detail="", why=""):
        """Collapse (did it work?) × (did we expect it to?) into one of four outcomes."""
        if expect == PASS:
            return self.add(phase, cid, PASS if ok else FAIL, detail, why)
        return self.add(phase, cid, XPASS if ok else XFAIL, detail, why)

    def counts(self):
        c = dict.fromkeys((PASS, FAIL, XFAIL, XPASS, SKIP), 0)
        for _, _, s, _, _ in self.rows:
            c[s] += 1
        return c

    def summary(self) -> int:
        c = self.counts()
        print("\n" + "═" * 76)
        print("  SUITE REPORT")
        print("═" * 76)
        for phase in dict.fromkeys(r[0] for r in self.rows):
            rows = [r for r in self.rows if r[0] == phase]
            tally = {}
            for _, _, s, _, _ in rows:
                tally[s] = tally.get(s, 0) + 1
            bits = " ".join(f"{ICON[s]}{n}" for s, n in tally.items())
            print(f"  {phase:<10} {len(rows):>2} cases   {bits}")
        print("─" * 76)
        print(f"  {c[PASS]} passed · {c[FAIL]} FAILED · {c[XFAIL]} xfail (known gaps) · "
              f"{c[XPASS]} xpass · {c[SKIP]} skipped")

        if c[FAIL]:
            print("\n  \033[31mFAILURES — these were expected to work:\033[0m")
            for phase, cid, s, detail, _ in self.rows:
                if s == FAIL:
                    print(f"    ✗ [{phase}] {cid}\n        {detail}")
        if c[XPASS]:
            print("\n  \033[35mXPASS — a known gap just closed. Change `expect` to PASS:\033[0m")
            for phase, cid, s, _, why in self.rows:
                if s == XPASS:
                    print(f"    ★ [{phase}] {cid} — was: {why[:110]}")
        if c[XFAIL]:
            print("\n  \033[33mKnown gaps (expected, not failures):\033[0m")
            for phase, cid, s, _, why in self.rows:
                if s == XFAIL:
                    print(f"    x [{phase}] {cid}")
        print("\n  RESULT:", "\033[31mFAIL\033[0m" if c[FAIL] else "\033[32mPASS\033[0m")
        return 1 if c[FAIL] else 0


# ── invocation helpers ────────────────────────────────────────────────────────
def invoke_agent(agent: str, text: str, timeout=280):
    """Direct call to a NAMED agent: no concierge, no channel. `time` + `runonce`."""
    body = {"text": text, "agent": agent, "deliver": False,
            "source": {"type": "time", "name": "runonce", "thread_id": f"api:{RUN}:{agent}"},
            "event": {"kind": "runonce", "payload": {}}}
    code, rep = srv("POST", "/invoke", body, gw_headers(), timeout=timeout)
    return code, str(rep.get("answer") or ""), (rep.get("meta") or {}), rep.get("error")


def ask_concierge(channel: str, native: str, tag: str, text: str, timeout=300):
    """Route through the CONCIERGE from a given origin — the sink follows the thread_id."""
    if channel == "web":
        code, rep = srv("POST", "/api/concierge",
                        {"text": text, "thread_id": f"web:{RUN}:{tag}"}, timeout=timeout)
        return code, str(rep.get("reply") or "")
    # slack/discord get a #locus suffix for fresh memory (channel_origin strips it at fire time);
    # telegram must not, because its AP send step bakes the target verbatim. See live_matrix.py.
    thread = (f"gw:telegram:{native}" if channel == "telegram"
              else f"gw:{channel}:{native}#{RUN}{tag}")
    body = {"text": text, "agent": "concierge", "deliver": False,
            "source": {"type": "channel", "name": channel, "thread_id": thread, "user": f"e2e-{RUN}"},
            "event": {"kind": "message", "payload": {}}}
    code, rep = srv("POST", "/invoke", body, gw_headers(), timeout=timeout)
    return code, str(rep.get("answer") or rep.get("error") or "")


def subs():
    code, s = srv("GET", "/api/events/subscriptions", timeout=30)
    return s.get("subscriptions", []) if code == 200 else []


def adopt_new(before: set) -> list:
    return [s["id"] for s in subs() if s["id"] not in before]


def match(before: set, *, mode=None, source=None, sink=None):
    fresh = reused = None
    for s in subs():
        if mode and s.get("mode") != mode:
            continue
        if source and not (s.get("source_connector") == source and s.get("source_type") == "integration"):
            continue
        if sink and sink not in (s.get("deliver_to") or []):
            continue
        if s["id"] in before:
            reused = reused or s
        else:
            fresh = fresh or s
    return (fresh, True) if fresh else (reused, False)


# ── channel discovery ─────────────────────────────────────────────────────────
def targets() -> dict:
    t = {"web": ""}
    tok = env("SLACK_BOT_TOKEN")
    chan = env("SLACK_TEST_CHANNEL")
    if tok and not chan:
        _, lst = http("GET", "https://slack.com/api/conversations.list?types=public_channel&limit=200",
                      headers={"Authorization": f"Bearer {tok}"}, timeout=20)
        m = [c for c in lst.get("channels", []) if c.get("is_member")]
        chan = m[0]["id"] if m else ""
    t["slack"] = chan or None if tok else None

    dtok, dchan = env("DISCORD_BOT_TOKEN"), env("DISCORD_TEST_CHANNEL_ID")
    if dtok and not dchan:
        dh = {"Authorization": f"Bot {dtok}"}
        _, g = http("GET", "https://discord.com/api/v10/users/@me/guilds", headers=dh, timeout=20)
        if isinstance(g, list) and g:
            _, cs = http("GET", f"https://discord.com/api/v10/guilds/{g[0]['id']}/channels",
                         headers=dh, timeout=20)
            txt = [c for c in (cs if isinstance(cs, list) else []) if c.get("type") == 0]
            dchan = txt[0]["id"] if txt else ""
    t["discord"] = dchan or None if dtok else None
    t["telegram"] = (env("TELEGRAM_CHAT_ID") or None) if env("TELEGRAM_BOT_TOKEN") else None
    return t


# ── phases ────────────────────────────────────────────────────────────────────
def phase_now(r: Report):
    print("\n\033[1m[now]\033[0m  every seeded agent, invoked directly (time/runonce — no concierge)")
    for cid, agent, text, check, expect, why in NOW_CASES:
        if left() < 60:
            r.add("now", cid, SKIP, "time budget exhausted")
            continue
        code, ans, meta, err = invoke_agent(agent, text)
        if code != 200:
            r.record("now", cid, False, expect, f"HTTP {code}: {err}", why)
            continue
        ok = bool(check(ans, meta))
        mcp = ",".join(meta.get("mcp") or []) or "no mcp"
        detail = f"[{mcp}] {ans[:70].replace(chr(10), ' ')}"
        r.record("now", cid, ok, expect, detail, why)


def phase_channels(r: Report, t: dict):
    print("\n\033[1m[channels]\033[0m  the same probe over each chat surface")
    # web (concierge)
    code, reply = ask_concierge("web", "", "probe", PROBE)
    r.record("channels", "channels/web/concierge", code == 200 and any(c.isdigit() for c in reply),
             PASS, reply[:70].replace("\n", " "))

    # slack — a REAL round trip: post a message, send the Events API callback Slack would send,
    # then read the bot's threaded reply back out of Slack.
    tok, secret, chan = env("SLACK_BOT_TOKEN"), env("SLACK_SIGNING_SECRET"), t.get("slack")
    if not (tok and chan):
        r.add("channels", "channels/slack/roundtrip", SKIP, "SLACK_BOT_TOKEN or channel missing")
    else:
        sh = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json; charset=utf-8"}
        _, posted = http("POST", "https://slack.com/api/chat.postMessage",
                         {"channel": chan, "text": f"[suite] {PROBE}"}, sh, timeout=20)
        if not posted.get("ok"):
            r.add("channels", "channels/slack/roundtrip", SKIP, f"postMessage: {posted.get('error')}")
        else:
            ts = posted["ts"]
            ev = {"type": "event_callback",
                  "event": {"type": "message", "text": PROBE, "channel": chan,
                            "user": "U0SUITE", "ts": ts}}
            raw, stamp = json.dumps(ev), str(int(time.time()))
            hdrs = {"Content-Type": "application/json"}
            if secret:
                hdrs["X-Slack-Request-Timestamp"] = stamp
                hdrs["X-Slack-Signature"] = "v0=" + hmac.new(
                    secret.encode(), f"v0:{stamp}:{raw}".encode(), hashlib.sha256).hexdigest()
            code, ack = http("POST", BASE + "/api/events/slack/events", raw_body=raw,
                             headers=hdrs, timeout=30)
            reply = ""
            deadline = time.monotonic() + min(180, max(30, left() - 120))
            while time.monotonic() < deadline and not reply:
                time.sleep(4)
                _, thr = http("GET", f"https://slack.com/api/conversations.replies?channel={chan}&ts={ts}",
                              headers=sh, timeout=20)
                for m in thr.get("messages", [])[1:]:
                    if m.get("text"):
                        reply = m["text"]
                        break
            r.record("channels", "channels/slack/roundtrip",
                     code == 200 and bool(reply) and any(c.isdigit() for c in reply),
                     PASS, reply[:70].replace("\n", " ") or "no threaded reply")

    # discord / telegram — drive the envelope their transport posts (the bot cannot message itself)
    for ch in ("discord", "telegram"):
        if not t.get(ch):
            r.add("channels", f"channels/{ch}/concierge", SKIP, f"{ch} not configured")
            continue
        code, ans = ask_concierge(ch, t[ch], "probe", PROBE)
        r.record("channels", f"channels/{ch}/concierge",
                 code == 200 and any(c.isdigit() for c in ans), PASS, ans[:70].replace("\n", " "))


def phase_standing(r: Report, t: dict, cases, ap_live: bool, created: list, phase: str):
    print(f"\n\033[1m[{phase}]\033[0m  arm standing flows and verify the AP flow exists")
    for cid, mode, ch, utter, expect, why in cases:
        if t.get(ch) is None:
            r.add(phase, cid, SKIP, f"{ch} not configured")
            continue
        if left() < 60:
            r.add(phase, cid, SKIP, "time budget exhausted")
            continue
        before = {s["id"] for s in subs()}
        code, reply = ask_concierge(ch, t[ch], cid.split("/")[-1], utter)
        created.extend(adopt_new(before))          # register EVERY new sub, before any filtering
        if code != 200:
            r.record(phase, cid, False, expect, f"HTTP {code}", why)
            continue
        sub, is_new = match(before, mode=mode, sink=(ch if ch != "web" else None))
        if not sub:
            r.record(phase, cid, False, expect,
                     f"no {mode} subscription for sink '{ch}': {reply[:70]}", why)
            continue
        if not ap_live:
            r.add(phase, cid, SKIP, "AP unreachable")
            continue
        # The flow must EXIST in AP, not merely have an id in our store. (An earlier version tested
        # `bool(flow)` on the whole response dict — always truthy — so it verified nothing.)
        ok, detail = (False, "no ap_flow_id") if not sub.get("ap_flow_id") else flow_alive(sub)
        sink_txt = ",".join(sub.get("deliver_to") or []) or "web"
        r.record(phase, cid, ok, expect,
                 f"{detail} ({'new' if is_new else 'reused'}) → {sink_txt}" if ok else detail, why)


def phase_push(r: Report, ap_live: bool, conn: dict, created: list):
    print("\n\033[1m[push]\033[0m  integration watchers + the generic inbound webhook")
    repo, folder = env("GITHUB_TEST_REPO"), env("BOX_FOLDER_ID")
    cases = [
        ("push/gmail/web", "gmail", "web",
         "when an email from my boss arrives, summarize it and message me", PASS, ""),
        ("push/box/web", "box", "web",
         "when a resume lands in my Box" + (f" folder {folder}" if folder else "") +
         ", judge it against this JD — 'senior python engineer, 5y, distributed systems' — and tell me",
         PASS, ""),
        ("push/github/web", "github", "web",
         (f"when a pull request opens on {repo}, summarize it and message me" if repo
          else "when a pull request opens on my repo, summarize it and message me"),
         PASS if repo else XFAIL,
         "" if repo else
         "No GITHUB_TEST_REPO set, so the utterance says 'my repo' — the concierge correctly asks "
         "*which* repo to watch (needs-input, not a bug). GitHub is OAuth-connected; when "
         "GITHUB_TEST_REPO=owner/repo is set, this case ARMS a real PUSH watcher (which creates a "
         "real repo webhook) and passes. Verified live 2026-07-10: named-repo arming + a synthetic-PR "
         "fire through pr_reviewer both work. Set GITHUB_TEST_REPO to a repo whose webhooks you may "
         "manage to turn this green."),
    ]
    for cid, app, ch, utter, expect, why in cases:
        if left() < 60:
            r.add("push", cid, SKIP, "time budget exhausted")
            continue
        before = {s["id"] for s in subs()}
        code, reply = ask_concierge(ch, "", cid.split("/")[1], utter)
        created.extend(adopt_new(before))
        if code != 200:
            r.record("push", cid, False, expect, f"HTTP {code}", why)
            continue
        sub, is_new = match(before, source=app)     # mode unconstrained: direct box arms a POLL
        if sub and sub.get("ap_flow_id") and ap_live:
            alive, detail = flow_alive(sub)
            r.record("push", cid, alive, expect,
                     detail if alive else f"DANGLING — {detail}", why)
            continue
        # The gate fires on the first UNCONNECTED integration the AGENT needs, which may not be the
        # app we asked about (resume_judge needs box AND gmail — it emails the verdict).
        want = connect_needed_app(reply)
        if want:
            note = f"CONNECT NEEDED ({want})" + ("" if ap_live else " — AP is DOWN, so this is a "
                                                                    "FALSE negative, not a real prompt")
            r.record("push", cid, False, expect, f"{note}: {reply[:60]}", why)
            continue
        r.record("push", cid, False, expect, f"not armed: {reply[:70]}", why)

    # generic inbound webhook — direct, no AP
    code, rep = srv("POST", "/api/events/hook/monitoring",
                    {"alert": "HighCPU", "service": "checkout-api", "value": "97%", "threshold": "85%"},
                    timeout=240)
    ans = str(rep.get("answer") or "")
    r.record("push", "push/webhook/incident_triage",
             code == 200 and rep.get("ok") and any(s in ans for s in ("P1", "P2", "P3", "sever")),
             PASS, ans[:70].replace("\n", " "))


def cleanup(created: list):
    if not created:
        return
    print(f"\n\033[1m[cleanup]\033[0m  deleting {len(created)} subscription(s) this run created")
    bad = 0
    for sid in dict.fromkeys(created):
        code, _ = srv("DELETE", f"/api/events/subscriptions/{sid}", timeout=60)
        bad += code != 200
    print(f"     {len(set(created)) - bad} deleted, {bad} failed")


def main() -> int:
    a = argparse.ArgumentParser(description="Live behavioural suite: NOW → channels → cron/poll/push.")
    a.add_argument("--only", choices=["now", "channels", "cron", "poll", "push", "flows"])
    a.add_argument("--no-cleanup", action="store_true")
    args = a.parse_args()

    print(f"\033[1mCUGA live suite\033[0m — {BASE}  (budget {left():.0f}s)")
    code, st = srv("GET", "/api/events/status", timeout=20)
    if code != 200:
        print(f"  server unreachable at {BASE} — run `make up`")
        return 2
    ap_url = env("AP_BASE_URL", "http://localhost:8081").rstrip("/")
    ap_live = http("GET", f"{ap_url}/api/v1/flags", timeout=8)[0] == 200
    _, integ = srv("GET", "/api/events/integrations", timeout=20)
    conn = {i["name"]: i.get("status") for i in integ.get("integrations", [])}
    _, ag = srv("GET", "/api/events/agents", timeout=20)
    print(f"  agents: {len(ag.get('agents', []))}   AP: {'up' if ap_live else 'DOWN'}   "
          f"integrations: { {k: conn.get(k) for k in ('box', 'github', 'gmail')} }")
    if not ap_live:
        print("  \033[33mAP is down — cron/poll/push cannot arm, and CONNECT NEEDED would be a "
              "false negative (DECISIONS_2026-07-09 §2).\033[0m")

    t = targets()
    print(f"  channels: { {k: (v or '—') for k, v in t.items()} }")

    r, created = Report(), []
    only = args.only
    try:
        if only in (None, "now"):
            phase_now(r)
        if only in (None, "channels"):
            phase_channels(r, t)
        if only in (None, "cron", "flows"):
            phase_standing(r, t, CRON_CASES, ap_live, created, "cron")
        if only in (None, "poll", "flows"):
            phase_standing(r, t, POLL_CASES, ap_live, created, "poll")
        if only in (None, "push", "flows"):
            phase_push(r, ap_live, conn, created)
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        if not args.no_cleanup:
            cleanup(created)
        elif created:
            print(f"\n[cleanup] skipped — {len(created)} left: {', '.join(created)}")
    return r.summary()


if __name__ == "__main__":
    sys.exit(main())
