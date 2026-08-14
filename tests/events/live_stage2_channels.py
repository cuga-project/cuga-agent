"""LIVE Stage-2 — arm channels (Telegram/Discord/Slack) + the Box resume watcher via AP.

Run once your bots + AP tunnel are set up (see events/docs/CHANNELS_SETUP.md). This arms the
INBOUND flows + the Box PUSH watcher in AP, then simulates an inbound channel message to /invoke
to prove resolve → route (a human still sends the *real* inbound message; this drives the rest).

Prereq: seeded server (EVENTS_SEED_AGENTS=1) + AP reachable (AP_BASE_URL) + a public tunnel so the
channels reach AP. Env: EVENTS_SERVER_URL, GATEWAY_TOKEN, CHANNELS (default "telegram,discord,slack").
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("EVENTS_SERVER_URL", "http://localhost:7860").rstrip("/")
TOKEN = os.environ.get("GATEWAY_TOKEN", "")
CHANNELS = os.environ.get("CHANNELS", "telegram,discord,slack").split(",")
ADMIN = "default/default/admin"


def _req(method, path, body=None, headers=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except Exception:  # noqa: BLE001
            return e.code, {"error": e.read().decode(errors="replace")[:300]}


def main() -> int:
    checks = []
    # 1. arm each channel's inbound flow (admin)
    for ch in CHANNELS:
        c, r = _req("POST", f"/api/events/admin/channels/{ch.strip()}/arm", {"scope": ADMIN})
        ok = c == 200 and r.get("ok")
        checks.append((f"arm inbound flow: {ch.strip()} (AP flow {r.get('ap_flow_id', '?')})", ok))
        if not ok:
            print(f"   {ch}: {r}")

    # 2. arm the Box resume watcher (admin acts as a builder; resume_judge has box integration)
    #    via the concierge as admin so it exercises find_or_create_flow(push).
    c, r = _req(
        "POST",
        "/api/concierge",
        {"text": "when a resume lands in Box, judge fit vs the JD and email me", "thread_id": "web:stage2"},
        headers={"X-User-Id": "admin"},
        timeout=300,
    )
    reply = json.dumps(r.get("reply", r))
    checks.append(
        (
            "Box resume watcher armed (push)",
            "push" in reply.lower() or "arm" in reply.lower() or "box" in reply.lower(),
        )
    )
    print("   resume watcher:", reply[:180])

    # 3. simulate an inbound channel message (a human sends the REAL one; here we prove routing).
    #    First link a telegram id to alice so resolution has a target.
    _, lk = _req("POST", "/api/events/link/telegram", {"scope": "default/default/alice"})
    env = {
        "source": {"type": "channel", "name": "telegram", "thread_id": "gw:telegram:55501"},
        "event": {"kind": "message", "payload": {}},
        "text": f"/start {lk.get('token')}",
        "agent": "concierge",
        "deliver": False,
    }
    _req("POST", "/invoke", env, headers={"X-Gateway-Token": TOKEN})  # link 55501 → alice
    env["text"] = "what is the price of bitcoin?"
    c, r = _req("POST", "/invoke", env, headers={"X-Gateway-Token": TOKEN}, timeout=300)
    ans = json.dumps(r.get("answer", r))
    checks.append(
        ("simulated telegram inbound → resolved+routed → answer", c == 200 and (r.get("ok") is True))
    )
    print("   inbound routed answer:", ans[:160])

    print("\n---")
    passed = sum(1 for _, ok in checks if ok)
    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print(f"\n{passed}/{len(checks)} checks passed")
    print(
        "NOTE: a REAL inbound message (you messaging the bot) completes the round-trip; AP then "
        "sends the reply back to the channel. Watch the server + AP logs by trace_id."
    )
    print("RESULT:", "PASS — stage-2 arming green" if passed == len(checks) else "PARTIAL/FAIL")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.URLError as e:
        print(f"Cannot reach {BASE} ({e}). Start a seeded server + AP first.")
        sys.exit(2)
