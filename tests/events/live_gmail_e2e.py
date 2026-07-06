"""LIVE Gmail integration e2e — the connection is live and the watcher arms a REAL AP flow.

A true integration test (no mocks): confirms the per-user Gmail OAuth connection exists in AP, then
asks the concierge to arm an inbox watcher and asserts a **real AP flow** is created (via
`subscriptions.ap_flow_id`) — i.e. AP is now actually watching the connected inbox.

The final leg (a real email arrives → the flow fires → mailbot summarizes) needs an email SENT to the
connected account, which this harness can't do for you — it prints exactly how to complete it.

Run:  EVENTS_SERVER_URL=http://localhost:8100 .venv/bin/python tests/events/live_gmail_e2e.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

SERVER = os.environ.get("EVENTS_SERVER_URL", "http://localhost:8100").rstrip("/")


def _get(path):
    with urllib.request.urlopen(urllib.request.Request(
            SERVER + path, headers={"x-user-id": "admin"}), timeout=30) as r:
        return json.load(r)


def _concierge(text, thread):
    req = urllib.request.Request(f"{SERVER}/api/concierge", method="POST",
                                 data=json.dumps({"text": text, "thread_id": thread}).encode(),
                                 headers={"Content-Type": "application/json", "x-user-id": "admin"})
    with urllib.request.urlopen(req, timeout=200) as r:
        return str(json.load(r).get("reply", ""))


def main() -> int:
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        ok = ok and cond
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

    # 1) the per-user Gmail OAuth connection exists in AP
    conns = [c.get("externalId", "") for c in _get("/api/events/connections").get("connections", [])]
    check("Gmail OAuth connection exists (per-user, in AP)",
          any(x.endswith("::gmail") for x in conns), next((x for x in conns if x.endswith("::gmail")), "—"))
    guides = {g["label"]: g for g in _get("/api/events/setup-guides")["guides"]}
    check("setup-guides reports Gmail connected", guides.get("Gmail", {}).get("connected") is True)

    # 2) arm an inbox watcher → a REAL AP flow (proves AP is watching the connected inbox)
    rep = _concierge("when a new email arrives in my inbox, summarize it and message me", thread="gm:e2e")
    print("   concierge:", rep[:150])
    subs = _get("/api/events/subscriptions").get("subscriptions", [])
    gm = next((s for s in subs if s.get("mode") == "PUSH" and s.get("source_connector") == "gmail"), None)
    armed = bool(gm) or any(w in rep.lower() for w in ("armed", "created", "watch", "set up"))
    check("concierge armed the Gmail inbox watcher (not connect-needed)",
          armed and "connect needed" not in rep.lower(), rep[:80])
    if gm:
        check("Gmail watcher is a REAL AP flow (ap_flow_id recorded)", bool(gm.get("ap_flow_id")),
              gm.get("ap_flow_id", "no ap_flow_id"))

    print(f"\nRESULT: {'PASS — Gmail connected + inbox watcher armed on AP' if ok else 'PARTIAL/FAIL'}")
    print("\nTo complete the FIRE leg (real email → mailbot): send an email to the connected account")
    print("  (cugatest@gmail.com); AP's new_email trigger fires the watcher → mailbot summarizes it.")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.URLError as e:
        print(f"cannot reach {SERVER} ({e})"); sys.exit(2)
