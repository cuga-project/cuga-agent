"""Live Slack check — verifies every leg we CAN against the real Slack + AP, and prints the two
manual steps Slack requires that no code can do for you.

Slack in Activepieces differs from Telegram/Discord:
  • Auth is **OAuth2** (not a bare bot token). The AP Slack piece needs an OAuth2 connection created
    by Slack's consent flow — a raw ``xoxb`` bot token does NOT satisfy AP's connection schema. So
    connecting Slack needs EVENTS_OAUTH_SLACK_CLIENT_ID/_SECRET set + the OAuth redirect, OR a
    connection created in the AP UI ("Connect" → Slack → Add to Slack).
  • The ``new-message`` trigger is **APP_WEBHOOK** (Slack Events API → instant, like Telegram). It
    requires the Slack app's **Event Subscriptions** Request URL to point at AP's app-events URL and
    the app subscribed to ``message.channels``.

So this harness proves: the bot token is valid, the bot can post (delivery leg), and the descriptor
is correct — then tells you the two Slack-app setup steps. Once the OAuth connection exists, arming
works via ``/api/events/admin/channels/slack/arm`` exactly like Telegram/Discord.

Run:
    EVENTS_SERVER_URL=http://localhost:7860 .venv/bin/python tests/events/live_slack_check.py
Reads .env for SLACK_BOT_TOKEN.
"""

import json
import os
import sys
import urllib.request
import urllib.error

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SERVER = os.environ.get("EVENTS_SERVER_URL", "http://localhost:7860")
SLACK = "https://slack.com/api"


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


def _http(method, url, body=None, headers=None, timeout=20):
    data = json.dumps(body).encode() if isinstance(body, (dict, list)) else (body.encode() if body else None)
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)}


def main():
    tok = _env("SLACK_BOT_TOKEN")
    if not tok:
        print("SKIP — no SLACK_BOT_TOKEN")
        return 0
    h = {"Authorization": f"Bearer {tok}", "content-type": "application/json; charset=utf-8"}
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        ok = ok and cond
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

    # 1) bot token valid (real Slack API)
    _, who = _http("POST", f"{SLACK}/auth.test", "", {"Authorization": f"Bearer {tok}"})
    check("bot token valid (auth.test)", who.get("ok"), f"team={who.get('team')} bot={who.get('user')}")

    # 2) a channel the bot is in
    _, ch = _http(
        "GET",
        f"{SLACK}/conversations.list?types=public_channel&limit=50",
        None,
        {"Authorization": f"Bearer {tok}"},
    )
    member = [c for c in ch.get("channels", []) if c.get("is_member")]
    chan = member[0]["id"] if member else None
    check(
        "bot is a member of a channel",
        bool(chan),
        f"#{member[0]['name']}={chan}" if member else "invite the bot to a channel",
    )

    # 3) the delivery leg — can the bot post? (real chat.postMessage)
    if chan:
        _, r = _http(
            "POST",
            f"{SLACK}/chat.postMessage",
            {"channel": chan, "text": "✅ live_slack_check: delivery leg OK"},
            h,
        )
        check("bot can post (chat.postMessage)", r.get("ok"), r.get("ts") or r.get("error"))

    # 4) descriptor sanity (offline)
    sys.path.insert(0, os.path.join(REPO, "src", "cuga", "backend", "events"))
    import flows  # noqa: E402

    d = flows.CHANNELS["slack"]
    check(
        "descriptor: reply→channel, ignoreBots, sendAsBot, DYNAMIC",
        d["native_ref"] == "{{trigger.channel}}"
        and d["trigger_const"].get("ignoreBots")
        and d["const"].get("sendAsBot")
        and "channel" in d["dynamic_props"],
    )

    # 5) arm the DIRECT backend (default): returns the Events URL to paste — no AP, no OAuth
    _, r = _http(
        "POST",
        f"{SERVER}/api/events/admin/channels/slack/arm",
        {},
        {"content-type": "application/json", "x-user-id": "admin"},
    )
    check(
        "arm slack (direct backend)",
        r.get("ok") and r.get("backend") == "direct",
        r.get("backend") or r.get("error"),
    )
    print(f"  [INFO] Events Request URL → {r.get('events_url', '?')}")
    print(f"  [INFO] signature verification: {r.get('signature_verification', '?')}")

    print(f"\nRESULT: {'PASS — direct Slack wiring + delivery confirmed' if ok else 'FAIL'}")
    print("\nOne Slack-app step (no code can do it for you):")
    print("  • Slack app → Event Subscriptions → Request URL = the events_url above,")
    print("    subscribe bot event 'message.channels'. Recommended: set SLACK_SIGNING_SECRET.")
    print("    Then post in the channel → instant reply (direct backend, no AP).")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
