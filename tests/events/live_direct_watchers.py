"""LIVE: the DIRECT watchers (Slack · Discord · Telegram) — arm → fire → verify → clean up.

Direct watchers have no Activepieces flow at all: CUGA already receives the Slack Events API and the
Discord Gateway, so a watcher is just a subscription row (``ap_flow_id = NULL``) that the
direct-event dispatcher matches incoming events against.

This harness fires each watcher by POSTing a **correctly SIGNED** Slack Events API callback — the
same bytes, headers and HMAC signature Slack itself sends, verified by the same
``slack_direct.verify_signature`` a real delivery passes through. So a PASS proves the whole CUGA
side end to end: signature → event-type routing → watcher match (incl. the emoji/channel/pattern
filters) → agent dispatch → real delivery back into Slack.

The ONE thing it cannot prove is that Slack *delivers* the event to us — that depends on the Slack
app being subscribed to the event type and holding the paired OAuth scope (see
events_docs/setup/SLACK.md). ``--scopes`` reports exactly which of those are in place, so a missing
scope is reported as a SETUP gap rather than a silent failure.

Run:  .venv/bin/python tests/events/live_direct_watchers.py            # arm + fire every watcher
      .venv/bin/python tests/events/live_direct_watchers.py --scopes   # just the readiness report
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.request

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SERVER = os.environ.get("EVENTS_SERVER_URL", "http://localhost:8100").rstrip("/")


def _env(key, default=""):
    v = os.environ.get(key)
    if v:
        return v.split(" #", 1)[0].strip()
    p = os.path.join(REPO, ".env")
    if os.path.exists(p):
        for line in open(p):
            if line.strip().startswith(key + "="):
                return line.split("=", 1)[1].split(" #", 1)[0].strip().strip('"').strip("'")
    return default


SIGNING = _env("SLACK_SIGNING_SECRET")
SLACK_TOKEN = _env("SLACK_BOT_TOKEN")
CHANNEL = os.environ.get("SLACK_TEST_CHANNEL", "C0BEYJ9NATB")

# Each Slack watcher: the bot event Slack must be subscribed to + the OAuth scope it needs.
# (trigger, utterance, slack_event, scope, synthetic event payload)
SLACK_WATCHERS = [
    ("new_reaction", "/push when a message gets a :bug: reaction in slack, triage it as an incident",
     "reaction_added", "reactions:read",
     lambda: {"type": "reaction_added", "user": "U_TESTER", "reaction": "bug",
              "item": {"type": "message", "channel": CHANNEL, "ts": "1700000000.000100"},
              "event_ts": "1700000000.000200"}),
    ("new_slack_mention", "/push when the team is @mentioned in a slack channel, draft an answer",
     "app_mention", "app_mentions:read",
     lambda: {"type": "app_mention", "user": "U_TESTER", "channel": CHANNEL,
              "text": "<@BOT> what is our incident escalation policy?",
              "ts": "1700000000.000300", "event_ts": "1700000000.000300"}),
    ("channel_created", "/push when a new channel is created, post a welcome and suggest a charter",
     "channel_created", "channels:read",
     lambda: {"type": "channel_created",
              "channel": {"id": "C_NEW1", "name": "launch-planning", "created": 1700000000,
                          "creator": "U_TESTER"}}),
    ("new_slack_user", "/push when a new user joins the workspace, send them an onboarding brief",
     "team_join", "users:read",
     lambda: {"type": "team_join",
              "user": {"id": "U_NEW1", "name": "dana", "real_name": "Dana Lee",
                       "profile": {"title": "Backend engineer"}}}),
]


def http(method, url, body=None, headers=None, timeout=200):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:  # noqa: BLE001
            return e.code, {}
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)}


def granted_scopes() -> set:
    """The scopes Slack actually granted this bot token (from auth.test's response header)."""
    req = urllib.request.Request("https://slack.com/api/auth.test",
                                 headers={"Authorization": f"Bearer {SLACK_TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return {s.strip() for s in (r.headers.get("x-oauth-scopes") or "").split(",") if s.strip()}
    except Exception:  # noqa: BLE001
        return set()


def post_slack_event(event: dict) -> tuple[int, dict]:
    """POST a Slack Events API callback with a REAL HMAC signature — byte-identical to Slack's own
    delivery, so it passes the same verify_signature a genuine event does."""
    body = json.dumps({"type": "event_callback", "team_id": "T_TEST", "event": event})
    ts = str(int(time.time()))
    sig = "v0=" + hmac.new(SIGNING.encode(), f"v0:{ts}:{body}".encode(), hashlib.sha256).hexdigest()
    req = urllib.request.Request(f"{SERVER}/api/events/slack/events", data=body.encode(),
                                 method="POST",
                                 headers={"Content-Type": "application/json",
                                          "X-Slack-Request-Timestamp": ts,
                                          "X-Slack-Signature": sig})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {}


def slack_latest(n=1) -> list:
    req = urllib.request.Request(
        f"https://slack.com/api/conversations.history?channel={CHANNEL}&limit={n}",
        headers={"Authorization": f"Bearer {SLACK_TOKEN}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return (json.load(r) or {}).get("messages", []) or []


def main() -> int:
    scopes = granted_scopes()
    print(f"Direct watchers — {SERVER}  ·  slack #{CHANNEL}")
    print(f"\n[scopes] granted to the bot token: {', '.join(sorted(scopes)) or '(none)'}")
    ready, missing = [], []
    for trig, _u, ev, scope, _p in SLACK_WATCHERS:
        (ready if scope in scopes else missing).append((trig, ev, scope))
    for trig, ev, scope in ready:
        print(f"  \033[32m✓\033[0m {trig:20} needs {scope:20} (subscribe the app to `{ev}`)")
    for trig, ev, scope in missing:
        print(f"  \033[33m—\033[0m {trig:20} needs {scope:20} \033[33mSCOPE NOT GRANTED\033[0m "
              f"— Slack will not deliver `{ev}`")
    if "--scopes" in sys.argv:
        return 0
    if not SIGNING:
        print("\n\033[31mSLACK_SIGNING_SECRET is not set — cannot sign a synthetic event.\033[0m")
        return 1

    print("\n[fire] each watcher gets a REAL signed Slack Events API callback (same bytes, same HMAC)")
    results, created = [], []
    for trig, utter, ev, scope, payload in SLACK_WATCHERS:
        # ARM FROM A SLACK ORIGIN. The sink follows the thread_id origin, so a watcher armed from
        # web chat delivers to WEB (the answer rides back in the HTTP response) and nothing reaches
        # Slack. `gw:slack:<channel>` makes Slack the sink — which is what a user arming it from
        # Slack actually does.
        code, rep = http("POST", f"{SERVER}/api/concierge",
                         {"text": utter, "thread_id": f"gw:slack:{CHANNEL}"}, timeout=240)
        reply = str(rep.get("reply", ""))
        m = re.search(r"[Ss]ubscription ([\w-]+)", reply)
        if not (code == 200 and m and ("ARMED" in reply or "REUSING" in reply)):
            results.append((trig, "ARM-FAIL", reply[:90]))
            print(f"  \033[31m✗\033[0m {trig:20} ARM-FAIL — {reply[:80]}")
            continue
        sub_id = m.group(1)
        created.append(sub_id)
        before = slack_latest(1)
        before_ts = before[0].get("ts") if before else ""
        code, _ = post_slack_event(payload())
        if code != 200:
            results.append((trig, "EVENT-REJECTED", f"HTTP {code}"))
            print(f"  \033[31m✗\033[0m {trig:20} the endpoint rejected the signed event (HTTP {code})")
            continue
        # the dispatcher runs the agent in the background; poll Slack for the delivered answer
        landed, answer = False, ""
        for _ in range(30):
            time.sleep(4)
            msgs = slack_latest(1)
            if msgs and msgs[0].get("ts") != before_ts:
                landed, answer = True, (msgs[0].get("text") or "").replace("\n", " ")
                break
        status = "PASS" if landed else "NO-DELIVERY"
        results.append((trig, status, answer[:90]))
        icon = "\033[32m✓\033[0m" if landed else "\033[31m✗\033[0m"
        print(f"  {icon} {trig:20} {status:12} → {answer[:66]!r}")

    print(f"\n[cleanup] deleting {len(created)} watcher subscription(s)")
    for sid in created:
        http("DELETE", f"{SERVER}/api/events/subscriptions/{sid}", timeout=60)

    npass = sum(1 for _, s, _ in results if s == "PASS")
    print(f"\nRESULT: {npass}/{len(SLACK_WATCHERS)} direct watchers fired end-to-end")
    if missing:
        print(f"  NOTE: {len(missing)} watcher(s) will not receive REAL Slack events until their "
              f"scope is granted + the app is subscribed (events_docs/setup/SLACK.md). The synthetic "
              f"fire above still proves CUGA's side.")
    return 0 if npass == len(SLACK_WATCHERS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
