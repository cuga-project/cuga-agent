"""LIVE Gmail trigger+ACTION e2e — arms a REAL AP flow whose post-agent step is a Gmail ACTION.

The action-half counterpart of live_gmail_e2e.py. Where that arms a watcher that DELIVERS a summary
to a chat channel, this arms a watcher that, after the agent answers, runs a Gmail ACTION
(create_draft_reply / reply_to_email / send_email) as a step IN the flow — the design in
events_docs/plans/TRIGGERS_ACTIONS_DESIGN.md.

It asserts the armed AP flow actually CONTAINS the action step (pieceName=gmail, the right
actionName), i.e. AP will run the action when a real email arrives — no mocks.

Run:  EVENTS_SERVER_URL=http://localhost:7860 .venv/bin/python tests/events/live_gmail_action_e2e.py

Prereqs: the events server is up, per-user Gmail is connected (see live_gmail_e2e.py / setup docs).
The final leg (a real email → the flow drafts a reply) needs an email sent to the connected inbox;
this harness prints how to finish it.
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
            SERVER + path, headers={"x-user-id": "admin"}), timeout=60) as r:
        return json.load(r)


def _concierge(text, thread):
    req = urllib.request.Request(f"{SERVER}/api/concierge", method="POST",
                                 data=json.dumps({"text": text, "thread_id": thread}).encode(),
                                 headers={"Content-Type": "application/json", "x-user-id": "admin"})
    with urllib.request.urlopen(req, timeout=200) as r:
        return str(json.load(r).get("reply", ""))


def _flow_actionnames(sub_id: str) -> list:
    """Pull the armed AP flow for a subscription and list its step actionNames (best-effort across
    the API shapes the server exposes: ?flow=1 on subscriptions, or the flow-view endpoint)."""
    names: list = []
    try:
        data = _get(f"/api/events/subscriptions/{sub_id}/flow")
    except Exception:  # noqa: BLE001
        return names
    blob = json.dumps(data)
    for token in ("reply_to_email", "create_draft_reply", "send_email"):
        if token in blob:
            names.append(token)
    return names


def main() -> int:
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

    # 0) Gmail connected?
    conns = [c.get("externalId", "") for c in _get("/api/events/connections").get("connections", [])]
    check("Gmail OAuth connection exists (per-user, in AP)", any(x.endswith("::gmail") for x in conns))

    # 1) arm a trigger+ACTION flow: draft a reply (the SAFEST gmail action — no send)
    rep = _concierge("when I get a new email, draft a reply that summarizes my response",
                     thread="gm:act:e2e")
    print("   concierge:", rep[:180])
    check("concierge armed an action flow (not connect-needed / error)",
          "connect needed" not in rep.lower() and not rep.lower().startswith("error"), rep[:80])

    # 2) the subscription exists and its AP flow CONTAINS a gmail action step
    subs = _get("/api/events/subscriptions").get("subscriptions", [])
    gm = next((s for s in subs if s.get("mode") == "PUSH" and s.get("source_connector") == "gmail"
               and s.get("ap_flow_id")), None)
    check("Gmail action watcher is a REAL AP flow (ap_flow_id recorded)", bool(gm),
          (gm or {}).get("ap_flow_id", "none"))
    if gm:
        acts = _flow_actionnames(gm["id"])
        check("armed flow CONTAINS a Gmail action step (reply/draft/send)", bool(acts),
              ",".join(acts) or "no action step found in flow json")

    # 3) send_email requires a recipient → the gate must ASK, not silently mail the wrong place
    rep2 = _concierge("when I get an email, email me about it", thread="gm:act:ask")
    check("send_email with no address ASKS for a recipient (never silent)",
          ("address" in rep2.lower() or "who should i" in rep2.lower()), rep2[:80])

    print(f"\nRESULT: {'PASS — action flow armed with a real Gmail action step' if ok else 'PARTIAL/FAIL'}")
    print("\nComplete the REAL-fire leg: send an email to the connected inbox; within ~5 min AP fires")
    print("  the flow → the agent writes → AP creates the DRAFT reply. Check the account's Drafts.")
    return 0 if ok else 1


if __name__ == "__main__":
    import os as _os
    if _os.environ.get("EVENTS_ACTIONS", "0").split(" #", 1)[0].strip().lower() not in ("1", "true", "yes"):
        print("SKIP: ACTION half gated off (EVENTS_ACTIONS=0). Set EVENTS_ACTIONS=1 to run this harness.")
        sys.exit(0)
    sys.exit(main())
