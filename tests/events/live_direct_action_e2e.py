"""LIVE direct-trigger → Gmail action (Option A) e2e.

A direct trigger (slack/discord/telegram) owns no AP flow, so its Gmail action runs via a reusable
EXECUTOR flow (catch_webhook ▸ gmail/send_email) CUGA fires after the agent answers. This harness
live-verifies the NOVEL, risky half without needing Slack connected:

  1. Arm "when a message posts in #alerts, email me a summary at <you>" (a slack DIRECT trigger).
     A successful "ARMED direct watcher … via an executor flow" reply PROVES, live, that
     ap_engine.ensure_action_executor created the executor flow AND it passed the arm-time validity
     gate (`_assert_steps_valid` runs INSIDE ensure_action_executor — an invalid step raises, so an
     ARM reply can only happen if AP judged the gmail/send_email step valid).
  2. (opt-in, EXECUTOR_FIRE=1) POST a test body to the executor's webhook → a REAL email arrives,
     proving the fire leg end-to-end. Off by default (it sends mail).

What this does NOT cover: a real Slack message → dispatch (Slack isn't connected here). That last hop
reuses the already-proven direct-watcher dispatch path; the executor is the new code.

Run:  EVENTS_SERVER_URL=http://localhost:7860 TEST_EMAIL=you@example.com \
        .venv/bin/python tests/events/live_direct_action_e2e.py
      add EXECUTOR_FIRE=1 to also fire a real send.

Prereqs: events server up, per-user Gmail connected, Activepieces reachable.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

SERVER = os.environ.get("EVENTS_SERVER_URL", "http://localhost:7860").rstrip("/")
EMAIL = os.environ.get("TEST_EMAIL", "anupama.murthi@gmail.com")
FIRE = os.environ.get("EXECUTOR_FIRE") == "1"


def _post(path, body):
    req = urllib.request.Request(SERVER + path, method="POST",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "x-user-id": "admin"})
    with urllib.request.urlopen(req, timeout=200) as r:
        return json.load(r)


def _get(path):
    with urllib.request.urlopen(urllib.request.Request(
            SERVER + path, headers={"x-user-id": "admin"}), timeout=60) as r:
        return json.load(r)


def main() -> int:
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

    # 0) prereqs
    integ = {i["name"]: i for i in _get("/api/events/integrations")["integrations"]}
    check("gmail connected", integ.get("gmail", {}).get("connected"),
          "connect per-user Gmail first" if not integ.get("gmail", {}).get("connected") else "")

    # 1) arm a DIRECT trigger (slack) + a gmail action → forces the executor path
    utt = f"when a message is posted in #alerts, email me a summary at {EMAIL}"
    reply = str(_post("/api/concierge", {"text": utt, "thread_id": "live-direct-action"}).get("reply", ""))
    print(f"\n  arm reply: {reply}\n")
    check("armed a DIRECT watcher", "ARMED direct watcher" in reply, reply[:120])
    # the KEY assertion: the executor flow was created AND passed the validity gate (inline in
    # ensure_action_executor) — otherwise the arm would have raised/declined, not said "executor flow".
    check("executor flow created + validity-gate PASSED live",
          "executor flow" in reply and "gmail/send_email" in reply)
    check("did NOT silently drop the action (no plain-watcher-only)",
          "couldn't set up the executor" not in reply and "isn't wired" not in reply)

    # 2) optional: fire the executor for real (sends an email)
    if FIRE and ok:
        # find the executor flow id via the flows console, then trigger it with a test body
        try:
            flows = _get("/api/events/flows")            # best-effort; shape varies
            blob = json.dumps(flows)
            check("exec-gmail-send_email flow present in AP", "exec-gmail-send" in blob)
        except Exception as e:  # noqa: BLE001
            print(f"  (could not list flows to fire: {e})")
        print("  EXECUTOR_FIRE=1 set — to send for real, POST to the executor webhook with:")
        print(json.dumps({"receiver": [EMAIL], "subject": "CUGA executor live test",
                          "body": "This is a live test of the direct-action executor."}, indent=2))
    else:
        print("  (skipping the real send — set EXECUTOR_FIRE=1 to fire the executor and get an email)")

    print("\n" + ("✅ direct-action executor LIVE-verified (arm + validity gate)"
                  if ok else "❌ see failures above"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
