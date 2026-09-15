"""LIVE server-level e2e — the RUNTIME ROUTER over pre-built agents, through HTTP.

Agents are PRE-BUILT (seed the demo fleet: start the server with EVENTS_SEED_AGENTS=1). Then the
concierge routes an end user's utterance to one of four outcomes:
  • answer-now via an existing agent (real MCP tool → real number),
  • create a standing flow (AP), and REUSE it on a repeat (dedup),
  • DECLINE when no agent fits (never invents one),
  • CONNECT-NEEDED when a per-user integration isn't logged in.

Prereqs (see the events docs (MCP_SETUP.md)):
  registry with cuga-apps config +  the server:
    # CUGA (the worker) on :7860, then the events SERVICE on :8100 — the combined mount and its
    # EVENTS_ENABLED flag are gone, so these are two processes now.
    .venv/bin/python -m uvicorn cuga.backend.server.main:app --port 7860
    EVENTS_WORKER_BACKEND=cuga EVENTS_SEED_AGENTS=1 \\
      .venv/bin/python -m cuga.backend.events.service
Env: EVENTS_SERVER_URL (default http://localhost:7860).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("EVENTS_SERVER_URL", "http://localhost:7860").rstrip("/")


def _get(path, timeout=120):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return r.status, json.load(r)


def _post(path, body, timeout=400):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except Exception:  # noqa: BLE001
            return e.code, {"error": e.read().decode(errors="replace")[:400]}


def _concierge(text, thread="web:e2e"):
    c, rep = _post("/api/concierge", {"text": text, "thread_id": thread})
    return c, json.dumps(rep.get("reply", rep))


def main() -> int:
    print(f"server: {BASE}")
    checks = []

    code, st = _get("/api/events/status")
    checks.append(
        ("status: enabled + worker_backend=cuga", code == 200 and st.get("worker_backend") == "cuga")
    )

    # pre-built agents are present (seeded, not concierge-created)
    code, cap = _concierge("list what you can do")
    print("  capabilities:", cap[:200])
    checks.append(("router sees PRE-BUILT agents (pricebot/papers)", "pricebot" in cap or "papers" in cap))

    # 1) answer-now via an existing agent → real MCP tool → number
    print("  answer-now (bitcoin → pricebot → cuga_finance)…")
    code, rep = _concierge("what is the current price of bitcoin in usd? just the number")
    print("  reply:", rep[:200])
    checks.append(
        ("answer-now → numeric price via a pre-built agent", code == 200 and any(ch.isdigit() for ch in rep))
    )

    # 2) create a standing flow, then 3) REUSE it (dedup)
    utter = "every 1 minute send me new arxiv papers on mixture of experts"
    code, rep1 = _concierge(utter, thread="web:flow")
    print("  flow create:", rep1[:160])
    checks.append(
        (
            "standing request → flow created (cron/poll)",
            any(w in rep1.lower() for w in ("armed", "created", "poll", "cron", "schedul", "every", "watch")),
        )
    )
    code, rep2 = _concierge(utter, thread="web:flow")
    print("  flow reuse:", rep2[:160])
    checks.append(("repeat request → REUSES flow (dedup, no duplicate)", "reus" in rep2.lower()))

    # 4) decline when nothing fits — never invents an agent
    code, rep = _concierge("book me a flight to tokyo next friday", thread="web:decline")
    print("  decline:", rep[:160])
    checks.append(
        (
            "no agent fits → DECLINE (mentions builder / no agent)",
            any(w in rep.lower() for w in ("builder", "no agent", "not set up", "don't have")),
        )
    )

    print("\n---")
    passed = sum(1 for _, ok in checks if ok)
    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print(f"\n{passed}/{len(checks)} checks passed")
    print("RESULT:", "PASS — router e2e green" if passed == len(checks) else "PARTIAL/FAIL")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.URLError as e:
        print(f"\nCannot reach {BASE} ({e}). Start registry + server (EVENTS_SEED_AGENTS=1) first.")
        sys.exit(2)
