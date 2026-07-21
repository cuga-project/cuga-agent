"""Slash-command parser.

Recognizes ``/<name>`` and ``/<name> <args>`` when the slash is the very first
character of the input. Empty ``/``, leading whitespace before ``/``, or a slash
that does not appear at position zero all pass through to the planner as plain
text — slash is reserved for explicit user-side dispatch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


_SLASH_RE = re.compile(r"^/([a-zA-Z0-9_][a-zA-Z0-9_:-]*)(?:\s+(.*))?$", re.DOTALL)


@dataclass(frozen=True)
class ParsedSlash:
    """A successfully parsed slash invocation."""

    name: str
    raw_args: str
    raw_input: str


def parse(raw: str | None) -> ParsedSlash | None:
    """Return a ParsedSlash when ``raw`` is a slash invocation, otherwise ``None``.

    ``None`` means "pass through to the planner as plain text". The caller is
    responsible for that fallback; this function only recognizes the slash form.
    """
    if not raw:
        return None
    m = _SLASH_RE.match(raw)
    if not m:
        return None
    name = m.group(1)
    raw_args = (m.group(2) or "").strip()
    return ParsedSlash(name=name, raw_args=raw_args, raw_input=raw)
