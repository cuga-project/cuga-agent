"""Live Telegram check — the one channel that runs on Activepieces (not a direct backend).

Telegram is AP-backed: the AP ``telegram-bot`` piece receives (webhook) and sends. This harness
proves every leg we CAN without a human: the bot token is valid (getMe), the delivery leg works
(sendMessage, if you pass a chat id), the descriptor is correct, and arming goes through AP. The one
step no code can do is a human sending the bot a message for the full inbound round-trip.

Run:
    EVENTS_SERVER_URL=http://localhost:7860 .venv/bin/python tests/events/live_telegram_check.py
    # optional, to prove the delivery leg end-to-end:
    TELEGRAM_TEST_CHAT_ID=<your chat id> .venv/bin/python tests/events/live_telegram_check.py
Reads .env for TELEGRAM_BOT_TOKEN + EVENTS_TELEGRAM_BOT_USERNAME.
"""
import json
import os
import sys
import urllib.error
import urllib.request

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SERVER = os.environ.get("EVENTS_SERVER_URL", "http://localhost:7860")


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
    tok = _env("TELEGRAM_BOT_TOKEN")
    if not tok:
        print("SKIP — no TELEGRAM_BOT_TOKEN"); return 0
    api = f"https://api.telegram.org/bot{tok}"
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        ok = ok and cond
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

    # 1) bot token valid (real Telegram API)
    _, who = _http("GET", f"{api}/getMe")
    me = who.get("result", {})
    check("bot token valid (getMe)", who.get("ok"), f"@{me.get('username')} id={me.get('id')}")
    # the @handle must match EVENTS_TELEGRAM_BOT_USERNAME (drives the /start deep-link)
    want_handle = _env("EVENTS_TELEGRAM_BOT_USERNAME").lstrip("@")
    if want_handle:
        check("bot username matches EVENTS_TELEGRAM_BOT_USERNAME",
              (me.get("username") or "").lower() == want_handle.lower(),
              f"getMe=@{me.get('username')} vs .env @{want_handle}")

    # 2) delivery leg (optional — needs a chat id you've messaged the bot from)
    chat = os.environ.get("TELEGRAM_TEST_CHAT_ID", "").strip()
    if chat:
        _, r = _http("POST", f"{api}/sendMessage",
                     {"chat_id": chat, "text": "✅ live_telegram_check: delivery leg OK"})
        check("bot can send (sendMessage)", r.get("ok"), r.get("description") or "sent")
    else:
        print("  [INFO] set TELEGRAM_TEST_CHAT_ID=<your chat id> to prove the delivery leg")

    # 3) descriptor sanity (offline) — Telegram is AP-backed, minute-granularity send action
    sys.path.insert(0, os.path.join(REPO, "src", "cuga", "backend", "events"))
    import flows  # noqa: E402
    d = flows.CHANNELS["telegram"]
    check("descriptor: reply→chat, send_text_message wiring",
          "{{trigger" in d["native_ref"] and "chat" in d["dynamic_props"])

    # 4) arm the inbound flow through AP (needs the server + AP up)
    _, r = _http("POST", f"{SERVER}/api/events/admin/channels/telegram/arm",
                 {}, {"content-type": "application/json", "x-user-id": "admin"})
    armed = r.get("ok") and (r.get("ap_flow_id") or r.get("backend"))
    check("arm telegram inbound (via AP)", bool(armed),
          r.get("ap_flow_id") or r.get("error") or "needs server+AP")

    print(f"\nRESULT: {'PASS — Telegram token + wiring confirmed' if ok else 'PARTIAL/FAIL'}")
    print("\nOne human step (no code can do it): message the bot @{} → it round-trips through AP → "
          "/invoke → the concierge → reply.".format(me.get("username", "<bot>")))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
