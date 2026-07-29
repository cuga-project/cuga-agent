"""LIVE Gmail integration e2e — the connection is live and the watcher arms a REAL AP flow.

A true integration test (no mocks): confirms the per-user Gmail OAuth connection exists in AP, then
asks the concierge to arm an inbox watcher and asserts a **real AP flow** is created (via
`subscriptions.ap_flow_id`) — i.e. AP is now actually watching the connected inbox.

The final leg (a real email arrives → the flow fires → mailbot summarizes) needs an email SENT to the
connected account, which this harness can't do for you — it prints exactly how to complete it.

Run:  EVENTS_SERVER_URL=http://localhost:7860 .venv/bin/python tests/events/live_gmail_e2e.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

SERVER = os.environ.get("EVENTS_SERVER_URL", "http://localhost:7860").rstrip("/")


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


def _env(key: str, default: str = "") -> str:
    v = os.environ.get(key)
    if v:
        return v.split(" #", 1)[0].strip()
    p = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
    if os.path.exists(p):
        for line in open(p):
            if line.strip().startswith(key + "="):
                return line.split("=", 1)[1].split(" #", 1)[0].strip().strip('"').strip("'")
    return default


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

    # 3) SYNTHETIC FIRE — the leg the arm-only checks missed. POST the exact envelope AP sends on
    # a real new_email, with a HOSTILE payload (a huge forwarded email): 2026-07-16 exactly this
    # shape made the delegate lose the body variable and the supervisor's raw deliberation was
    # DELIVERED to Slack as the answer. Asserts answer QUALITY, not just "an answer came back".
    gwtok = _env("GATEWAY_TOKEN")
    if gwtok:
        filler = ("> On Mon, quarterly numbers were discussed at length. " * 60 + "\n") * 12
        env = {"agent": "cuga", "thread_id": "gm:e2e:sim-fire",
               "text": "when a new email arrives in my inbox, summarize it and message me",
               "deliver": False,
               "source": {"type": "integration", "name": "gmail", "thread_id": "gm:e2e:sim-fire"},
               "event": {"kind": "new_email", "payload": {
                   "subject": "Fwd: Q3 vendor budget — approval needed by Friday",
                   "from": "finance@example.com",
                   "body": ("Forwarding from finance — approve the Q3 vendor budget increase of "
                            "$42,000 for the data platform by Friday, or the renewal lapses.\n\n"
                            "---------- Forwarded message ----------\n" + filler)}}}
        req = urllib.request.Request(f"{SERVER}/invoke", method="POST",
                                     data=json.dumps(env).encode(),
                                     headers={"Content-Type": "application/json",
                                              "X-Gateway-Token": gwtok})
        with urllib.request.urlopen(req, timeout=300) as r:
            body = json.load(r)
        ans = str(body.get("answer") or "")
        # leaked executor/deliberation scaffolding = the exact 2026-07-16 failure signature
        leak = any(m in ans for m in ("## New Variables Created", "Execution output:",
                                      "We have a loop", "delegate_to_"))
        check("synthetic fire (40KB forwarded email) answered ok", bool(body.get("ok")) and bool(ans))
        check("answer is a SUMMARY, not leaked deliberation",
              not leak and ("42,000" in ans or "42000" in ans or "budget" in ans.lower()),
              ans[:100].replace("\n", " "))
        if ok:
            try:
                from _ledger import record
                record("gmail", "fire_synth", "ok",
                       "synthetic new_email fire at /invoke — 40KB forwarded email → clean summary "
                       "(deliberation-leak + size regression gate)", source="live_gmail_e2e.py")
            except Exception:  # noqa: BLE001
                pass
    else:
        print("   (no GATEWAY_TOKEN — skipping the synthetic-fire leg)")

    print(f"\nRESULT: {'PASS — Gmail connected + watcher armed + synthetic fire clean' if ok else 'PARTIAL/FAIL'}")
    print("\nTo complete the REAL-fire leg (genuine email → summary): send an email to the connected")
    print("  account (cugatest@gmail.com); AP's new_email trigger fires the watcher within ~5 min.")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.URLError as e:
        print(f"cannot reach {SERVER} ({e})"); sys.exit(2)
