#!/usr/bin/env python3
"""Verify the Telegram bot token — independent of CUGA/AP.

Mirrors the CUGA use case (DM the bot, get a reply): prove the token identifies a bot (getMe) and
can DELIVER a message (sendMessage) — the two halves of a chat round-trip.

Keys:   TELEGRAM_BOT_TOKEN
Extra:  TELEGRAM_CHAT_ID  (optional — set it to send a real test message to yourself)
"""

import sys

from _common import PASS, FAIL, SKIP, load_env, request_json, title, ok, bad, warn, info


def main():
    env = load_env()
    title("Telegram")
    tok = env.get("TELEGRAM_BOT_TOKEN")
    if not tok:
        warn("TELEGRAM_BOT_TOKEN not set — skipping")
        return SKIP

    api = f"https://api.telegram.org/bot{tok}"
    st, _, j, _ = request_json("GET", f"{api}/getMe")
    if st != 200 or not (j and j.get("ok")):
        bad(f"getMe failed (HTTP {st}) — token invalid")
        return FAIL
    me = j["result"]
    ok(f"token valid — bot @{me.get('username')} ({me.get('first_name')})")

    chat = env.get("TELEGRAM_CHAT_ID")
    if not chat:
        info("set TELEGRAM_CHAT_ID to also send a real test message")
        st, _, upd, _ = request_json("GET", f"{api}/getUpdates")
        ids = sorted(
            {str(u["message"]["chat"]["id"]) for u in (upd or {}).get("result", []) if "message" in u}
        )
        if ids:
            info("chat ids seen recently (DM the bot to populate this): " + ", ".join(ids))
        return PASS

    st, _, j, _ = request_json(
        "POST",
        f"{api}/sendMessage",
        data={"chat_id": chat, "text": "CUGA pre-req check: your Telegram bot token works."},
    )
    if st == 200 and j and j.get("ok"):
        ok(f"sent a test message to chat {chat} — check Telegram")
        return PASS
    bad(f"sendMessage failed (HTTP {st}): {(j or {}).get('description')}")
    return FAIL


if __name__ == "__main__":
    sys.exit(main())
