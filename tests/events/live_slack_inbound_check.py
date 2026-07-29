"""LIVE Slack INBOUND check — fires REAL signed Slack events at the running server and confirms the
from-Slack code path end to end, WITHOUT the Slack console or a human posting a message.

This is the piece the other Slack harness can't do: it proves signature verification + the mention
gate + the concierge + the agent actually process a Slack message. It signs the payload with your
SLACK_SIGNING_SECRET exactly as Slack does, so a 200 + the answer in the log means the code is fine —
and if messages STILL don't work in real Slack, the problem is delivery (the Event Subscriptions
Request URL), not CUGA.

It sends two events:
  1) a DM  (channel_type=im)              — always reaches chat
  2) a channel message WITH the bot @mention — exercises the mention gate (EVENTS_SLACK_CHAT=mention)

Run:  EVENTS_SERVER_URL=http://localhost:7860 .venv/bin/python tests/events/live_slack_inbound_check.py
Reads .env for SLACK_BOT_TOKEN + SLACK_SIGNING_SECRET.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _line in (_ROOT / ".env").read_text().splitlines():
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.split(" #", 1)[0].strip())

SERVER = os.environ.get("EVENTS_SERVER_URL", "http://localhost:7860").rstrip("/")
SECRET = os.environ.get("SLACK_SIGNING_SECRET", "")
TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")


def _bot_user_id() -> str:
    try:
        r = urllib.request.urlopen(urllib.request.Request(
            "https://slack.com/api/auth.test", headers={"Authorization": f"Bearer {TOKEN}"}), timeout=10)
        return json.load(r).get("user_id", "")
    except Exception:  # noqa: BLE001
        return ""


def _post_event(event: dict) -> int:
    body = json.dumps({"type": "event_callback", "event": event})
    ts = str(int(time.time()))
    sig = "v0=" + hmac.new(SECRET.encode(), f"v0:{ts}:{body}".encode(), hashlib.sha256).hexdigest()
    req = urllib.request.Request(f"{SERVER}/api/events/slack/events", data=body.encode(),
                                 headers={"Content-Type": "application/json",
                                          "X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig})
    return urllib.request.urlopen(req, timeout=40).status


def main() -> int:
    if not SECRET:
        print("SLACK_SIGNING_SECRET not set — can't sign. (Server would accept unsigned, but this "
              "harness proves the signed path.)")
        return 1
    ok = True
    uid = _bot_user_id()
    print(f"bot user id: {uid or '(auth.test failed)'}\n")

    print("1) signed DM …")
    s1 = _post_event({"type": "message", "channel_type": "im", "channel": "D_PROBE",
                      "user": "U_PROBE", "text": "what is the capital of France?",
                      "ts": "1700000000.000100"})
    print(f"   → HTTP {s1}  ({'ok' if s1 == 200 else 'FAIL'})")
    ok = ok and s1 == 200

    print("2) signed channel message WITH @mention …")
    txt = (f"<@{uid}> " if uid else "") + "what is the capital of France?"
    s2 = _post_event({"type": "message", "channel_type": "channel", "channel": "C_PROBE",
                      "user": "U_PROBE", "text": txt, "ts": "1700000000.000200"})
    print(f"   → HTTP {s2}  ({'ok' if s2 == 200 else 'FAIL'})")
    ok = ok and s2 == 200

    print("\n" + ("✅ from-Slack code path works (200s). If real Slack still does nothing, the Event "
                  "Subscriptions Request URL isn't pointed at this server — check `make logs` for the "
                  "agent's answer, and api.slack.com → Event Subscriptions → Verified."
                  if ok else "❌ a POST failed — check SLACK_SIGNING_SECRET + the server log."))
    print("Tip: `make logs` should show the agent answering 'Paris' for both events above.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
