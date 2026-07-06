"""Direct Slack integration — bypasses Activepieces.

Slack's own API takes the bot token (``xoxb-…``) directly, so we don't need AP's OAuth2 connection
(the wall that blocked the AP path) NOR the AP slack piece (whose app-event trigger silently ate the
payload). This is the DEFAULT Slack backend; the AP path stays behind ``EVENTS_SLACK_BACKEND=ap`` so
we can revisit that bug later.

Flow:  Slack Events API ▸ POST /api/events/slack/events (this module) ▸ /invoke(concierge) ▸
        chat.postMessage back to the channel.

Setup (Slack app at api.slack.com/apps):
  • Bot Token Scopes: chat:write, channels:history, channels:read (+ groups:history for private)
  • Event Subscriptions → Request URL = <EVENTS_PUBLIC_URL>/api/events/slack/events
    → subscribe bot event ``message.channels`` (+ ``message.groups`` for private)
  • Invite the bot to the channel.
Env: SLACK_BOT_TOKEN (required) · SLACK_SIGNING_SECRET (recommended — verifies requests are Slack's).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time

import httpx


def bot_token() -> str:
    return (os.environ.get("SLACK_BOT_TOKEN", "") or "").split(" #", 1)[0].strip()


def signing_secret() -> str:
    return (os.environ.get("SLACK_SIGNING_SECRET", "") or "").split(" #", 1)[0].strip()


def verify_signature(headers, raw_body: str) -> tuple[bool, str]:
    """Verify Slack's request signature (X-Slack-Signature over 'v0:ts:body' with the signing
    secret). Returns (ok, reason). If no signing secret is configured we allow it but flag it —
    set SLACK_SIGNING_SECRET to lock this down."""
    secret = signing_secret()
    if not secret:
        return True, "unverified (SLACK_SIGNING_SECRET not set)"
    ts = headers.get("x-slack-request-timestamp") or headers.get("X-Slack-Request-Timestamp") or ""
    sig = headers.get("x-slack-signature") or headers.get("X-Slack-Signature") or ""
    if not ts or not sig:
        return False, "missing signature headers"
    try:
        if abs(time.time() - int(ts)) > 60 * 5:      # replay window
            return False, "stale timestamp"
    except ValueError:
        return False, "bad timestamp"
    base = f"v0:{ts}:{raw_body}".encode()
    mine = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return (hmac.compare_digest(mine, sig), "ok" if hmac.compare_digest(mine, sig) else "bad signature")


def should_process(event: dict) -> bool:
    """A real human message we should answer — not the bot's own posts, edits, joins, etc."""
    if not event or event.get("type") != "message":
        return False
    if event.get("bot_id") or event.get("subtype"):     # bot messages / edits / joins have a subtype
        return False
    if not (event.get("text") and event.get("channel")):
        return False
    return True


async def send_message(channel: str, text: str, thread_ts: str | None = None) -> dict:
    """Post a reply via chat.postMessage (bot token) — no AP connection needed. When ``thread_ts``
    is given the reply lands IN THAT THREAD (Slack roots a thread at that ts), so a threaded
    conversation stays threaded instead of spilling to the channel root."""
    tok = bot_token()
    if not tok:
        return {"ok": False, "error": "no SLACK_BOT_TOKEN"}
    body = {"channel": channel, "text": text}
    if thread_ts:
        body["thread_ts"] = thread_ts
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post("https://slack.com/api/chat.postMessage",
                         headers={"Authorization": f"Bearer {tok}",
                                  "content-type": "application/json; charset=utf-8"},
                         json=body)
        try:
            return r.json()
        except Exception:  # noqa: BLE001
            return {"ok": False, "error": f"HTTP {r.status_code}"}
