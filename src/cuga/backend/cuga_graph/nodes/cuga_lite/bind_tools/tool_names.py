"""Provider-safe tool names for native ``bind_tools``.

OpenAI-compatible APIs reject a request when any tool name is outside
``^[A-Za-z0-9_-]{1,64}$``, so one long registry name (``<app>_<tool>``) makes
every bound tool unusable. Such tools are bound under an alias that exists only
in the request: ``_safe_bind`` binds :func:`provider_safe_tools`, and
``AgentGraphAdapter.normalize_response`` maps aliases in a reply back with
:func:`resolve_tool_names`, so code, approval checks and the sandbox see real names.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Collection, Dict, List, Sequence, Set, Tuple

from langchain_core.tools import BaseTool
from loguru import logger

__all__ = [
    "PROVIDER_TOOL_NAME_RE",
    "provider_safe_tool_name",
    "provider_safe_tools",
    "resolve_tool_names",
]

PROVIDER_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_INVALID = re.compile(r"[^A-Za-z0-9_-]")
_MAX_LEN = 64
_DIGEST_LEN = 8
# A whole token shaped like an alias: up to 55 legal characters, "_", 8 hex digits.
_ALIAS = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{1,55}_[0-9a-f]{8}(?![A-Za-z0-9_-])")

# ``_safe_bind`` runs on every model call: log each (real, alias) pair once per process.
_logged_aliases: Set[Tuple[str, str]] = set()


def provider_safe_tool_name(name: str) -> str:
    """Return ``name`` if providers accept it, else a deterministic legal alias.

    The alias is the name with illegal characters replaced by ``_``, cut to 55
    characters, plus ``_`` and 8 hex digits of the SHA-1 of the original name.
    Truncation alone would merge long names that share a prefix, and the model
    would call the wrong tool.
    """
    if PROVIDER_TOOL_NAME_RE.fullmatch(name):
        return name
    raw = name.encode("utf-8", "surrogatepass")  # a lone surrogate must not raise here
    digest = hashlib.sha1(raw, usedforsecurity=False).hexdigest()[:_DIGEST_LEN]
    stem = _INVALID.sub("_", name)[: _MAX_LEN - 1 - _DIGEST_LEN] or "tool"
    return f"{stem}_{digest}"


def provider_safe_tools(tools: Sequence[Any]) -> Sequence[Any]:
    """Return ``tools`` with illegal names replaced by aliases, or ``tools`` itself if none are.

    Aliased entries are copies; the shared tool objects are never mutated.
    Raises ``RuntimeError`` when two tools would be bound under the same name.
    """
    aliases: Dict[int, str] = {}
    for i, tool in enumerate(tools):
        if isinstance(tool, BaseTool):
            alias = provider_safe_tool_name(tool.name)
            if alias != tool.name:
                aliases[i] = alias
    if not aliases:
        return tools

    bound_as: Dict[str, str] = {}
    safe_tools = []
    for i, tool in enumerate(tools):
        name = getattr(tool, "name", None)
        final = aliases.get(i, name)
        if isinstance(final, str):
            other = bound_as.setdefault(final, name)
            if other != name:
                raise RuntimeError(
                    f"bind_tools: tools {other!r} and {name!r} would both be bound as {final!r}. "
                    "Rename one of them."
                )
        if i in aliases:
            tool = tool.model_copy(update={"name": final})
            if (name, final) not in _logged_aliases:
                _logged_aliases.add((name, final))
                logger.info("bind_tools: binding tool {!r} as {!r} (provider-safe name)", name, final)
        safe_tools.append(tool)
    return safe_tools


def resolve_tool_names(text: str, tool_names: Collection[str]) -> str:
    """Replace each alias in ``text`` with the real name in ``tool_names`` it stands for.

    ``text`` can be a reply, code or a single tool name; everything else in it,
    real names included, is left as it is. Raises ``RuntimeError`` when an alias
    could mean more than one tool.
    """
    if not text or not _ALIAS.search(text):
        return text
    owners: Dict[str, List[str]] = {}
    for real in tool_names:
        alias = provider_safe_tool_name(real)
        if alias != real:
            owners.setdefault(alias, []).append(real)

    def _real(match: re.Match) -> str:
        alias = match.group(0)
        reals = owners.get(alias, [])
        clash = [*reals, alias] if reals and alias in tool_names else reals
        if len(clash) > 1:
            raise RuntimeError(f"Tool name {alias!r} is ambiguous: it matches tools {sorted(clash)!r}.")
        return reals[0] if reals else alias

    return _ALIAS.sub(_real, text)
