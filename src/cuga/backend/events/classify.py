"""Deterministic trigger classifier — the dry-run baseline & eval oracle.

The *real* concierge classifies via LLM tool-calling (events_docs/ARCHITECTURE.md). This module is a
dependency-free heuristic used for: (a) ``dry_run`` when no LLM is configured, and
(b) the acceptance eval's expected-label oracle. It is intentionally simple and
transparent — not a replacement for the LLM, a check on it.

Returns one of NOW | CRON | PUSH | POLL and a best-effort cadence/source.
"""

from __future__ import annotations

import re

# event verbs that imply a native app trigger (PUSH)
_PUSH = re.compile(
    r"\bwhen (a |an |someone |my |the )?\b|\bwhenever\b|\blands?\b|\barrives?\b|"
    r"\bopens?\b|\bis (opened|created|added|labeled|uploaded)\b|\bposts?\b",
    re.I,
)
# "only when it changes / crosses / moves / on a move" → POLL (an interval that emits on change).
# Verbs are restricted to change-signals so "when a PR opens"/"when it lands" stay PUSH.
_POLL = re.compile(
    r"\bonly (if|when)\b|\b(if|when) it (changes?|moves?|drops?|crosses?|rises?|"
    r"spikes?|jumps?|goes|hits)\b|\b(on|upon) a (move|change|dip|spike|jump|drop|"
    r"rise|swing)\b|\bwatch\b.*\b(change|move|>|<|%|threshold|drops?|rises?)\b|"
    r"\bnotify me (only|when)\b|"
    # "tell me only about new items" / "only if there are new ones": an interval that
    # emits only on NEW content is a POLL, not a plain CRON — the proven feed_watcher
    # misroute (armed CRON 3/3 on the dry-run planner, 2026-07-10).
    r"\bonly\b[^.]{0,40}\bnew\b",
    re.I,
)
# recurring clock words → CRON
_CRON = re.compile(
    r"\bevery\b|\bdaily\b|\bhourly\b|\bweekday\b|\beach (morning|day|week|friday|"
    r"monday|hour)\b|\bat \d{1,2}(:\d\d)?\s*(am|pm)?\b|\bcron\b",
    re.I,
)

# native sources → (app, event) — GENERATED from the trigger registry (triggers.py), so a new
# trigger's phrases are picked up here automatically. Longer (more specific) phrases are tried
# first: "review request" must win over the generic "PRs" phrase. Legacy rss/webhook rows keep
# their old labels for the dry-run planner.
try:
    from .triggers import classifier_sources as _registry_sources
except ImportError:  # bare import (tests put the events dir on sys.path)
    from triggers import classifier_sources as _registry_sources  # type: ignore

_SOURCES = [(re.compile(rx, re.I), se) for rx, se in _registry_sources()]
_SOURCES += [
    (re.compile(r"\brss|blog|feed\b", re.I), ("rss", "new_item")),
    (re.compile(r"\bwebhook\b", re.I), ("webhook", "catch_webhook")),
]

# word-numbers: dictated/voice input says "every five minutes", not "every 5 minutes"
_NUM_WORDS = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "fifteen": 15,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
}
_NUM_RX = r"(\d+|" + "|".join(_NUM_WORDS) + r")"
_UNIT_SECS = {
    "second": 1,
    "sec": 1,
    "minute": 60,
    "min": 60,
    "hour": 3600,
    "hr": 3600,
    "day": 86400,
    "week": 604800,
}
_INTERVAL = re.compile(rf"every\s+{_NUM_RX}?\s*(second|sec|minute|min|hour|hr|day)s?\b", re.I)
_AT_TIME = re.compile(r"\bat\s+(\d{1,2})(?::(\d\d))?\s*(am|pm)?", re.I)
_WEEKDAY = re.compile(r"\bweekday|mon(day)?[- ]?(to|through|-)?[- ]?fri(day)?\b", re.I)
# a bounded run: "for one hour", "for 45 minutes", "for the next 2 days", "for an hour"
_TTL = re.compile(
    rf"\bfor\s+(?:the\s+next\s+)?{_NUM_RX}\s*"
    r"(second|sec|minute|min|hour|hr|day|week)s?\b",
    re.I,
)


def _num(tok: str | None) -> int:
    if not tok:
        return 1  # "every minute" / "every hour"
    tok = tok.lower()
    return int(tok) if tok.isdigit() else _NUM_WORDS.get(tok, 1)


def classify(text: str) -> str:
    """Heuristic NOW | CRON | PUSH | POLL. Order matters: POLL beats CRON when both match
    (a timer that emits only on change is POLL, not plain CRON)."""
    t = text or ""
    if _POLL.search(t):
        return "POLL"
    if _PUSH.search(t) and not _CRON.search(t):
        return "PUSH"
    if _CRON.search(t):
        return "CRON"
    return "NOW"


def source_of(text: str) -> tuple[str, str] | None:
    """Best-effort (app, event) for a PUSH utterance, else None.

    Two-stage, because phrase length alone is a fragile tiebreak: "when someone posts in #help on
    DISCORD" matched slack's channel-message phrase and discord's by one character. So when the
    utterance NAMES a platform explicitly, a trigger of that platform wins over any other app's
    match — an unambiguous signal beats a longer regex."""
    ranked = source_candidates(text)
    return ranked[0] if ranked else None


def source_candidates(text: str) -> list[tuple[str, str]]:
    """ALL (app, event) candidates for a PUSH utterance, best first, deduped.

    ``source_of`` is this list's head. The full list is what the FlowSpec resolver needs: ONE
    distinct app among the hits means the trigger is unambiguous (armable without an LLM); several
    apps mean a genuine ambiguity the resolver must not guess through."""
    # Strip email ADDRESSES first — an address is an action recipient ("email me at me@gmail.com"),
    # never a trigger signal. Left in, the 'gmail' inside 'me@gmail.com' matched the gmail trigger and
    # a "when a PR opens … email me at …@gmail.com" mis-resolved to gmail/new_email (caught by the
    # verifier, 2026-07-19). Addresses don't help trigger detection, so masking them is safe.
    t = re.sub(r"[\w.+-]+@[\w.-]+\.\w+", " ", text or "")
    # Drop the branch region ("… if <cond> A, otherwise B") before trigger matching. The trigger is
    # in the "when <trigger>" clause; branch-condition words pollute it — "if it MENTIONS urgent"
    # matched github/new_gh_mention and a gmail branch flow went ambiguous (verifier-adjacent, 2026-07-20).
    t = re.sub(r"\bif\b.*", " ", t, flags=re.IGNORECASE | re.DOTALL)
    hits = [se for rx, se in _SOURCES if rx.search(t)]
    named = [se for se in hits if re.search(rf"\b{re.escape(se[0])}\b", t, re.I)]
    out: list[tuple[str, str]] = []
    for se in named + hits:
        if se not in out:
            out.append(se)
    return out


def ttl_of(text: str) -> int | None:
    """Bounded-run TTL in seconds ("… for one hour" → 3600), else None.

    The AP schedule piece has no end-date, so a TTL can only be honored on OUR side: the arm
    path stores expires_at and /invoke deletes the flow on its first tick past the deadline.
    Guard: the TTL phrase must not BE the cadence phrase ("check every hour" has no TTL)."""
    m = _TTL.search(text or "")
    if not m:
        return None
    iv = _INTERVAL.search(text or "")
    if iv and iv.start() <= m.start() < iv.end():
        return None  # "for" overlapped the cadence match — not a bound
    n, unit = _num(m.group(1)), m.group(2).lower()
    return n * _UNIT_SECS.get(unit, 60)


def cadence_of(text: str) -> dict:
    """Best-effort {'interval_seconds': n} or {'cron': '...'} for CRON/POLL, else {}.

    Precedence: a NUMBERED interval ("every 5 minutes", "every five minutes") wins; then an
    anchored time ("every day at 9am" → cron — the unit-only 'every day' must NOT eat the 9am);
    then a unit-only interval ("every hour" → 3600)."""
    m = _INTERVAL.search(text or "")
    if m and m.group(1):
        return {"interval_seconds": _num(m.group(1)) * _UNIT_SECS.get(m.group(2).lower(), 60)}
    at = _AT_TIME.search(text or "")
    if at:
        hour = int(at.group(1)) % 12
        if (at.group(3) or "").lower() == "pm":
            hour += 12
        minute = int(at.group(2) or 0)
        dow = "1-5" if _WEEKDAY.search(text or "") else "*"
        return {"cron": f"{minute} {hour} * * {dow}"}
    if m:
        return {"interval_seconds": _num(None) * _UNIT_SECS.get(m.group(2).lower(), 60)}
    return {}


def decision(text: str) -> dict:
    """Full heuristic decision for dry-run: {mode, source, event, cadence}."""
    mode = classify(text)
    out: dict = {"mode": mode}
    if mode == "PUSH":
        se = source_of(text)
        if se:
            out["source"], out["event"] = se
    if mode in ("CRON", "POLL"):
        out["cadence"] = cadence_of(text)
    return out
