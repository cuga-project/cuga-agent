"""The slash-command bridge: CUGA core → the eventing service, over plain HTTP.

This is the ONLY part of CUGA core that knows the eventing layer exists, which is why it lives in a
file of its own rather than mixed into ``run_routes``. Core imports nothing from
``cuga.backend.events``: it detects the intent (``/automate …``) and POSTs it. No shared database,
no shared objects, no bot tokens — one HTTP call, and the whole thing is inert when
``EVENTS_API_URL`` is unset, which is vanilla CUGA.

Used by ``/stream`` (the web chat) and by ``/run`` (a channel forwarding an utterance).
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from loguru import logger

# ── the slash forwarder: main-chat arming, without mounting the events layer ───────────────────
# CUGA core does not know how to arm anything, and shouldn't. But a user typing "/automate …" in
# the MAIN chat box must still reach the concierge — handed to the plain agent it tries to
# IMPLEMENT the schedule (a loop with sleeps), which is the silent-failure trap this whole feature
# exists to close. So core detects the intent and FORWARDS over HTTP. No events import, no shared
# DB, no bot tokens — just one POST.
# A leading @mention is tolerated: Slack/Discord normally strip it before we see the text, but that
# depends on a bot-id lookup succeeding. If it ever doesn't, "<@U123> /automate …" must still be
# recognised as arming — handing it to the plain agent is the silent-failure trap (it tries to
# IMPLEMENT the schedule), which is precisely what this feature exists to prevent.
# ── the master switch ──────────────────────────────────────────────────────────────────────────
# CUGA_EVENTS_ENABLED gates EVERY events-facing seam in core: whether /run and /run/agents are
# mounted, whether a roster is imported at startup, and whether `/automate …` is forwarded. Off by
# default, so a CUGA that was not deliberately configured for eventing has none of it.
#
# WHY A SWITCH AND NOT JUST THE INDIVIDUAL VARIABLES. Each seam used to turn itself on from
# whichever variable it happened to need — /run mounted because GATEWAY_TOKEN was set, the roster
# seeded because CUGA_SUPERVISOR_ROSTER was set. Both of those get set for other reasons, so
# eventing could switch on as a side effect of unrelated configuration, and there was no single
# place to say "not here". One explicit opt-in is answerable; four implicit ones are not.
#
# NOT the old ``EVENTS_ENABLED``. That one gated MOUNTING the events layer inside CUGA's process,
# and it is gone with combined mode. This gates core's outward-facing seams towards a SEPARATE
# events service, which is a different question with a different answer.
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def events_enabled() -> bool:
    """Is this CUGA configured to take part in eventing at all?"""
    raw = (os.environ.get("CUGA_EVENTS_ENABLED", "") or "").split(" #", 1)[0].strip().lower()
    return raw in _TRUTHY


SLASH_VERB_NAMES = frozenset({"automate", "watch", "schedule", "cron", "poll", "push", "cancel"})


def slash_verb(text: str) -> str | None:
    """The slash verb at the head of an utterance, or None. Plain string scanning, no regex.

    This began as `\\s*(?:<@[^>]+>\\s*)*/(automate|…)\\b` and then as `(?:\\s|<@[^>]+>)*/(…)`. Both
    are quantifiers applied to unbounded, attacker-supplied chat text, which CodeQL flags
    (py/polynomial-redos) — the second still degrades because the engine re-tries the alternation
    across a long run of spaces. Rather than keep tuning a pattern against a scanner, do the two
    things the pattern was for — skip leading whitespace and `<@…>` mentions, then read one word —
    with `lstrip`/`find`/`isalpha`. Every step is a single linear pass, so the pathological input
    simply does not exist.

    Behaviour is unchanged, including the `\\b` at the end: `/automate` and `/automate?` match,
    `/automated` and `/automate1` do not.
    """
    # Walk with a CURSOR, never a slice. `s = s[close+1:].lstrip()` copied the remainder on every
    # mention, so "<@=>" repeated made this quadratic — 3.5 ms at 4k mentions, 662 ms at 64k. That is
    # the same denial-of-service the regex was replaced to avoid, reintroduced by the replacement.
    # Indexing touches each character once.
    s = text or ""
    n = len(s)
    i = 0
    while i < n and s[i].isspace():
        i += 1
    while s.startswith("<@", i):
        close = s.find(">", i)
        if close < i + 3:  # `<@[^>]+>` needs a BODY: "<@>" is not a mention and the regex this
            break  # replaced did not skip it, so neither do we (differential-tested, 140k inputs)
        i = close + 1
        while i < n and s[i].isspace():
            i += 1
    if not s.startswith("/", i):
        return None
    i += 1
    j = i
    while j < n and s[j].isalpha():
        j += 1
    word = s[i:j].lower()
    if word not in SLASH_VERB_NAMES:
        return None
    nxt = s[j : j + 1]
    if nxt and (nxt.isalnum() or nxt == "_"):  # the \b: a word char here means a longer word
        return None
    return word


# Threads with an arming dialogue open, so a bare "yes" / "cancel" / "change the prompt to …" is
# forwarded too. Deliberately IN-MEMORY: core must not read the events store. It is a routing hint,
# not state — the eventing service holds the real parked entry (10-minute TTL) and is the only
# thing that can actually arm. Lost on restart, which costs the user one retype at worst.
_events_open_threads: set = set()


def events_api_url() -> str:
    return (os.environ.get("EVENTS_API_URL", "") or "").split(" #", 1)[0].strip().rstrip("/")


def forwards_to_events(query: str, thread_id: Optional[str]) -> bool:
    if not events_enabled() or not events_api_url():
        return False  # eventing off, or no service configured → plain chat, as before
    if slash_verb(query or ""):
        return True
    return bool(thread_id) and thread_id in _events_open_threads


async def forward_slash_to_events(
    query: str, thread_id: Optional[str], headers, channel: Optional[Dict[str, Any]] = None
) -> str:
    """POST the utterance to the eventing service's /api/concierge and return its reply text.

    Also tracks whether the dialogue is still open, straight off the structured `state` the events
    service returns — so the follow-up "yes" routes here without core ever querying anything.

    ``channel`` is the originating channel envelope when the utterance came from Slack/Telegram/
    Discord via /run. It rides along so the concierge arms with the right delivery target and under
    the right identity.
    """
    import httpx

    base = events_api_url()
    tok = (os.environ.get("GATEWAY_TOKEN", "") or "").split(" #", 1)[0].strip()
    hdrs = {"Content-Type": "application/json"}
    if tok:
        hdrs["X-Gateway-Token"] = tok
    # Carry identity through, or the flow arms under a different scope than the Studio queries
    # (armed, but invisible in the Flows tab — a bug we have already paid for once).
    for h in ("X-Tenant-Id", "X-Instance-Id", "X-User-Id"):
        if headers is not None and headers.get(h):
            hdrs[h] = headers.get(h)
    payload: Dict[str, Any] = {"text": query, "thread_id": thread_id}
    if channel:
        payload["channel"] = channel
        # The concierge resolves per-user identity from the channel's native sender id; without it
        # a Slack-armed flow lands in a different scope than the Studio lists.
        if channel.get("user"):
            hdrs.setdefault("X-Channel-User", str(channel["user"]))
    try:
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(f"{base}/api/concierge", headers=hdrs, json=payload)
        if r.status_code != 200:
            return f"The eventing service returned HTTP {r.status_code}. Nothing was armed."
        data = r.json() if r.content else {}
    except Exception as e:  # noqa: BLE001 — a down events service must not break chat
        logger.warning(f"slash forward to {base} failed: {e}")
        return f"Couldn't reach the eventing service at {base} ({e}). Nothing was armed."
    state = (data.get("state") or "").lower()
    if thread_id:
        if state in ("confirm", "needs_input"):
            _events_open_threads.add(thread_id)  # the next message is part of this dialogue
        else:
            _events_open_threads.discard(thread_id)  # armed / cancelled / plain answer → done
    return data.get("reply") or data.get("answer") or data.get("message") or ""
