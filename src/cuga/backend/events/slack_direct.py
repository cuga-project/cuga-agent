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
    try:
        from .secret_seam import secret as _secret
    except ImportError:  # flat load (tests put the events dir on sys.path)
        from secret_seam import secret as _secret
    return _secret("SLACK_BOT_TOKEN")


def signing_secret() -> str:
    try:
        from .secret_seam import secret as _secret
    except ImportError:  # flat load (tests put the events dir on sys.path)
        from secret_seam import secret as _secret
    return _secret("SLACK_SIGNING_SECRET")


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
        if abs(time.time() - int(ts)) > 60 * 5:  # replay window
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
    if event.get("bot_id") or event.get("subtype"):  # bot messages / edits / joins have a subtype
        return False
    if not (event.get("text") and event.get("channel")):
        return False
    return True


def chat_mode() -> str:
    """EVENTS_SLACK_CHAT: 'all' (default — every channel message reaches the concierge) or
    'mention' (a channel message reaches CHAT only when it @mentions the bot; DMs always do)."""
    return (os.environ.get("EVENTS_SLACK_CHAT", "all").split(" #", 1)[0].strip().lower()) or "all"


_BOT_UID = {"id": ""}


async def bot_user_id() -> str:
    """The bot's own user id, for `<@U…>` mention detection. SLACK_BOT_USER_ID in .env wins;
    otherwise resolved once via auth.test and cached for the process (it never changes)."""
    env = (os.environ.get("SLACK_BOT_USER_ID", "") or "").split(" #", 1)[0].strip()
    if env:
        return env
    if _BOT_UID["id"]:
        return _BOT_UID["id"]
    tok = bot_token()
    if not tok:
        return ""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post("https://slack.com/api/auth.test", headers={"Authorization": f"Bearer {tok}"})
            _BOT_UID["id"] = (r.json() or {}).get("user_id", "") or ""
    except Exception:  # noqa: BLE001
        return ""
    return _BOT_UID["id"]


async def mention_gate(event: dict) -> tuple[bool, str]:
    """(reaches CHAT?, text with the bot's mention stripped).

    'mention' mode: a CHANNEL message reaches the concierge only when it @mentions the bot; a DM
    is inherently addressed to the bot and always passes. This gates CHAT only — channel-message
    WATCHERS must still see the gated traffic (the caller dispatches them separately), else arming
    'watch #incidents' and enabling mention mode would silently kill the watcher."""
    text = event.get("text") or ""
    if chat_mode() != "mention" or event.get("channel_type") == "im":
        return True, text  # a Slack `im` is strictly 1:1 with the bot
    uid = await bot_user_id()
    tok = f"<@{uid}>"
    if uid and tok in text:
        return True, text.replace(tok, " ").strip()
    # a reply in a thread the BOT rooted (e.g. it posted a trigger's answer and a human answers
    # back) is addressed to the bot even without a mention — Telegram's privacy mode delivers
    # replies-to-bot for the same reason. `parent_user_id` is the thread root's author.
    if uid and event.get("thread_ts") and event.get("parent_user_id") == uid:
        return True, text
    # a follow-up in a thread the bot has ANSWERED IN ("@bot weather in NY?" → answer →
    # "what about NYC?") — the mention rooted the thread at the USER's message, so
    # parent_user_id is the user; what makes it a conversation is that the bot replied.
    # send_message records those threads; the API fallback survives a reload.
    if (
        uid
        and event.get("thread_ts")
        and await _bot_in_thread(str(event.get("channel") or ""), str(event.get("thread_ts")), uid)
    ):
        return True, text
    return False, text


# threads the bot has replied in — (channel, thread_ts) → expiry. In-process cache in front of a
# conversations.replies fallback, so follow-ups keep working across a `make reload`.
_THREADS: dict = {}
_THREAD_TTL_SECS = 24 * 3600


def remember_thread(channel: str, thread_ts: str) -> None:
    if len(_THREADS) > 4000:  # bounded: drop expired, then oldest
        now = time.time()
        for k in [k for k, exp in _THREADS.items() if exp < now][:2000] or list(_THREADS)[:2000]:
            _THREADS.pop(k, None)
    _THREADS[(channel, thread_ts)] = time.time() + _THREAD_TTL_SECS


async def _bot_in_thread(channel: str, thread_ts: str, uid: str) -> bool:
    exp = _THREADS.get((channel, thread_ts))
    if exp and exp > time.time():
        return True
    tok = bot_token()
    if not (tok and channel and thread_ts):
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                "https://slack.com/api/conversations.replies",
                params={"channel": channel, "ts": thread_ts, "limit": 30},
                headers={"Authorization": f"Bearer {tok}"},
            )
            msgs = (r.json() or {}).get("messages") or []
    except Exception:  # noqa: BLE001
        return False
    if any(m.get("user") == uid for m in msgs):
        remember_thread(channel, thread_ts)
        return True
    return False


async def fetch_message_text(channel: str, ts: str) -> str:
    """The text of the message at ``ts`` — "" if it can't be read.

    POINTER-SHAPED EVENTS. Slack's `reaction_added` / `reaction_removed` / `star_added` carry only
    `item.channel` + `item.ts`; the message itself is NOT in the payload. So a watcher armed as
    "when someone reacts :bug:, review the code" reached the agent with a reaction and no code, and
    the agent truthfully answered that it had nothing to review — no error anywhere.

    Resolving the pointer here (rather than giving the agent a Slack tool) keeps the bot token in
    the one module that already owns it, and fixes every pointer-shaped trigger at once.

    Needs `channels:history` — the same scope SLACK.md already requires for `message.channels`, so
    no new permission. Returns "" on any failure: a watcher that fires with less context is better
    than one that does not fire.
    """
    tok = bot_token()
    if not (tok and channel and ts):
        return ""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                "https://slack.com/api/conversations.replies",
                params={"channel": channel, "ts": ts, "limit": 1, "inclusive": "true"},
                headers={"Authorization": f"Bearer {tok}"},
            )
        msgs = (r.json() or {}).get("messages") or []
    except Exception:  # noqa: BLE001
        return ""
    return str((msgs[0] or {}).get("text") or "") if msgs else ""


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
        remember_thread(channel, thread_ts)  # follow-ups in this thread reach chat sans mention
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {tok}", "content-type": "application/json; charset=utf-8"},
            json=body,
        )
        try:
            return r.json()
        except Exception:  # noqa: BLE001
            return {"ok": False, "error": f"HTTP {r.status_code}"}
