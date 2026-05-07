"""Parse user-friendly cadence strings to APScheduler triggers.

Accepts:
  - Interval shorthand: "30s", "5m", "2h", "1d"
  - Raw cron (5 fields): "0 9 * * *"
  - A few natural phrases: "daily 9am", "every weekday", "every morning"

Returns a tuple `(trigger_kind, normalized_spec, cron_or_none, interval_seconds_or_none)`.
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

from cuga.backend.loops.models import TriggerKind

_INTERVAL_RE = re.compile(r"^\s*(\d+)\s*([smhd])\s*$", re.IGNORECASE)
_CRON_RE = re.compile(r"^\s*(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s*$")
_NATURAL = {
    "daily": "0 9 * * *",
    "every morning": "0 9 * * *",
    "every weekday": "0 9 * * 1-5",
    "every night": "0 21 * * *",
    "weekly": "0 9 * * 1",
    "every monday": "0 9 * * 1",
    "hourly": "0 * * * *",
}

_UNIT_S = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_cadence(spec: str) -> Tuple[TriggerKind, str, Optional[str], Optional[int]]:
    """Return (kind, normalized_spec, cron, interval_seconds)."""
    if spec is None:
        raise ValueError("spec must be a string")
    s = spec.strip().lower()
    if not s:
        raise ValueError("empty cadence spec")

    # Natural language shortcuts
    if s in _NATURAL:
        cron = _NATURAL[s]
        return TriggerKind.CRON, s, cron, None

    # Interval shorthand
    m = _INTERVAL_RE.match(s)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        seconds = n * _UNIT_S[unit]
        if seconds < 5:
            raise ValueError("interval must be at least 5 seconds")
        return TriggerKind.INTERVAL, f"{n}{unit}", None, seconds

    # Raw cron
    if _CRON_RE.match(s):
        return TriggerKind.CRON, s, s, None

    # "daily 9am" / "daily 14:30" — pull the time
    if s.startswith("daily "):
        rest = s[6:].strip()
        h, mn = _parse_time(rest)
        cron = f"{mn} {h} * * *"
        return TriggerKind.CRON, s, cron, None

    raise ValueError(
        f"unrecognized cadence '{spec}' — try '5m', '0 9 * * *', 'daily 9am', or 'every weekday'"
    )


def _parse_time(s: str) -> Tuple[int, int]:
    """Parse '9am', '9:30am', '14:30', '14' into (hour, minute) in 24h."""
    s = s.strip().lower().replace(" ", "")
    am = pm = False
    if s.endswith("am"):
        am = True
        s = s[:-2]
    elif s.endswith("pm"):
        pm = True
        s = s[:-2]
    if ":" in s:
        h_str, m_str = s.split(":", 1)
        h = int(h_str)
        mn = int(m_str)
    else:
        h = int(s)
        mn = 0
    if pm and h < 12:
        h += 12
    if am and h == 12:
        h = 0
    if not (0 <= h <= 23 and 0 <= mn <= 59):
        raise ValueError(f"invalid time '{s}'")
    return h, mn


def parse_delay_seconds(spec) -> int:
    """Parse a one-shot delay: int seconds, or '30s'/'5m'/'2h'/'1d'."""
    if isinstance(spec, (int, float)):
        n = int(spec)
        if n < 5:
            raise ValueError("delay must be at least 5 seconds")
        return n
    if not isinstance(spec, str):
        raise ValueError(f"delay must be int seconds or interval string, got {type(spec).__name__}")
    m = _INTERVAL_RE.match(spec.strip().lower())
    if m:
        seconds = int(m.group(1)) * _UNIT_S[m.group(2).lower()]
        if seconds < 5:
            raise ValueError("delay must be at least 5 seconds")
        return seconds
    # Plain integer string
    try:
        n = int(spec)
        if n < 5:
            raise ValueError("delay must be at least 5 seconds")
        return n
    except ValueError as e:
        raise ValueError(
            f"unrecognized delay '{spec}' — use seconds (e.g. 60) or shorthand (e.g. '5m')"
        ) from e
