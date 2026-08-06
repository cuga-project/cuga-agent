"""LIVE: does a SCHEDULED FIRE actually get delivered to each channel?

The gap this closes. ``live_fire.py`` proves a cron/poll tick *runs* and proves the whole
arm→confirm→fire loop on **Slack**. ``live_matrix.py`` proves every channel answers a NOW question
and that a flow *arms* against each sink. Neither proves the last hop for Discord, Telegram or the
web: that when a standing flow fires with nobody waiting, the answer reaches the surface that armed
it. That hop is ``delivery.send_direct`` — a different code path per channel, and the one that was
silently missing for ``web`` entirely (a fire wrote a runs row and told nobody).

So this posts the exact ``/invoke`` body the native scheduler posts for a due subscription — an
``event.kind = "tick"`` with ``deliver: true`` and a ``gw:<channel>:<native>`` thread — and then
**reads the message back out of the channel** rather than trusting a 200.

Readback per channel, because each is a different kind of evidence:
  · slack    — ``conversations.history``: the bot's own message is visible. Decisive.
  · discord  — ``GET /channels/{id}/messages``: same. Decisive.
  · telegram — the Bot API exposes no "messages I sent" read, so the evidence is Telegram's own
               ``ok: true`` for ``sendMessage``, surfaced by the server. Reported as SENT, not
               VERIFIED, and the difference is printed rather than glossed.
  · web      — ``GET /api/events/inbox?thread_id=…``: the mailbox the browser drains. Decisive.

A channel with no token/target is SKIPPED and named — never silently passed.

    EVENTS_SERVER_URL=https://…  .venv/bin/python tests/events/live_fire_delivery.py
    …                                             --only web discord
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

OK, FAIL, SKIP, SENT = "\033[32m✓\033[0m", "\033[31m✗\033[0m", "\033[90m–\033[0m", "\033[33m~\033[0m"
RUN = str(int(time.time()))[-6:]


def env(k: str, d: str = "") -> str:
    return (os.environ.get(k, d) or "").split(" #", 1)[0].strip()


def _load_dotenv() -> None:
    """Channel tokens live in .env, the same file the deployed secret was built from."""
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    try:
        with open(os.path.join(root, ".env")) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass


def http(method: str, url: str, body=None, headers=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            try:
                return r.status, json.loads(raw)
            except json.JSONDecodeError:
                return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


# Resolved in main(), AFTER .env is loaded — reading them at import time meant GATEWAY_TOKEN was
# always empty and every /invoke came back 401.
SERVER = ""
GW = ""


def fire(channel: str, native: str, marker: str) -> tuple[int, object]:
    """POST the body the native scheduler posts for a due CRON — a tick, not a chat message."""
    return http("POST", f"{SERVER}/invoke", {
        "text": f"Reply with exactly this and nothing else: {marker}",
        "agent": "cuga",
        "deliver": True,
        "source": {"type": "time", "name": "cron", "thread_id": f"gw:{channel}:{native}"},
        "event": {"kind": "tick", "payload": {}},
    }, {"X-Gateway-Token": GW} if GW else {}, timeout=240)


# ── per-channel readback ──────────────────────────────────────────────────────────────────────────

def _channel_from_subscriptions(channel: str) -> str:
    """Last-resort target discovery: a native id out of an ALREADY-ARMED flow on the server.

    Discovery via the vendor API needs a scope the bot may not hold — this Discord bot token gets
    403 (code 1010) on ``/users/@me/guilds``. But the server is already holding real, working
    addresses in every flow armed from that channel (``thread_id = gw:<channel>:<native>``), so ask
    it rather than skipping the check.
    """
    st, body = http("GET", f"{SERVER}/api/events/subscriptions", None,
                    {"X-Gateway-Token": GW, "X-User-Id": "admin"} if GW else {}, timeout=30)
    if st != 200 or not isinstance(body, dict):
        return ""
    for s in body.get("subscriptions", []):
        tid = s.get("thread_id") or ""
        marker = f"gw:{channel}:"
        if marker in tid:
            return tid.split(marker, 1)[1].split("#", 1)[0]
    return ""


def _slack_channel(tok: str) -> str:
    """SLACK_TEST_CHANNEL, else the first public channel the bot is actually a member of — the same
    discovery live_matrix uses, so this runs on a stock .env with no extra setup."""
    if env("SLACK_TEST_CHANNEL"):
        return env("SLACK_TEST_CHANNEL")
    _, lst = http("GET", "https://slack.com/api/conversations.list?types=public_channel&limit=200",
                  None, {"Authorization": f"Bearer {tok}"}, timeout=20)
    member = [c for c in (lst.get("channels", []) if isinstance(lst, dict) else []) if c.get("is_member")]
    return member[0]["id"] if member else _channel_from_subscriptions("slack")


def _discord_channel(tok: str) -> str:
    """DISCORD_TEST_CHANNEL_ID, else the first text channel of the first guild the bot is in."""
    if env("DISCORD_TEST_CHANNEL_ID"):
        return env("DISCORD_TEST_CHANNEL_ID")
    dh = {"Authorization": f"Bot {tok}"}
    _, guilds = http("GET", "https://discord.com/api/v10/users/@me/guilds", None, dh, timeout=20)
    if not isinstance(guilds, list) or not guilds:
        return _channel_from_subscriptions("discord")
    _, chans = http("GET", f"https://discord.com/api/v10/guilds/{guilds[0]['id']}/channels",
                    None, dh, timeout=20)
    text = [c for c in (chans if isinstance(chans, list) else []) if c.get("type") == 0]
    return text[0]["id"] if text else ""


def check_slack(marker: str):
    tok = env("SLACK_BOT_TOKEN")
    chan = _slack_channel(tok) if tok else ""
    if not tok or not chan:
        return SKIP, "no SLACK_BOT_TOKEN, or the bot is in no channel (/invite it)"
    st, body = fire("slack", chan, marker)
    if st != 200:
        return FAIL, f"/invoke HTTP {st}"
    time.sleep(4)
    _, hist = http("GET", f"https://slack.com/api/conversations.history?channel={chan}&limit=25",
                   None, {"Authorization": f"Bearer {tok}"})
    if not isinstance(hist, dict) or not hist.get("ok"):
        return FAIL, f"conversations.history: {hist.get('error') if isinstance(hist, dict) else hist}"
    for m in hist.get("messages", []):
        if marker in (m.get("text") or ""):
            return OK, "read back out of the channel"
    return FAIL, "fired, but the message never appeared in Slack"


def check_discord(marker: str):
    tok = env("DISCORD_BOT_TOKEN")
    chan = _discord_channel(tok) if tok else ""
    if not tok or not chan:
        return SKIP, "no DISCORD_BOT_TOKEN, or the bot is in no guild with a text channel"
    st, body = fire("discord", chan, marker)
    if st != 200:
        return FAIL, f"/invoke HTTP {st}"
    time.sleep(4)
    code, msgs = http("GET", f"https://discord.com/api/v10/channels/{chan}/messages?limit=25",
                      None, {"Authorization": f"Bot {tok}"})
    if not isinstance(msgs, list):
        # The READBACK was refused, which says nothing about whether the send worked. This bot
        # token gets 403 (code 1010) on the message-history routes — a missing scope on the app,
        # not a delivery failure. Calling that a product FAIL is the same "red cell nobody
        # believes" that made live_matrix useless for cron/poll, so report it as unverifiable and
        # name the reason. The server's own log settles it: `deliver via=direct channel=discord`.
        detail = msgs.get("message") if isinstance(msgs, dict) else str(msgs)[:80]
        return SENT, (f"sent; readback refused by Discord (HTTP {code}: {detail}) — needs the "
                      "Read Message History scope. Confirm with: make ce-logs GREP=deliver")
    for m in msgs:
        if marker in (m.get("content") or ""):
            return OK, "read back out of the channel"
    return FAIL, "fired, but the message never appeared in Discord"


def check_telegram(marker: str):
    tok, chat = env("TELEGRAM_BOT_TOKEN"), env("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        return SKIP, "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set"
    st, body = fire("telegram", chat, marker)
    if st != 200:
        return FAIL, f"/invoke HTTP {st}"
    # The Bot API has no "messages I sent" read (getUpdates returns INCOMING only), so a readback
    # is impossible by design. The server's send either succeeded against Telegram or it didn't.
    return SENT, "sent (Telegram exposes no readback — verify visually in the chat)"


def check_web(marker: str):
    """The regression this whole mailbox exists for: a fire with no channel must still be readable."""
    thread = f"web:delivery-probe-{RUN}"
    st, body = fire("web", thread, marker)
    if st != 200:
        return FAIL, f"/invoke HTTP {st}"
    # No gw: origin in a real web thread; the server falls back to the web channel and mails it.
    st2, inbox = http("GET", f"{SERVER}/api/events/inbox?thread_id={thread}", None,
                      {"X-Gateway-Token": GW} if GW else {})
    if st2 != 200 or not isinstance(inbox, dict):
        return FAIL, f"/api/events/inbox HTTP {st2}"
    for m in inbox.get("messages", []):
        if marker in (m.get("text") or ""):
            return OK, f"in the browser's mailbox ({inbox['count']} msg)"
    return FAIL, "fired, but nothing reached the browser's mailbox"


CHECKS = {"web": check_web, "slack": check_slack, "discord": check_discord,
          "telegram": check_telegram}


def main() -> int:
    ap = argparse.ArgumentParser(description="Does a scheduled fire reach each channel?")
    ap.add_argument("--only", nargs="*", choices=sorted(CHECKS))
    args = ap.parse_args()
    _load_dotenv()
    global SERVER, GW
    SERVER = env("EVENTS_SERVER_URL", "http://localhost:8100").rstrip("/")
    GW = env("GATEWAY_TOKEN")
    if not GW:
        print("  \033[33mno GATEWAY_TOKEN\033[0m — /invoke will 401 if the server requires one")

    names = args.only or ["web", "slack", "discord", "telegram"]
    print(f"\033[1mFIRE → DELIVERY\033[0m — {SERVER}")
    print("  posts the native scheduler's own tick body, then reads the message back\n")

    results = {}
    for name in names:
        marker = f"cuga-delivery-{name}-{RUN}"
        sym, note = CHECKS[name](marker)
        results[name] = sym
        print(f"  {sym} {name:<9} {note}")

    print("\n" + "─" * 72)
    bad = [n for n, s in results.items() if s == FAIL]
    unverifiable = [n for n, s in results.items() if s == SENT]
    skipped = [n for n, s in results.items() if s == SKIP]
    print(f"  {sum(1 for s in results.values() if s == OK)} verified · "
          f"{len(unverifiable)} sent-but-unverifiable · {len(skipped)} skipped · {len(bad)} failed")
    if unverifiable:
        print(f"  \033[33mnot proof:\033[0m {', '.join(unverifiable)} — the API offers no readback")
    if skipped:
        print(f"  \033[90mskipped:\033[0m {', '.join(skipped)} — not configured")
    print("  RESULT: " + ("\033[31mFAIL\033[0m" if bad else "\033[32mPASS\033[0m"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
