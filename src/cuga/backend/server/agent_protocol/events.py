"""Protocol-neutral stream event type shared by A2A and ACP adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(slots=True)
class AgentStreamEvent:
    """A single event emitted by an agent runner.

    ``name``  — event category (e.g. ``"final_answer"``, ``"error"``).
    ``data``  — structured payload or plain text; ``None`` if the event
                carries no payload.
    ``final`` — ``True`` on the terminal event; the caller must not
                await further events after receiving one.
    """

    name: str
    data: Mapping[str, Any] | str | None = None
    final: bool = False
