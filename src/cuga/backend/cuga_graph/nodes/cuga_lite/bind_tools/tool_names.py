"""Provider-safe tool names for native ``bind_tools``.

OpenAI-compatible endpoints (OpenAI, Azure OpenAI, LiteLLM proxies in front of
them) require every function name to match ``^[A-Za-z0-9_-]{1,64}$`` and reject
the whole request when one name does not, so a single long registry name
(``<app>_<tool>``) makes every bound tool unusable.

Such tools are bound under a legal alias that lives only in the request:
``_safe_bind`` binds :func:`provider_safe_tools`, and the decode boundary
(``AgentGraphAdapter.normalize_response``) maps every alias in a reply back
with :func:`resolve_tool_names`, so code, the approval check and the sandbox
only see real names.
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
    """Legal names come back unchanged; anything else gets a deterministic legal alias.

    The alias is the name with illegal characters replaced by ``_``, cut to 55
    characters, then ``_`` and the first 8 hex digits of the SHA-1 of the
    *original* name, so ``a.b`` and ``a_b`` stay distinct. Plain truncation is
    not safe: long names of one family share their prefixes, and a merged name
    makes the model call the wrong tool without any error.
    """
    if PROVIDER_TOOL_NAME_RE.fullmatch(name):
        return name
    raw = name.encode("utf-8", "surrogatepass")  # a lone surrogate must not raise here
    digest = hashlib.sha1(raw, usedforsecurity=False).hexdigest()[:_DIGEST_LEN]
    stem = _INVALID.sub("_", name)[: _MAX_LEN - 1 - _DIGEST_LEN] or "tool"
    return f"{stem}_{digest}"


def provider_safe_tools(tools: Sequence[Any]) -> Sequence[Any]:
    """Return ``tools`` with every illegal tool name replaced by its alias.

    Aliased entries are copies (``model_copy``); the shared tool objects are
    never mutated, so prompts, policies and tracking keep seeing real names.
    When no name needs an alias, ``tools`` itself is returned.

    Raises ``RuntimeError`` when tools with different names would be bound
    under the same name: the model would call one believing it is the other.
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
    """Replace every alias in ``text`` with the real tool name it stands for.

    ``text`` is a model reply, code or a single tool name; ``tool_names`` are
    the real names that can run (the execution context's keys). Anything that
    is not the alias of one of them, real names included, is left as it is.

    Raises ``RuntimeError`` when an alias is ambiguous: it stands for more than
    one tool, or for one tool while being the real name of another.
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
