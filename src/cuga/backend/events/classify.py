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
_PUSH = re.compile(r"\bwhen (a |an |someone |my |the )?\b|\bwhenever\b|\blands?\b|\barrives?\b|"
                   r"\bopens?\b|\bis (opened|created|added|labeled|uploaded)\b|\bposts?\b", re.I)
# "only when it changes / crosses / moves / on a move" → POLL (an interval that emits on change).
# Verbs are restricted to change-signals so "when a PR opens"/"when it lands" stay PUSH.
_POLL = re.compile(r"\bonly (if|when)\b|\b(if|when) it (changes?|moves?|drops?|crosses?|rises?|"
                   r"spikes?|jumps?|goes|hits)\b|\b(on|upon) a (move|change|dip|spike|jump|drop|"
                   r"rise|swing)\b|\bwatch\b.*\b(change|move|>|<|%|threshold|drops?|rises?)\b|"
                   r"\bnotify me (only|when)\b|"
                   # "tell me only about new items" / "only if there are new ones": an interval that
                   # emits only on NEW content is a POLL, not a plain CRON — the proven feed_watcher
                   # misroute (armed CRON 3/3 on the dry-run planner, 2026-07-10).
                   r"\bonly\b[^.]{0,40}\bnew\b", re.I)
# recurring clock words → CRON
_CRON = re.compile(r"\bevery\b|\bdaily\b|\bhourly\b|\bweekday\b|\beach (morning|day|week|friday|"
                   r"monday|hour)\b|\bat \d{1,2}(:\d\d)?\s*(am|pm)?\b|\bcron\b", re.I)

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

_INTERVAL = re.compile(r"every\s+(\d+)\s*(second|sec|minute|min|hour|hr|day)s?", re.I)
_AT_TIME = re.compile(r"\bat\s+(\d{1,2})(?::(\d\d))?\s*(am|pm)?", re.I)
_WEEKDAY = re.compile(r"\bweekday|mon(day)?[- ]?(to|through|-)?[- ]?fri(day)?\b", re.I)


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
    t = text or ""
    hits = [se for rx, se in _SOURCES if rx.search(t)]
    if not hits:
        return None
    named = [se for se in hits if re.search(rf"\b{re.escape(se[0])}\b", t, re.I)]
    return (named or hits)[0]


def cadence_of(text: str) -> dict:
    """Best-effort {'interval_seconds': n} or {'cron': '...'} for CRON/POLL, else {}."""
    m = _INTERVAL.search(text or "")
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        mult = {"second": 1, "sec": 1, "minute": 60, "min": 60, "hour": 3600, "hr": 3600,
                "day": 86400}.get(unit, 60)
        return {"interval_seconds": n * mult}
    at = _AT_TIME.search(text or "")
    if at:
        hour = int(at.group(1)) % 12
        if (at.group(3) or "").lower() == "pm":
            hour += 12
        minute = int(at.group(2) or 0)
        dow = "1-5" if _WEEKDAY.search(text or "") else "*"
        return {"cron": f"{minute} {hour} * * {dow}"}
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
