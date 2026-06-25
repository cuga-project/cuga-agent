"""Slash-command parser, registry, dispatcher, and built-in command framework.

A user typing ``/<name> [args]`` in the chat input is parsed here, dispatched to
either a built-in handler (e.g. ``/help``, ``/clear``, ``/skills``) or a
discovered skill, or passed through to the planner when the message is not a
slash command. The same ``parse_and_dispatch`` entry point is used by both the
``POST /stream`` HTTP handler and the Python SDK's ``invoke()`` method so HTTP
and SDK paths get identical semantics.
"""

from cuga.backend.slash_commands.builtins import discover_builtins
from cuga.backend.slash_commands.command_resolver import CommandResolver, CommandSuggestion
from cuga.backend.slash_commands.dispatcher import (
    build_command_resolver,
    build_slash_registry,
    parse_and_dispatch,
)
from cuga.backend.slash_commands.parser import ParsedSlash, parse
from cuga.backend.slash_commands.registry import SlashRegistry
from cuga.backend.slash_commands.types import (
    BuiltinCommand,
    CommandRef,
    DispatchContext,
    DispatchKind,
    DispatchResult,
)

__all__ = [
    "BuiltinCommand",
    "CommandRef",
    "CommandResolver",
    "CommandSuggestion",
    "DispatchContext",
    "DispatchKind",
    "DispatchResult",
    "ParsedSlash",
    "SlashRegistry",
    "build_command_resolver",
    "build_slash_registry",
    "discover_builtins",
    "parse",
    "parse_and_dispatch",
]
