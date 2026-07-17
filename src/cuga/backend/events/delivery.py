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
    "telegram": "ap",      # AP webhook round-trip verified live
    "discord": "direct",   # direct Gateway (instant, no public URL); AP polling behind EVENTS_DISCORD_BACKEND=ap
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


async def send_direct(channel: str, target: str, text: str, locus: str = "") -> tuple[bool, str]:
    """CUGA-side outbound send for a direct channel. Returns (ok, reason).

    ``locus`` is the thread anchor from the gw thread id (principal.channel_locus): Slack posts
    into that thread (thread_ts), Discord replies to that message id — a flow armed in a thread
    delivers INTO the thread instead of the channel root."""
    ch = (channel or "").lower()
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
    log.warning("no direct sender for channel %s (target=%s) — dropping", channel, target)
    return False, f"no direct sender for '{channel}'"
