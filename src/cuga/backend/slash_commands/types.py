"""Shared types for the slash-command subsystem."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    List,
    Literal,
    Optional,
    Protocol,
    runtime_checkable,
)

from cuga.backend.slash_commands.parser import ParsedSlash

if TYPE_CHECKING:  # pragma: no cover
    from cuga.backend.skills.registry import SkillRegistry
    from cuga.backend.slash_commands.command_resolver import CommandSuggestion
    from cuga.backend.slash_commands.registry import SlashRegistry


DispatchKind = Literal["builtin", "skill", "passthrough", "unknown"]


@dataclass(frozen=True)
class CommandRef:
    """Lightweight description of a slash command for the registry/listing UI."""

    name: str
    description: str
    kind: Literal["builtin", "skill"]
    argument_hint: Optional[str] = None


@dataclass
class DispatchContext:
    """Runtime context passed to a built-in's ``handle`` method.

    Built-ins read the parsed input and registry, and may use the optional
    server-injected hooks. SDK callers leave the hooks as ``None``; built-ins
    that depend on a hook should fall back gracefully (e.g. ``/clear`` mints a
    new thread id either way; only the stop-event clear is server-only).
    """

    parsed: ParsedSlash
    slash_registry: "SlashRegistry"
    skill_registry: Optional["SkillRegistry"] = None
    thread_id: Optional[str] = None
    clear_stop_event: Optional[Callable[[str], None]] = None
    extra: dict = field(default_factory=dict)


@dataclass
class DispatchResult:
    """The outcome of ``parse_and_dispatch``."""

    kind: DispatchKind
    text: Optional[str] = None
    new_thread_id: Optional[str] = None
    injected_messages: List[Any] = field(default_factory=list)
    resolved_name: Optional[str] = None
    raw_input: Optional[str] = None
    raw_args: Optional[str] = None
    # Top semantic matches for an unknown command; empty otherwise.
    suggestions: List["CommandSuggestion"] = field(default_factory=list)
    allowed_tools: Optional[tuple[str, ...]] = None
    """Whitelist of tool names declared in the skill's ``allowed-tools``
    frontmatter. Only meaningful when ``kind == "skill"``; for all other kinds
    (``builtin``, ``unknown``, ``passthrough``) this stays ``None``. For skills:
    ``None`` = no restriction declared; ``()`` = key present but empty
    ("allow nothing, every call needs approval"); non-empty tuple = whitelist."""


@runtime_checkable
class BuiltinCommand(Protocol):
    """Protocol every built-in slash command must satisfy."""

    name: str
    description: str
    argument_hint: Optional[str]

    def handle(self, ctx: DispatchContext) -> Awaitable[DispatchResult]: ...
