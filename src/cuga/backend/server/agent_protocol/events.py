"""Stable protocol-neutral stream event contract for transport adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(slots=True)
class AgentStreamEvent:
    """A safe display event emitted by an agent runner.

    ``name`` identifies a neutral category. ``final_answer`` and ``error`` are
    terminal; ``input_required`` is also terminal for the current turn while
    preserving the context for a later HITL approval response. Other names are
    non-terminal progress. ``data`` contains only adapter-safe display text and
    structured metadata, never transport-specific wire models. Once ``final``
    is true, consumers must stop awaiting events from that turn.
    """

    name: str
    data: Mapping[str, Any] | str | None = None
    final: bool = False
