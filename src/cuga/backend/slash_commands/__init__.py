"""Slash-command parser, registry, and dispatcher for skill invocation.

A user typing ``/<name> [args]`` in the chat input is parsed here and
dispatched to a discovered skill, with the message passed through to the
planner when it is not a slash command. The same ``parse_and_dispatch``
entry point is used by both the ``POST /stream`` HTTP handler and the
Python SDK's ``invoke()`` method so HTTP and SDK paths get identical
semantics.
"""

from cuga.backend.slash_commands.dispatcher import (
    build_slash_registry,
    parse_and_dispatch,
)
from cuga.backend.slash_commands.parser import ParsedSlash, parse
from cuga.backend.slash_commands.registry import SlashRegistry
from cuga.backend.slash_commands.types import (
    CommandRef,
    DispatchContext,
    DispatchKind,
    DispatchResult,
)

__all__ = [
    "CommandRef",
    "DispatchContext",
    "DispatchKind",
    "DispatchResult",
    "ParsedSlash",
    "SlashRegistry",
    "build_slash_registry",
    "parse",
    "parse_and_dispatch",
]
