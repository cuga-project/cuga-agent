"""Shared types for the slash-command subsystem."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Callable,
    Literal,
    Optional,
)

from cuga.backend.slash_commands.parser import ParsedSlash

if TYPE_CHECKING:  # pragma: no cover
    from cuga.backend.skills.registry import SkillRegistry
    from cuga.backend.slash_commands.registry import SlashRegistry


DispatchKind = Literal["skill", "passthrough", "unknown"]


@dataclass(frozen=True)
class CommandRef:
    """Lightweight description of a slash command for the registry/listing UI."""

    name: str
    description: str
    kind: Literal["skill"]
    argument_hint: Optional[str] = None


@dataclass
class DispatchContext:
    """Runtime context passed to dispatch helpers."""

    parsed: ParsedSlash
    slash_registry: "SlashRegistry"
    skill_registry: Optional["SkillRegistry"] = None
    thread_id: Optional[str] = None
    clear_stop_event: Optional[Callable[[str], None]] = None
    extra: dict = field(default_factory=dict)


@dataclass
class DispatchResult:
    """The outcome of ``parse_and_dispatch``.

    For ``kind == "skill"``, ``planner_input`` carries the translated
    suggestion the caller feeds to the planner; ``raw_input`` keeps the
    user's original utterance for display/history.
    """

    kind: DispatchKind
    planner_input: Optional[str] = None
    resolved_name: Optional[str] = None
    raw_input: Optional[str] = None
    raw_args: Optional[str] = None
