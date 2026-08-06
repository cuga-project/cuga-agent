"""Delivery backend selection — the single place that decides whether a channel's outbound
send is owned by **Activepieces** (an AP send-step appended to the flow) or by **CUGA itself**
(a direct adapter like ``slack_direct``).

Why this exists: Slack now delivers *directly* (bot token → chat.postMessage), bypassing AP — see
``slack_direct``. But a STANDING flow (cron/poll/push) that delivers to Slack used to always append
an AP send-step, which fails for a direct channel (there is no AP connection). This module closes
that gap: a scheduled/push flow whose sink is a *direct* channel keeps ``deliver=True`` (no AP send
step) and, when the fired run reaches ``/invoke``, CUGA sends the answer itself via ``send_direct``.

One knob per channel: ``EVENTS_<CHANNEL>_BACKEND`` (``direct`` | ``ap``). Slack defaults to
``direct``; telegram/discord default to ``ap`` (their AP round-trip is verified live). Flip a
channel to direct here the day its direct adapter lands — no other code changes.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("events.delivery")

# Per-channel default backend. Override at runtime with EVENTS_<CHANNEL>_BACKEND=direct|ap.
_DEFAULT_BACKEND = {
    "slack": "direct",     # direct is the default (bot token); AP path behind EVENTS_SLACK_BACKEND=ap
    "telegram": "direct",  # direct long-poll (getUpdates/sendMessage, no AP); AP webhook behind EVENTS_TELEGRAM_BACKEND=ap
    "discord": "direct",   # direct Gateway (instant, no public URL); AP polling behind EVENTS_DISCORD_BACKEND=ap
    "web": "direct",       # the browser: no socket to push into, so its transport is a durable
                           # per-thread mailbox the UI drains by cursor — see web_inbox.
}


def channel_backend(channel: str) -> str:
    """Return 'direct' or 'ap' for a channel sink. Env override wins over the built-in default."""
    ch = (channel or "").lower()
    env = os.environ.get(f"EVENTS_{ch.upper()}_BACKEND")
    if env:
        return env.strip().lower()
    return _DEFAULT_BACKEND.get(ch, "ap")


def is_direct(channel: str) -> bool:
    """True when CUGA (not AP) owns this channel's outbound send."""
    return channel_backend(channel) == "direct"


async def send_direct(channel: str, target: str, text: str, locus: str = "",
                      scope: str = "", meta: dict | None = None) -> tuple[bool, str]:
    """CUGA-side outbound send for a direct channel. Returns (ok, reason).

    ``locus`` is the thread anchor from the gw thread id (principal.channel_locus): Slack posts
    into that thread (thread_ts), Discord replies to that message id — a flow armed in a thread
    delivers INTO the thread instead of the channel root.

    ``scope``/``meta`` are only consumed by the ``web`` mailbox, whose "send" is a durable row that
    has to remember whose it is and which flow produced it."""
    ch = (channel or "").lower()
    if ch == "web":
        # The browser has no socket, so delivery is a row keyed by the thread that armed the flow.
        # ``target`` IS the thread id here (app.py falls back to it when a thread has no gw origin).
        if not target:
            return False, "web: no thread_id to deliver to"
        from . import web_inbox
        if web_inbox.store() is None:
            return False, "web: no mailbox mounted"
        m = meta or {}
        web_inbox.put(scope=scope, thread_id=target, text=text, agent=str(m.get("agent") or ""),
                      subscription_id=str(m.get("subscription_id") or ""),
                      flow_name=str(m.get("flow_name") or ""),
                      event_kind=str(m.get("event_kind") or ""))
        return True, "ok"
    if ch == "slack":
        from . import slack_direct
        res = await slack_direct.send_message(target, text, thread_ts=locus or None)
        ok = bool(res.get("ok"))
        return ok, ("ok" if ok else f"slack: {res.get('error') or res}")
    if ch == "discord":
        from . import discord_direct
        res = await discord_direct.send_message(target, text, reply_to=locus)
        ok = bool(res.get("ok"))
        return ok, ("ok" if ok else f"discord: {res.get('error') or res}")
    if ch == "telegram":
        from . import telegram_direct
        # locus (a message id) → threaded reply; a scheduled fire has none, so it posts to the chat.
        res = await telegram_direct.send_message(target, text, reply_to=(locus or None))
        ok = bool(res.get("ok"))
        return ok, ("ok" if ok else f"telegram: {res.get('error') or res}")
    log.warning("no direct sender for channel %s (target=%s) — dropping", channel, target)
    return False, f"no direct sender for '{channel}'"
