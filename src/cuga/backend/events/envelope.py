"""The normalized ``/invoke`` envelope — the one shape every trigger arrives with.

    { "source": {"type": "channel|integration|time", "name": "...", "thread_id": "..."},
      "event":  {"kind": "message|new_email|new_pr|tick|runonce", "payload": {...}},
      "text":   "<utterance if a channel>",
      "agent":  "<target agent_id, when the subscription names one>",
      "deliver": true }

Stdlib-only and self-contained so it's trivially testable. See events/docs/ARCHITECTURE.md.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict

SOURCE_TYPES = ("channel", "integration", "time")
# Base kinds + every canonical kind in the trigger registry (triggers.py) — one source of truth.
# The registry is stdlib-only, so this import keeps envelope dependency-free. Dual-mode import:
# tests put the events dir itself on sys.path and import `envelope` bare.
try:
    from .triggers import event_kinds as _registry_kinds
except ImportError:  # bare import (tests / scripts with the events dir on sys.path)
    from triggers import event_kinds as _registry_kinds  # type: ignore

EVENT_KINDS = tuple(
    dict.fromkeys(
        ("message", "new_email", "new_pr", "new_issue", "new_file", "tick", "runonce") + _registry_kinds()
    )
)


def normalize_kind(kind: str | None) -> str:
    """Canonicalize an event kind to the EVENT_KINDS form: hyphens→underscores, lowercased — so an
    LLM-/AP-produced ``new-email`` / ``new-PR`` becomes ``new_email`` / ``new_pr``. Unknown kinds pass
    through (still non-canonical) so ``validate`` can flag genuinely-bad ones. This is the seam that
    keeps a flow built with a hyphenated kind from being 400'd at ``/invoke``."""
    if not kind:
        return "message"
    return str(kind).replace("-", "_").lower()


@dataclass
class Source:
    type: str = "channel"  # channel | integration | time
    name: str = "web"  # telegram | gmail | box | cron | ...
    thread_id: str = "web:local"  # chat id, or a stable per-subscription id, or a correlation id
    user: str = ""  # channel-native AUTHOR id (Slack ev.user / Discord author) →
    # per-user identity (whose creds/perms). Empty → fall back to
    # the thread-native id (Telegram chat.id is already the user).


@dataclass
class Event:
    kind: str = "message"  # see EVENT_KINDS
    payload: dict = field(default_factory=dict)


@dataclass
class Envelope:
    source: Source = field(default_factory=Source)
    event: Event = field(default_factory=Event)
    text: str = ""  # the user utterance when source.type == "channel"
    agent: str | None = None  # target worker agent_id, if the subscription names one
    deliver: bool = False  # AP asks the seam to deliver the answer
    scope: str = ""  # Principal.scope; "" = unset → /invoke resolves from headers
    trace_id: str = ""  # end-to-end correlation id (see trace.py)

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
                user=src.get("user", "") or "",
            ),
            event=Event(
                kind=normalize_kind(ev.get("kind", "message")), payload=dict(ev.get("payload") or {})
            ),
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
        """What the worker actually runs on: the utterance for a channel message, else a rendered
        line from the event payload (so a Box new_file / GitHub new_pr becomes text).

        ROBUSTNESS (Tier 0): flows forward the full raw trigger output as ``_raw``. We prefer the
        curated, non-empty fields (clean context); but if the curated map produced nothing — a new/
        unmapped piece, or a wrong hand-written path — we fall back to ``_raw`` so the worker is
        NEVER handed a blank payload. This retires the whole class of "flow ran green but the agent
        got nothing" bugs regardless of whether the per-piece field map is right.

        PUSH FRAMING: an integration fire prepends one-shot framing. Without it, live probes showed
        two failure modes — the agent re-fetching (or confabulating) the event instead of trusting
        the [event] payload (wrong PR summarized), and meta-answers ("I've sent you the message")
        that would be DELIVERED verbatim to the sink instead of the actual content."""
        if self.source.type == "channel" and self.text:
            return self.text
        pl = self.event.payload or {}
        if pl:
            # curated fields (non-_) that actually resolved to a value. Each field is CAPPED:
            # a full forwarded email body (tens of KB) blew up the supervisor→delegate exchange —
            # the delegate lost the variable, floundered ~7 min, and its raw deliberation was
            # delivered as the answer (Slack, 2026-07-16). 1500 chars keeps the substance.
            parts = [
                f"{k}={str(v)[:1500]}" for k, v in pl.items() if not str(k).startswith("_") and str(v).strip()
            ]
            ctx = ", ".join(parts)
            if not ctx and pl.get("_raw"):
                # curated map came back empty → surface the FULL raw trigger output instead
                try:
                    raw = json.dumps(pl["_raw"], default=str)[:4000]
                except Exception:  # noqa: BLE001
                    raw = str(pl["_raw"])[:4000]
                ctx = f"raw={raw}"
            base = (
                (self.text + ("\n\n[event] " + ctx if ctx else "")).strip()
                or ctx
                or (self.text or "(no input)")
            )
            # (webhook excluded: the hook endpoint composes its OWN one-shot instruction text —
            # stacking this generic frame on top gave the agent two competing frames)
            if self.source.type == "integration" and self.source.name != "webhook" and ctx:
                base = (
                    "A watched event just occurred — the [event] line below is its "
                    "authoritative data; work from it (fetching MORE detail on it is fine, "
                    "second-guessing it is not). Handle THIS event now, once: the subscription "
                    "keeps watching, so do not wait for or ask about future events. Your reply "
                    "is delivered to the user as the result — reply with the content itself, "
                    "not a description of what you did.\n" + base
                )
            return base
        return self.text or "(no input)"


def validate(d: dict) -> list[str]:
    """Return a list of human-readable problems (empty = valid). Cheap, dependency-free."""
    problems: list[str] = []
    src = (d or {}).get("source") or {}
    if src.get("type") and src["type"] not in SOURCE_TYPES:
        problems.append(f"source.type '{src['type']}' not in {SOURCE_TYPES}")
    ev = (d or {}).get("event") or {}
    if ev.get("kind") and normalize_kind(ev["kind"]) not in EVENT_KINDS:
        problems.append(f"event.kind '{ev['kind']}' not in {EVENT_KINDS}")
    if (
        src.get("type") == "channel"
        and not (d.get("text") or "").strip()
        and ev.get("kind", "message") == "message"
    ):
        problems.append("channel message envelope has empty text")
    return problems
