#!/usr/bin/env python3
"""Verify the Discord bot token — independent of CUGA/AP.

Mirrors the CUGA use case (post in a channel, get a reply): prove the token identifies the bot
(users/@me) and, if you name a channel, can POST a message (the reply leg). MESSAGE CONTENT INTENT
is a Gateway setting — it can't be read over REST; you confirm it live by messaging the bot.

Keys:   DISCORD_BOT_TOKEN
Extra:  DISCORD_TEST_CHANNEL_ID  (optional — a text channel the bot can post in)
"""
import sys

from _common import PASS, FAIL, SKIP, load_env, request_json, title, ok, bad, warn, info

API = "https://discord.com/api/v10"
UA = "DiscordBot (https://github.com/cuga-project/cuga-agent, 1.0)"


def main():
    env = load_env()
    title("Discord")
    tok = env.get("DISCORD_BOT_TOKEN")
    if not tok:
        warn("DISCORD_BOT_TOKEN not set — skipping")
        return SKIP

    h = {"Authorization": f"Bot {tok}", "User-Agent": UA}
    st, _, j, _ = request_json("GET", f"{API}/users/@me", headers=h)
    if st != 200 or not j:
        bad(f"users/@me failed (HTTP {st}) — token invalid")
        return FAIL
    ok(f"token valid — bot {j.get('username')} (id {j.get('id')})")

    ch = env.get("DISCORD_TEST_CHANNEL_ID")
    if not ch:
        info("set DISCORD_TEST_CHANNEL_ID to also post a real test message")
        info("reminder: enable MESSAGE CONTENT INTENT in the Developer Portal so the bot can read text")
        return PASS

    st, _, j, _ = request_json("POST", f"{API}/channels/{ch}/messages", headers=h,
                               data={"content": "CUGA pre-req check: Discord bot token works."})
    if st in (200, 201):
        ok(f"posted a test message to channel {ch}")
        return PASS
    bad(f"post failed (HTTP {st}): {(j or {}).get('message')} — is the bot in the server with Send Messages?")
    return FAIL


if __name__ == "__main__":
    sys.exit(main())
