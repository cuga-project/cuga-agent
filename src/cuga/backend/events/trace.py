"""End-to-end tracing for the events layer (events/docs/ARCHITECTURE.md).

Every request carries a ``trace_id`` stamped on each seam log line, so grepping one id
follows a request across CUGA and AP:

    [trace=abc123] inbound     source=channel/telegram thread=... text="..."
    [trace=abc123] concierge   decision=CREATE agent=resume_judge mode=PUSH
    [trace=abc123] flow.build  shape=push+router ap_flow_id=... dry_run=false
    [trace=abc123] invoke      agent=resume_judge backend=react thread=...
    [trace=abc123] worker.done ok=true ms=1840
    [trace=abc123] deliver     via=gmail ok=true

Stdlib-only.
"""

from __future__ import annotations

import logging
import uuid

log = logging.getLogger("cuga.events")

# Canonical seam names — keep stable so log-greps and dashboards don't drift.
SEAMS = ("inbound", "concierge", "flow.build", "invoke", "worker.done", "deliver", "error")


def new_trace_id() -> str:
    """A short, unique correlation id (uuid4-derived; not time-based)."""
    return uuid.uuid4().hex[:12]


def _fmt_fields(fields: dict) -> str:
    parts = []
    for k, v in fields.items():
        s = str(v)
        if len(s) > 200:
            s = s[:200] + "…"
        if " " in s and not (s.startswith('"') or s.startswith("[")):
            s = f'"{s}"'
        parts.append(f"{k}={s}")
    return " ".join(parts)


def seam(trace_id: str, seam_name: str, level: int = logging.INFO, **fields) -> str:
    """Emit one structured seam log line and return the formatted line (handy for tests)."""
    line = f"[trace={trace_id or '-'}] {seam_name:<11} {_fmt_fields(fields)}".rstrip()
    log.log(level, line)
    return line


class Trace:
    """A bound tracer for one request — carries the id so callers don't repeat it."""

    def __init__(self, trace_id: str | None = None):
        self.id = trace_id or new_trace_id()

    def __call__(self, seam_name: str, **fields) -> str:
        return seam(self.id, seam_name, **fields)

    def error(self, seam_name: str, **fields) -> str:
        return seam(self.id, seam_name, level=logging.ERROR, **fields)
