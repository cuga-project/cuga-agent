"""Live Discord check — arms the Discord inbound flow and verifies every leg that can be checked
without waiting on Discord's POLLING trigger, then tells you the human step.

Discord differs from Telegram in three ways that matter for testing:
  1. ``new_message`` is a POLLING trigger over ONE channel (not a webhook) → replies are not instant;
     AP polls the channel on its schedule (default ~5 min).
  2. The bot needs the **Message Content Intent** (Discord Developer Portal → Bot → Privileged
     Gateway Intents) or the polled messages arrive with EMPTY content.
  3. Replies go to the message's ``channel_id`` — for a message in a THREAD that's the thread id,
     so replies land back in the thread.

What this harness automates (fast, deterministic):
  • connect the bot token → AP connection
  • arm the inbound flow watching your channel
  • verify the AP flow shape (trigger valid + channel input; send targets {{trigger.channel_id}})
  • confirm the bot can actually POST to the channel (the delivery leg)
Then it prints the one manual step (post a message, wait for the poll).

Run:
    EVENTS_SERVER_URL=http://localhost:7860 DISCORD_CHANNEL_ID=<channel id> \
      .venv/bin/python tests/events/live_discord_check.py

Reads .env for DISCORD_BOT_TOKEN, AP_EMAIL/AP_PASSWORD, AP_BASE_URL. DISCORD_CHANNEL_ID may also be
auto-discovered from the bot's first guild's first text channel if not set.
"""
import json
import os
import sys
import urllib.request
import urllib.error

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SERVER = os.environ.get("EVENTS_SERVER_URL", "http://localhost:7860")
APBASE = os.environ.get("AP_BASE_URL", "http://localhost:8081")
DISCORD_API = "https://discord.com/api/v10"


def _env(key, default=""):
    v = os.environ.get(key)
    if v:
        return v.split(" #", 1)[0].strip()
    p = os.path.join(REPO, ".env")
    if os.path.exists(p):
        for line in open(p):
            line = line.strip()
            if line.startswith(key + "="):
                return line.split("=", 1)[1].split(" #", 1)[0].strip().strip('"').strip("'")
    return default


def _http(method, url, body=None, headers=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    h = {"content-type": "application/json", "User-Agent": "Mozilla/5.0"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method=method)
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
    tok = _env("DISCORD_BOT_TOKEN")
    if not tok:
        print("SKIP — no DISCORD_BOT_TOKEN in env/.env"); return 0
    dhdr = {"Authorization": f"Bot {tok}", "User-Agent": "DiscordBot"}
    checks, ok = [], True

    def check(name, cond, detail=""):
        nonlocal ok
        ok = ok and cond
        checks.append((cond, name, detail))
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

    # channel to watch
    chan = os.environ.get("DISCORD_CHANNEL_ID", "").strip()
    if not chan:
        st, guilds = _http("GET", f"{DISCORD_API}/users/@me/guilds", headers=dhdr)
        if isinstance(guilds, list) and guilds:
            st, chans = _http("GET", f"{DISCORD_API}/guilds/{guilds[0]['id']}/channels", headers=dhdr)
            text = [c for c in (chans or []) if isinstance(c, dict) and c.get("type") == 0]
            if text:
                chan = text[0]["id"]
                print(f"  (auto-discovered channel #{text[0]['name']} = {chan})")
    if not chan:
        print("FAIL — set DISCORD_CHANNEL_ID (a text channel the bot can see)"); return 1

    print(f"server={SERVER}  channel={chan}\n")

    # 1) connect the bot token
    st, r = _http("POST", f"{SERVER}/api/events/connect/discord/token",
                  {"token": tok, "scope": "default/default"}, {"x-user-id": "admin"})
    check("connect bot token", st == 200 and r.get("ok"), r.get("connection") or str(r)[:80])

    # 2) arm the inbound flow watching the channel
    st, r = _http("POST", f"{SERVER}/api/events/admin/channels/discord/arm",
                  {"scope": "default/default", "channel": chan}, {"x-user-id": "admin"})
    flow_id = r.get("ap_flow_id")
    check("arm inbound flow", st == 200 and r.get("ok"), flow_id or str(r)[:120])

    # 3) verify the flow shape in AP
    if flow_id:
        _, auth = _http("POST", f"{APBASE}/api/v1/authentication/sign-in",
                        {"email": _env("AP_EMAIL"), "password": _env("AP_PASSWORD")})
        bt = auth.get("token")
        _, fl = _http("GET", f"{APBASE}/api/v1/flows/{flow_id}", headers={"authorization": f"Bearer {bt}"})
        v = (fl or {}).get("version", {})
        t = v.get("trigger", {})
        step1 = t.get("nextAction") or {}
        send = step1.get("nextAction") or {}
        tin = t.get("settings", {}).get("input", {})
        sin = send.get("settings", {}).get("input", {})
        check("trigger watches the channel", tin.get("channel") == chan, f"channel={tin.get('channel')}")
        check("trigger valid", t.get("valid") is True)
        check("send targets the message's channel_id (thread-safe)",
              sin.get("channel_id") == "{{trigger.channel_id}}", sin.get("channel_id"))
        check("flow ENABLED", fl.get("status") == "ENABLED", fl.get("status"))

    # 4) the delivery leg — can the bot post to the channel?
    st, r = _http("POST", f"{DISCORD_API}/channels/{chan}/messages",
                  {"content": "✅ live_discord_check: delivery leg OK"}, dhdr)
    check("bot can POST to the channel", bool(r.get("id")), r.get("id") or str(r)[:80])

    print(f"\n{sum(1 for c,_,_ in checks if c)}/{len(checks)} checks passed")
    print("RESULT:", "PASS — wiring confirmed" if ok else "FAIL")
    print("\nManual step to see a full round-trip:")
    print("  1. Discord Developer Portal → your bot → enable **Message Content Intent**.")
    print(f"  2. In the watched channel, post e.g. 'what is the price of bitcoin?'")
    print("  3. Wait for AP's poll (~up to 5 min) → the reply posts back in the channel/thread.")
    print("  (Check the run in AP → project ea::default-default → the inbound-discord flow.)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
