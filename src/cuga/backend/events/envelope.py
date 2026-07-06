"""The normalized ``/invoke`` envelope — the one shape every trigger arrives with.

    { "source": {"type": "channel|integration|time", "name": "...", "thread_id": "..."},
      "event":  {"kind": "message|new_email|new_pr|tick|runonce", "payload": {...}},
      "text":   "<utterance if a channel>",
      "agent":  "<target agent_id, when the subscription names one>",
      "deliver": true }

Stdlib-only and self-contained so it's trivially testable. See events_docs/DESIGN.md §5.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict

SOURCE_TYPES = ("channel", "integration", "time")
EVENT_KINDS = ("message", "new_email", "new_pr", "new_issue", "new_file", "tick", "runonce")


@dataclass
class Source:
    type: str = "channel"          # channel | integration | time
    name: str = "web"              # telegram | gmail | box | cron | ...
    thread_id: str = "web:local"   # chat id, or a stable per-subscription id, or a correlation id


@dataclass
class Event:
    kind: str = "message"          # see EVENT_KINDS
    payload: dict = field(default_factory=dict)


@dataclass
class Envelope:
    source: Source = field(default_factory=Source)
    event: Event = field(default_factory=Event)
    text: str = ""                 # the user utterance when source.type == "channel"
    agent: str | None = None       # target worker agent_id, if the subscription names one
    deliver: bool = False          # AP asks the seam to deliver the answer
    scope: str = ""                # Principal.scope; "" = unset → /invoke resolves from headers
    trace_id: str = ""             # end-to-end correlation id (see trace.py)

    # ---- (de)serialization ----------------------------------------------
    @classmethod
    def from_dict(cls, d: dict) -> "Envelope":
        d = dict(d or {})
        src = d.get("source") or {}
        ev = d.get("event") or {}
        return cls(
            source=Source(
                type=src.get("type", "channel"),
                name=src.get("name", "web"),
                thread_id=src.get("thread_id", "web:local"),
            ),
            event=Event(kind=ev.get("kind", "message"), payload=dict(ev.get("payload") or {})),
            text=d.get("text", "") or "",
            agent=d.get("agent"),
            deliver=bool(d.get("deliver", False)),
            scope=d.get("scope", "") or "",
            trace_id=d.get("trace_id", "") or "",
        )

    def to_dict(self) -> dict:
        return asdict(self)

    # ---- convenience -----------------------------------------------------
    @property
    def thread_id(self) -> str:
        return self.source.thread_id

    def worker_input(self) -> str:
        """What the worker actually runs on: the utterance for a channel message, else a
        rendered line from the event payload (so a Box new_file / GitHub new_pr becomes text)."""
        if self.source.type == "channel" and self.text:
            return self.text
        if self.event.payload:
            ctx = ", ".join(f"{k}={v}" for k, v in self.event.payload.items()
                            if not str(k).startswith("_"))
            return (self.text + ("\n\n[event] " + ctx if ctx else "")).strip() or ctx
        return self.text or "(no input)"


def validate(d: dict) -> list[str]:
    """Return a list of human-readable problems (empty = valid). Cheap, dependency-free."""
    problems: list[str] = []
    src = (d or {}).get("source") or {}
    if src.get("type") and src["type"] not in SOURCE_TYPES:
        problems.append(f"source.type '{src['type']}' not in {SOURCE_TYPES}")
    ev = (d or {}).get("event") or {}
    if ev.get("kind") and ev["kind"] not in EVENT_KINDS:
        problems.append(f"event.kind '{ev['kind']}' not in {EVENT_KINDS}")
    if src.get("type") == "channel" and not (d.get("text") or "").strip() \
            and ev.get("kind", "message") == "message":
        problems.append("channel message envelope has empty text")
    return problems
