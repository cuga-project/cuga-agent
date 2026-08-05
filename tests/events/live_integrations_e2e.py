"""LIVE integrations e2e — the four trigger modes + full-AP integrations (Box · GitHub · Gmail).

This is the **true end-to-end** harness for the integration story. Integrations run on **Activepieces**
(OAuth/token + the piece trigger), so this needs a live server WITH a live AP engine, the demo fleet
seeded, and — for the PUSH legs to actually arm — the integration **connected** in AP.

It exercises, over real HTTP against the running server:
  • NOW   — answer-now via a pre-built agent (real MCP tool → a number)
  • CRON  — arm a scheduled flow → an AP flow is created (verified via /subscriptions.ap_flow_id)
  • POLL  — arm a watch-on-change flow → an AP flow is created
  • PUSH  — Box / GitHub / Gmail: arm the integration watcher via the concierge. When the integration
            is CONNECTED, an AP push flow is created; when it isn't, the concierge correctly returns
            CONNECT-NEEDED. Both are PASS (the wiring is proven either way) — but only the connected
            path is a real AP-flow e2e.

Prereqs:
  registry (cuga-apps) + server with a live AP engine:
    EVENTS_ENABLED=1 EVENTS_WORKER_BACKEND=cuga EVENTS_SEED_AGENTS=1 AP_BASE_URL=… (AP up)
  For a FULL PUSH e2e, connect the integration first (per events_docs/setup/{BOX,GITHUB,GMAIL}.md):
    • Box/Gmail: GET /api/events/connect/{app}  (OAuth consent)
    • GitHub:    POST /api/events/connect/github/token  {token: <PAT>}
Env: EVENTS_SERVER_URL (default http://localhost:7860), GATEWAY_TOKEN (from .env).

Run:  EVENTS_SERVER_URL=http://localhost:7860 .venv/bin/python tests/events/live_integrations_e2e.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("EVENTS_SERVER_URL", "http://localhost:7860").rstrip("/")
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _env(key: str, default: str = "") -> str:
    v = os.environ.get(key)
    if v:
        return v.split(" #", 1)[0].strip()
    p = os.path.join(REPO, ".env")
    if os.path.exists(p):
        for line in open(p):
            if line.strip().startswith(key + "="):
                return line.split("=", 1)[1].split(" #", 1)[0].strip().strip('"').strip("'")
    return default


def _get(path, timeout=120):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return r.status, json.load(r)


def _post(path, body, timeout=400, headers=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except Exception:  # noqa: BLE001
            return e.code, {"error": e.read().decode(errors="replace")[:400]}


def _concierge(text, thread="web:e2e"):
    # Arming is CONFIRM-gated (HITL spec): the concierge proposes, a human approves. Play the human.
    c, rep = _post("/api/concierge", {"text": text, "thread_id": thread})
    for _ in range(4):
        state = (rep or {}).get("state")
        if state not in ("confirm", "needs_input"):
            break
        c, rep = _post("/api/concierge",
                       {"text": "yes" if state == "confirm" else text, "thread_id": thread})
    return c, str(rep.get("reply", rep))


def _subs():
    try:
        _, s = _get("/api/events/subscriptions")
        return s.get("subscriptions", [])
    except Exception:  # noqa: BLE001
        return []


def _find_sub(mode=None, source=None):
    for s in _subs():
        if (mode is None or s.get("mode") == mode) and (source is None or s.get("source_connector") == source):
            return s
    return None


def main() -> int:
    print(f"server: {BASE}")
    checks = []

    def check(name, ok, detail=""):
        checks.append((name, bool(ok)))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

    # ── preconditions ────────────────────────────────────────────────────
    code, st = _get("/api/events/status")
    ap_on = bool(st.get("ap_configured"))
    check("status: enabled + worker=cuga", code == 200 and st.get("worker_backend") == "cuga")
    check("AP engine configured (integrations need AP)", ap_on,
          "AP up" if ap_on else "AP NOT configured — CRON/POLL/PUSH can't arm")

    # which integrations are connected in AP right now (drives whether PUSH really arms)
    _, integ = _get("/api/events/integrations")
    conn = {i["name"]: i.get("status") for i in integ.get("integrations", [])}
    print("  integrations:", {k: conn.get(k) for k in ("box", "github", "gmail")})

    # ── NOW ──────────────────────────────────────────────────────────────
    print("\n[NOW] answer-now via a pre-built agent")
    code, rep = _concierge("what is the current price of bitcoin in usd? just the number")
    print("   →", rep[:160])
    check("NOW: numeric price via pricebot (real MCP tool)", code == 200 and any(c.isdigit() for c in rep))

    # ── CRON ─────────────────────────────────────────────────────────────
    print("\n[CRON] arm a scheduled flow (AP schedule piece)")
    code, rep = _concierge("every day at 9am send me new arxiv papers on mixture of experts",
                           thread="web:cron")
    print("   →", rep[:160])
    armed_cron = any(w in rep.lower() for w in ("armed", "created", "cron", "every", "schedul"))
    sub = _find_sub(mode="CRON")
    check("CRON: concierge armed a scheduled flow", armed_cron or bool(sub))
    check("CRON: AP flow id recorded on the subscription", bool(sub and sub.get("ap_flow_id")),
          (sub or {}).get("ap_flow_id", "no CRON subscription found"))

    # ── POLL ─────────────────────────────────────────────────────────────
    print("\n[POLL] arm a watch-on-change flow")
    code, rep = _concierge("watch bitcoin every 2 minutes and ping me on any move", thread="web:poll")
    print("   →", rep[:160])
    armed_poll = any(w in rep.lower() for w in ("armed", "created", "poll", "watch", "every"))
    subp = _find_sub(mode="POLL")
    check("POLL: concierge armed a watch flow", armed_poll or bool(subp))
    check("POLL: AP flow id recorded on the subscription", bool(subp and subp.get("ap_flow_id")),
          (subp or {}).get("ap_flow_id", "no POLL subscription found"))

    # ── PUSH · Box / GitHub / Gmail (full AP) ────────────────────────────
    push_cases = [
        ("box", "resume_judge", "when a resume lands in my Box, judge it against the JD and email me"),
        ("github", "pr_reviewer", "when a pull request opens on my repo, summarize it and message me"),
        ("gmail", "mailbot", "when an email from my boss arrives, summarize it and message me"),
    ]
    for app, agent, utter in push_cases:
        print(f"\n[PUSH · {app}] arm the {agent} watcher (AP piece trigger)")
        connected = conn.get(app) == "connected"
        code, rep = _concierge(utter, thread=f"web:push:{app}")
        print("   →", rep[:180])
        low = rep.lower()
        armed = bool(_find_sub(mode="PUSH", source=app))          # authoritative: a subscription exists
        connect_needed = ("connect" in low and app in low)
        # honest outcomes:  not connected → CONNECT-NEEDED · connected + no required input → ARMED ·
        # connected but the trigger needs a slot/input → the concierge asks for it. That "slot" is
        # per-agent: a github repo, a box folder, or a domain input like the job description that
        # resume_judge judges resumes against ("share the JD").
        needs_input = any(w in low for w in ("repo", "repository", "folder", "which ", "specify",
                                             "builder", "no push trigger", "job description", "jd",
                                             "share the", "provide the", "attach"))
        ok = armed or connect_needed or (connected and needs_input)
        state = ("ARMED (AP flow)" if armed else
                 "connect-needed (not connected — correct)" if connect_needed else
                 "connected → needs a repo/folder/input slot (correct)" if (connected and needs_input) else
                 f"unexpected: {rep[:80]}")
        check(f"PUSH {app}: routes to AP correctly [{state}]", ok)
        if armed:
            s = _find_sub(mode="PUSH", source=app)
            check(f"PUSH {app}: AP flow id recorded (true e2e)", bool(s and s.get("ap_flow_id")),
                  (s or {}).get("ap_flow_id", "armed but no ap_flow_id"))

    # ── WEBHOOK (generic inbound; direct, no AP) ─────────────────────────
    print("\n[WEBHOOK] POST an alert → incident_triage → response")
    gw = _env("GATEWAY_TOKEN")
    code, r = _post("/api/events/hook/monitoring",
                    {"alert": "HighCPU", "service": "checkout-api", "value": "97%", "threshold": "85%"})
    ans = (r.get("answer") or "")
    print("   →", ans[:160])
    check("WEBHOOK: inbound payload triaged by an agent",
          code == 200 and r.get("ok") and any(sev in ans for sev in ("P1", "P2", "P3", "sever")))

    # ── summary ──────────────────────────────────────────────────────────
    print("\n" + "─" * 60)
    passed = sum(1 for _, ok in checks if ok)
    print(f"{passed}/{len(checks)} checks passed")
    unconn = [a for a in ("box", "github", "gmail") if conn.get(a) != "connected"]
    if unconn:
        print(f"\nNOTE: {', '.join(unconn)} not connected → their PUSH shows CONNECT-NEEDED (wiring proven).")
        print("For a FULL AP push e2e, connect them (see events_docs/setup/) and re-run:")
        print("  • Box/Gmail: open  GET /api/events/connect/{box,gmail}  (OAuth consent)")
        print("  • GitHub:    POST /api/events/connect/github/token  {\"token\":\"<PAT>\"}")
    print("RESULT:", "PASS" if passed == len(checks) else "PARTIAL — see FAILs above")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.URLError as e:
        print(f"\nCannot reach {BASE} ({e}). Start the registry + server (EVENTS_SEED_AGENTS=1, AP up).")
        sys.exit(2)
