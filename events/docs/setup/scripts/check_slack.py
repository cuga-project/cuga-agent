#!/usr/bin/env python3
"""Verify the Slack bot token — independent of CUGA/AP.

Mirrors the CUGA use case (post in a channel, get a reply): prove the token with auth.test (who am
I / which workspace) and, if you name a channel, chat.postMessage (the reply leg).

Keys:   SLACK_BOT_TOKEN
Extra:  SLACK_TEST_CHANNEL  (optional — a channel id the bot has been /invite'd to)
"""

import sys

from _common import PASS, FAIL, SKIP, load_env, request_json, title, ok, bad, warn, info


def main():
    env = load_env()
    title("Slack")
    tok = env.get("SLACK_BOT_TOKEN")
    if not tok:
        warn("SLACK_BOT_TOKEN not set — skipping")
        return SKIP

    h = {"Authorization": f"Bearer {tok}"}
    st, _, j, _ = request_json("POST", "https://slack.com/api/auth.test", headers=h)
    if not (j and j.get("ok")):
        bad(f"auth.test failed: {(j or {}).get('error', 'HTTP %s' % st)} — token invalid")
        return FAIL
    ok(f"token valid — team '{j.get('team')}', bot '{j.get('user')}' (id {j.get('user_id')})")

    ch = env.get("SLACK_TEST_CHANNEL")
    if not ch:
        info("set SLACK_TEST_CHANNEL (a channel id the bot is invited to) to post a real test message")
        return PASS

    st, _, j, _ = request_json(
        "POST",
        "https://slack.com/api/chat.postMessage",
        headers=h,
        data={"channel": ch, "text": "CUGA pre-req check: Slack bot token works."},
    )
    if j and j.get("ok"):
        ok(f"posted a test message to {ch}")
        return PASS
    err = (j or {}).get("error")
    hint = " — invite the bot first: /invite @your-bot" if err == "not_in_channel" else ""
    bad(f"chat.postMessage failed: {err}{hint}")
    return FAIL


if __name__ == "__main__":
    sys.exit(main())
