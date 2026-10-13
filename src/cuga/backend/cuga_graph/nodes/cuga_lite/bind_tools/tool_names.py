"""Provider-safe tool names for native ``bind_tools``.

OpenAI-compatible APIs reject a request when any tool name is outside
``^[A-Za-z0-9_-]{1,64}$`` (Gemini also wants a letter or ``_`` first), so one
long registry name (``<app>_<tool>``) makes every bound tool unusable. Such
tools are bound under an alias that exists only in the request: ``_safe_bind``
binds :func:`provider_safe_tools`, and ``AgentGraphAdapter.normalize_response``
maps aliases in a reply back with :func:`resolve_tool_names`, so code, approval
checks and the sandbox see real names.
"""

from __future__ import annotations

import functools
import hashlib
import io
import re
import tokenize
from typing import Any, Collection, Dict, List, Sequence, Set, Tuple

from langchain_core.tools import BaseTool
from loguru import logger

__all__ = [
    "PROVIDER_TOOL_NAME_RE",
    "provider_safe_tool_name",
    "provider_safe_tools",
    "resolve_tool_names",
]

PROVIDER_TOOL_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
_INVALID = re.compile(r"[^A-Za-z0-9_-]")
_MAX_LEN = 64
_DIGEST_LEN = 8
# A whole token shaped like an alias: up to 55 legal characters, "_", 8 hex digits.
_ALIAS = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{1,55}_[0-9a-f]{8}(?![A-Za-z0-9_-])")
# A ```python block, closed or not: the code CugaLite runs from a reply.
_PYTHON_FENCE = re.compile(r"(```python)(.*?)(```|\Z)", re.DOTALL)

# ``_safe_bind`` runs on every model call: log each (real, alias) pair once per process.
_logged_aliases: Set[Tuple[str, str]] = set()


@functools.lru_cache(maxsize=None)  # keys are tool names; resolve_tool_names maps them all per reply
def provider_safe_tool_name(name: str) -> str:
    """Return ``name`` if providers accept it, else a deterministic legal alias.

    The alias is the name with illegal characters replaced by ``_`` (and a ``_``
    in front if it would start with a digit or ``-``), cut to 55 characters, plus
    ``_`` and 8 hex digits of the SHA-1 of the original name. Truncation alone
    would merge long names that share a prefix, and the model would call the
    wrong tool.
    """
    if PROVIDER_TOOL_NAME_RE.fullmatch(name):
        return name
    raw = name.encode("utf-8", "surrogatepass")  # a lone surrogate must not raise here
    digest = hashlib.sha1(raw, usedforsecurity=False).hexdigest()[:_DIGEST_LEN]
    stem = _INVALID.sub("_", name) or "tool"
    if not (stem[0].isalpha() or stem[0] == "_"):
        stem = "_" + stem
    return f"{stem[: _MAX_LEN - 1 - _DIGEST_LEN]}_{digest}"


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
    """Replace each alias that the code in ``text`` uses as a name with the real tool name.

    ``text`` can be a model reply, code or a single tool name; ``tool_names`` are
    the real names that can run. Only Python names change — in each
    ```` ```python ```` block, or in the whole text when it has none — so strings,
    comments and attributes keep their exact values. Raises ``RuntimeError`` when
    an alias could mean more than one tool.
    """
    if not text or not _ALIAS.search(text):
        return text
    owners: Dict[str, List[str]] = {}
    for real in tool_names:
        alias = provider_safe_tool_name(real)
        if alias != real:
            owners.setdefault(alias, []).append(real)
    if not owners:  # no tool is aliased, so nothing can map back
        return text
    if text in owners:  # a single tool name, which can hold a "-" no Python name has
        return _real_name(text, owners, tool_names)
    if "```python" not in text:
        return _resolve_names(text, owners, tool_names)
    return _PYTHON_FENCE.sub(
        lambda m: m.group(1) + _resolve_names(m.group(2), owners, tool_names) + m.group(3), text
    )


def _real_name(alias: str, owners: Dict[str, List[str]], tool_names: Collection[str]) -> str:
    reals = owners[alias]
    clash = [*reals, alias] if alias in tool_names else reals
    if len(clash) > 1:
        raise RuntimeError(f"Tool name {alias!r} is ambiguous: it matches tools {sorted(clash)!r}.")
    return reals[0]


def _resolve_names(code: str, owners: Dict[str, List[str]], tool_names: Collection[str]) -> str:
    try:
        skip = (tokenize.NL, tokenize.COMMENT)
        tokens = [t for t in tokenize.generate_tokens(io.StringIO(code).readline) if t.type not in skip]
    except Exception:  # not Python, so nothing here runs (3.12+ can raise more than TokenError)
        return code
    if any(t.type == tokenize.ERRORTOKEN and t.string in ("'", '"') for t in tokens):
        return code  # an unclosed quote: 3.12+ raise above, 3.10/3.11 yield an error token
    line_starts = [0]
    for line in code.split("\n"):
        line_starts.append(line_starts[-1] + len(line) + 1)
    edits = []
    for previous, token in zip([None, *tokens], tokens):
        if token.type != tokenize.NAME or token.string not in owners or (previous and previous.string == "."):
            continue
        start = line_starts[token.start[0] - 1] + token.start[1]
        edits.append((start, start + len(token.string), _real_name(token.string, owners, tool_names)))
    for start, end, real in reversed(edits):
        code = code[:start] + real + code[end:]
    return code
