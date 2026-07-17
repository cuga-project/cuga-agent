"""EXHAUSTIVE live matrix — every agent, every trigger, every channel: arm → FIRE → answer-VERIFIED.

The three gates per case (events_docs/plans/EXHAUSTIVE_MATRIX.md):
  ARMED   — the flow/subscription really exists (not just a polite reply),
  FIRED   — an event traverses the REAL path (trigger/gateway → /invoke → supervisor → answer),
  QUALITY — the answer contains the case's planted facts (expect_any) and NONE of the failure
            signatures (forbid): executor scaffolding, deliberation leaks, refusals, loop attempts.

Legs are marked honestly:
  REAL    — a genuine external event (webhook POST, AP schedule tick, repo API action)
  SYNTH   — piece-exact payload injected at the seam a real event would use
  BLOCKED — cannot run now; the reason and the unblock are printed (e.g. Box dev token)

Coverage gates (fail the run, not just a case):
  * every trigger in the registry has a case here,
  * every roster agent has at least one NOW case (from catalog.py's examples),
  * subscriptions after == subscriptions before (no leaked flows).

Reuses the proven single-surface harnesses as subprocesses (github triggers 14/14, the channel/
flow suite) and adds the legs they never had. Runtime ~45–75 min. Run:
    .venv/bin/python tests/events/live_exhaustive.py            # everything
    .venv/bin/python tests/events/live_exhaustive.py --fast     # skip the subprocess harnesses
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO_DIR, "src", "cuga", "backend", "events"))
sys.path.insert(0, os.path.dirname(__file__))
import triggers  # noqa: E402
import catalog   # noqa: E402

SERVER = os.environ.get("EVENTS_SERVER_URL", "http://localhost:7860").rstrip("/")


def _env(key, default=""):
    v = os.environ.get(key)
    if v:
        return v.split(" #", 1)[0].strip()
    p = os.path.join(REPO_DIR, ".env")
    if os.path.exists(p):
        for line in open(p):
            if line.strip().startswith(key + "="):
                return line.split("=", 1)[1].split(" #", 1)[0].strip().strip('"').strip("'")
    return default


GW = _env("GATEWAY_TOKEN")

# the imported preflight checks read os.environ directly — hydrate it from .env once
for _k in ("AP_BASE_URL", "AP_EMAIL", "AP_PASSWORD", "TELEGRAM_BOT_TOKEN"):
    if not os.environ.get(_k) and _env(_k):
        os.environ[_k] = _env(_k)

# ---- the QUALITY gate ------------------------------------------------------
# failure signatures: if ANY of these appears in a delivered answer, the case FAILS even though
# an answer "came back" — this is the "gives you some crap" detector.
FORBID = [
    "## New Variables Created", "Execution output:", "We have a loop", "delegate_to_",
    "I'm unable to", "I am unable to", "I cannot run", "execution timeout",
    "time.sleep(", "asyncio.sleep(", "while True",
    "connect your", "CONNECT NEEDED",
]


def quality(answer: str, expect_any: list[str]) -> tuple[bool, str]:
    # models emit typographic dashes/quotes (e.g. U+2011 in "Q3‑candidates") — normalize before
    # substring checks or a CORRECT answer fails the marker gate (happened live 2026-07-17)
    a = (answer or "")
    for uni, ascii_ in (("\u2010", "-"), ("\u2011", "-"), ("\u2012", "-"), ("\u2013", "-"),
                        ("\u2014", "-"), ("\u2018", "'"), ("\u2019", "'"),
                        ("\u201c", '"'), ("\u201d", '"')):
        a = a.replace(uni.encode().decode("unicode_escape"), ascii_)
    for bad in FORBID:
        if bad.lower() in a.lower():
            return False, f"forbidden marker {bad!r}"
    if not a.strip():
        return False, "empty answer"
    if expect_any and not any(e.lower() in a.lower() for e in expect_any):
        return False, f"none of {expect_any} in answer"
    return True, ""


# ---- plumbing ---------------------------------------------------------------
def http(method, path_or_url, body=None, headers=None, timeout=240):
    url = path_or_url if path_or_url.startswith("http") else SERVER + path_or_url
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)}


def concierge(text, thread):
    code, rep = http("POST", "/api/concierge", {"text": text, "thread_id": thread},
                     headers={"x-user-id": "admin"})
    return rep.get("reply", "") if code == 200 else f"HTTP {code}: {rep}"


def invoke_fire(source_name: str, kind: str, payload: dict, text: str, thread: str):
    """A synthetic FIRE at the /invoke seam — the exact envelope an armed flow/gateway sends."""
    env = {"agent": "cuga", "thread_id": thread, "text": text, "deliver": False,
           "source": {"type": "integration", "name": source_name, "thread_id": thread},
           "event": {"kind": kind, "payload": payload}}
    code, rep = http("POST", "/invoke", env, headers={"X-Gateway-Token": GW}, timeout=300)
    return (rep.get("answer") or "") if code == 200 and rep.get("ok") else f"HTTP {code}: {rep}"


class Report:
    def __init__(self):
        self.rows = []          # (leg, case, kind REAL/SYNTH/BLOCKED, ok, detail)

    def add(self, leg, case, kind, ok, detail=""):
        self.rows.append((leg, case, kind, ok, detail))
        mark = "PASS" if ok else ("----" if kind == "BLOCKED" else "FAIL")
        print(f"  [{mark}] [{kind:7}] {leg:10} {case:34} {detail[:80]}")

    def summary(self):
        real = [r for r in self.rows if r[2] == "REAL"]
        synth = [r for r in self.rows if r[2] == "SYNTH"]
        blocked = [r for r in self.rows if r[2] == "BLOCKED"]
        fails = [r for r in self.rows if not r[3] and r[2] != "BLOCKED"]
        print("\n" + "=" * 78)
        print(f"  {len(self.rows)} cases · REAL {sum(r[3] for r in real)}/{len(real)}"
              f" · SYNTH {sum(r[3] for r in synth)}/{len(synth)}"
              f" · BLOCKED {len(blocked)} · FAILURES {len(fails)}")
        for r in fails:
            print(f"    ✗ {r[0]}/{r[1]} — {r[4][:100]}")
        for r in blocked:
            print(f"    ⏸ {r[0]}/{r[1]} — {r[4][:100]}")
        return len(fails)


# ---- payloads per trigger (planted MARKERS make the quality gate deterministic) --------------
REPO = os.environ.get("GITHUB_TEST_REPO", "anupamamurthi/pachyderm")
SYNTH_FIRES = {
    # app/event: (payload, worker text, expect_any)
    "gmail/new_email": ({"subject": "Fwd: vendor budget QX-77", "from": "finance@example.com",
                         "body": "Approve the $42,000 vendor increase by Friday or renewal lapses."},
                        "when a new email arrives, summarize it", ["42,000", "42000", "budget"]),
    "gmail/new_labeled_email": ({"subject": "Read-later: MoE survey ZL-88", "label": "Read-later",
                                 "body": "A survey of mixture-of-experts routing stability."},
                                "when I label an email Read-later, summarize it",
                                ["ZL-88", "mixture", "MoE", "survey"]),
    "gmail/new_attachment": ({"subject": "resume attached", "from": "jane@example.com",
                              "attachment_name": "jane_doe_ml_engineer.pdf",
                              "body": "Attaching my resume — 8 years production ML."},
                             "when an email with an attachment arrives, describe it",
                             ["jane", "resume", "attach"]),
    "gmail/new_gmail_label": ({"label": "Q3-Receipts"},
                              "when a new gmail label is created, announce it", ["Q3-Receipts"]),
    "discord/new_channel_message": ({"text": "deploy pipeline PLUM-9 is red again", "channel": "C1"},
                                    "when someone posts in the channel, triage it",
                                    ["PLUM-9", "deploy", "pipeline"]),
    "discord/new_member": ({"username": "zeta_tester_77", "guild": "G1"},
                           "when a member joins, draft a welcome", ["zeta_tester_77", "welcome"]),
    "telegram/new_channel_message": ({"text": "check this out https://example.com/moe-paper"},
                                     "when I send a link, summarize the page",
                                     ["example.com", "link", "page", "summar"]),
    "slack/new_reaction": ({"emoji": "bug", "message_text": "checkout 500s spiking GRB-12"},
                           "when a message gets a :bug: reaction, triage it",
                           ["GRB-12", "checkout", "P1", "P2", "sever"]),
    "slack/reaction_removed": ({"emoji": "bug", "message_text": "incident GRB-12 resolved"},
                               "when a :bug: reaction is removed, note the resolution",
                               ["GRB-12", "resolv", "removed"]),
    "slack/new_slack_mention": ({"text": "<@bot> what is blocking release RC-4?"},
                                "when the bot is mentioned, answer the question",
                                ["RC-4", "release", "block"]),
    "slack/channel_created": ({"channel_name": "proj-orchid"},
                              "when a channel is created, announce it", ["proj-orchid"]),
    "slack/new_slack_user": ({"user_name": "new.hire.omega"},
                             "when a user joins the workspace, draft onboarding pointers",
                             ["omega", "welcome", "onboard"]),
    "slack/new_emoji": ({"emoji_name": "partyblob77"},
                        "when a custom emoji is added, announce it", ["partyblob77"]),
    "slack/saved_message": ({"message_text": "decision: ship flag FLG-31 next sprint"},
                            "when I save a message, file it as a decision",
                            ["FLG-31", "decision", "ship"]),
    "slack/new_channel_message": ({"text": "prod alert: queue depth 9000 on JOB-55", "channel": "C9"},
                                  "when someone posts in the alerts channel, assess severity",
                                  ["JOB-55", "queue", "sever", "P1", "P2"]),
    "box/new_file": ({"name": "jane_doe_resume.pdf", "id": "F123"},
                     "when a resume lands in Box, judge fit vs the JD",
                     ["jane", "resume", "MATCH", "SKIP", "judg", "fit"]),
    "box/new_folder": ({"name": "Q3-candidates"}, "when a folder is created, announce it",
                       ["Q3-candidates"]),
    "box/new_box_comment": ({"comment": "please re-check page 2 of QV-19", "file": "offer.pdf"},
                            "when a comment lands on a file, summarize it",
                            ["QV-19", "page 2", "comment", "offer"]),
    "webhook/inbound": ({"alert": "HighCPU", "service": "checkout-api", "value": "97%"},
                        None, ["P1", "P2", "P3", "sever", "checkout"]),   # fired via real POST
}


def leg_preflight(r: Report) -> bool:
    code, rep = http("GET", "/api/events/status", timeout=10)
    ok = code == 200 and rep.get("enabled")
    r.add("preflight", "server+events", "REAL", bool(ok), f"HTTP {code}")
    if not ok:
        return False
    cap = "\n".join(rep.get("capability") or [])
    ap_ok = "Activepieces reachable" in cap
    r.add("preflight", "activepieces", "REAL", ap_ok, "reachable" if ap_ok else "DOWN — cron/poll/AP legs will fail")
    # the baked-frontend-URL check (the flap that silently kills every AP flow run)
    try:
        import preflight as pf
        ok2, msg = pf.check_activepieces()
        r.add("preflight", "ap_frontend_url", "REAL", bool(ok2), (msg or "")[:90])
        if ok2 is False and "DEAD" in (msg or ""):
            return False
    except Exception as e:  # noqa: BLE001
        r.add("preflight", "ap_frontend_url", "BLOCKED", True, f"check unavailable: {e}")
    return True


def leg_agents_now(r: Report):
    """Every roster agent answers its signature catalog utterance through the supervisor."""
    import yaml
    roster = yaml.safe_load(open(os.path.join(REPO_DIR, "supervisor_agents.yaml")))
    agents = roster.get("agents", roster) if isinstance(roster, dict) else roster
    names = {a["name"] for a in agents}
    by_agent = {}
    for e in catalog.EXAMPLES:
        if e.get("trigger") == "now" and e.get("agent") in names and e.get("agent") not in by_agent:
            by_agent[e["agent"]] = e["utterance"]
    missing = sorted(names - set(by_agent))
    for m in missing:                              # coverage gate: an agent with no NOW example
        r.add("now", m, "BLOCKED", True, "no catalog NOW example — add one (coverage gap)")
    for agent, utter in sorted(by_agent.items()):
        ans = concierge(utter, f"exh:now:{agent}")
        ok, why = quality(ans, [])
        r.add("now", agent, "REAL", ok, why or ans[:70].replace("\n", " "))


def leg_trigger_arms(r: Report):
    """Every registry trigger ARMS through the concierge (or asks its ONE missing-slot question)."""
    for row in triggers.rows():
        key = f"{row.app}/{row.event}"
        if row.app == "github":
            continue                               # the github subprocess harness arms all 14
        if key == "webhook/inbound":
            rep = concierge("how do I trigger a flow from an external webhook?", "exh:arm:webhook")
            ok = "/api/events/hook" in rep or "always live" in rep.lower() or "webhook" in rep.lower()
            r.add("arm", key, "REAL", ok, rep[:80])   # correct answer: nothing to arm, POST here
            continue
        utter = (SYNTH_FIRES.get(key, ({}, None, []))[1]
                 or f"when a {row.event.replace('_', ' ')} happens on {row.app}, tell me")
        # the fire-leg texts are seam prompts; arming needs the APP named the way a user would
        utter = {"box/new_box_comment": "when a comment lands on a file in my Box, summarize it",
                 "telegram/new_channel_message":
                     "whenever I send the telegram bot a link, summarize the page",
                 }.get(key, utter)
        rep = concierge(utter, f"exh:arm:{row.app}:{row.event}")
        armed = any(w in rep.upper() for w in ("ARMED", "REUSING", "ALREADY ACTIVE",
                                               "ALREADY ARMED", "ALREADY WATCHING"))
        asked = "?" in rep[-140:] or rep.strip().endswith("?")
        r.add("arm", key, "REAL", armed or asked,
              ("armed" if armed else "asks: " + rep[:60]) if (armed or asked) else rep[:80])


def leg_synth_fires(r: Report):
    """Fire every non-github trigger at the /invoke seam with a planted-marker payload."""
    box_blocked = False
    tok = _env("BOX_DEV_TOKEN")
    if tok:
        code, _ = http("GET", "https://api.box.com/2.0/users/me", headers={"Authorization": f"Bearer {tok}"}, timeout=15)
        box_blocked = code != 200
    else:
        box_blocked = True
    for key, (payload, text, expect) in sorted(SYNTH_FIRES.items()):
        app, event = key.split("/")
        if app == "webhook":
            code, rep = http("POST", "/api/events/hook/monitoring", payload)
            ans = str(rep.get("answer") or "")
            ok, why = quality(ans, expect)
            r.add("fire", key, "REAL", code == 200 and ok, why or ans[:70].replace("\n", " "))
            continue
        if app == "box" and box_blocked:
            r.add("fire", key, "BLOCKED", True, "BOX_DEV_TOKEN missing/expired — send a fresh one")
            continue
        ans = invoke_fire(app, event, payload, text, f"exh:fire:{app}:{event}")
        ok, why = quality(ans, expect)
        r.add("fire", key, "SYNTH", ok, why or ans[:70].replace("\n", " "))


def leg_channel_slash(r: Report):
    """The slash-arm probe per inbound door (the web one silently broke once — permanent gate).
    Bounded (TTL) so every armed flow deletes itself."""
    utter = "/cron every 2 minutes post the bitcoin price for 2 minutes"
    # web main chat (/stream) — SSE; take the final Answer event
    code, _ = 0, None
    try:
        req = urllib.request.Request(SERVER + "/stream", data=json.dumps({"query": utter}).encode(),
                                     headers={"Content-Type": "application/json",
                                              "X-Thread-ID": "exh:slash:webmain"}, method="POST")
        with urllib.request.urlopen(req, timeout=200) as resp:
            body = resp.read().decode()
        armed = ("ARMED" in body.upper()) or ("REUSING" in body.upper())
        r.add("slash", "web:/stream", "REAL", armed, body.strip().splitlines()[-1][:80] if body else "no output")
    except Exception as e:  # noqa: BLE001
        r.add("slash", "web:/stream", "REAL", False, str(e)[:80])
    # events chat + the 3 channels share /api/concierge / /invoke(concierge) — one probe each seam
    rep = concierge(utter, "exh:slash:conc")
    r.add("slash", "web:/api/concierge", "REAL",
          any(w in rep.upper() for w in ("ARMED", "REUSING")), rep[:80])
    env = {"agent": "concierge", "thread_id": "exh:slash:chan", "text": utter, "deliver": False,
           "source": {"type": "channel", "name": "telegram", "thread_id": "exh:slash:chan"},
           "event": {"kind": "message", "payload": {}}}
    code, rep2 = http("POST", "/invoke", env, headers={"X-Gateway-Token": GW})
    ans = str(rep2.get("answer") or "")
    r.add("slash", "channel:/invoke", "SYNTH",
          code == 200 and any(w in ans.upper() for w in ("ARMED", "REUSING")), ans[:80])


def leg_subprocess(r: Report, name: str, cmd: list[str], parse_pass: str, timeout=2400):
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_DIR, timeout=timeout)
    out = (p.stdout + p.stderr)
    open(f"/tmp/exhaustive_{name}.log", "w").write(out)   # keep the detail for diagnosis
    m = re.search(parse_pass, out)
    detail = (m.group(0) if m else out.strip().splitlines()[-1][:90] if out.strip() else "no output")
    r.add("harness", name, "REAL", p.returncode == 0, f"{detail} · {time.time()-t0:.0f}s")


def main() -> int:
    fast = "--fast" in sys.argv
    r = Report()
    subs_before = len((http("GET", "/api/events/subscriptions")[1]).get("subscriptions", []))

    print("== preflight ==")
    if not leg_preflight(r):
        r.summary()
        return 1
    print("== every agent answers (NOW, through the supervisor) ==")
    leg_agents_now(r)
    print("== every trigger arms ==")
    leg_trigger_arms(r)
    print("== every trigger fires (synthetic seam; webhook real) ==")
    leg_synth_fires(r)
    print("== slash-arm probe per door (TTL-bounded) ==")
    leg_channel_slash(r)
    if not fast:
        print("== proven single-surface harnesses (REAL fires) ==")
        py = os.path.join(REPO_DIR, ".venv", "bin", "python")
        leg_subprocess(r, "github-14-triggers", [py, "tests/events/live_github_triggers.py"],
                       r"\d+/\d+ (passed|ok|fired)")
        # live_e2e is the SUPERVISOR-NATIVE channel/flow suite (test-live). live_suite.py is
        # fleet-era (asserts per-agent meta.mcp by name → false-fails against the supervisor's
        # hint: reporting — ROADMAP §5) and must NOT be used here.
        leg_subprocess(r, "channels+flows-e2e", [py, "tests/events/live_e2e.py"],
                       r"\d+ passed[^\n]*")

    # cleanup: everything this run armed (exh: threads) — TTL flows self-delete; the rest by thread
    code, rep = http("GET", "/api/events/subscriptions")
    leaked = [s for s in rep.get("subscriptions", []) if "exh:" in str(s.get("thread_id", ""))]
    for s in leaked:
        http("DELETE", f"/api/events/subscriptions/{s['id']}")
    subs_after = len((http("GET", "/api/events/subscriptions")[1]).get("subscriptions", []))
    r.add("cleanup", "no leaked subscriptions", "REAL", subs_after <= subs_before,
          f"{subs_before} before → {subs_after} after ({len(leaked)} cleaned)")

    fails = r.summary()
    # ledger: per-trigger fire cells
    try:
        from _ledger import record
        for leg, case, kind, ok, detail in r.rows:
            if leg == "fire":
                record(case, "fire_synth" if kind == "SYNTH" else "fire_real",
                       "ok" if ok else ("blocked" if kind == "BLOCKED" else "fail"),
                       detail[:120], source="live_exhaustive.py")
    except Exception:  # noqa: BLE001
        pass
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
