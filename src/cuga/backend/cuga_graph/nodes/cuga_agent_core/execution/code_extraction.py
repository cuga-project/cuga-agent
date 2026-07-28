"""Shared fenced-code extraction and tool awaitable-wrapping utilities.

Previously duplicated (and drifted) across ``cuga_lite_graph`` and
``cuga_supervisor_graph``. The canonical behavior here is Cuga Lite's:
fenced ```python blocks are returned even without a
``print(`` call (the ``print(`` gate applies only to the no-fence raw-text
fallback), and ``make_tool_awaitable`` always normalizes Pydantic results
and always returns a coroutine function.
"""

from __future__ import annotations

import asyncio
import inspect
import re
from typing import Any, Callable, Optional

from pydantic import BaseModel

BACKTICK_PATTERN = r"```python(.*?)```"


def extract_and_combine_codeblocks(text: str, tools_needing_probing: frozenset[str] = frozenset()) -> str:
    """Extract all ```python codeblocks from text and combine them.

    When ``tools_needing_probing`` is non-empty, the code is isolated at the
    first probing-tool call so a probe can never run in the same turn as later
    dependent code: well-formed fenced blocks are kept up to and including the
    first block that calls one of those tools, and the recovery paths (unclosed
    fence, raw ``print(...)``) — which collapse what should be separate blocks
    into one blob — are cut at the line of the first probing call. Either way a
    fresh model turn (with the real tool result visible) runs before anything
    that depends on the probe.
    """
    code_blocks = re.findall(BACKTICK_PATTERN, text, re.DOTALL)

    if code_blocks:
        blocks = [block.strip() for block in code_blocks]
        if tools_needing_probing:
            blocks = _truncate_after_first_probing_block(blocks, tools_needing_probing)
        return "\n\n".join(blocks)

    recovered = _recover_non_closing_python_fence(text)
    if recovered:
        return _truncate_after_first_probing_line(recovered, tools_needing_probing)

    stripped_text = text.strip()

    if "print(" not in stripped_text:
        return ""

    try:
        compile(stripped_text.replace("await ", ""), "<string>", "exec")
        return _truncate_after_first_probing_line(stripped_text, tools_needing_probing)
    except SyntaxError:
        return ""


def _probing_call_pattern(tools_needing_probing: frozenset[str]) -> "re.Pattern[str]":
    return re.compile(r"\b(" + "|".join(re.escape(name) for name in tools_needing_probing) + r")\s*\(")


def _truncate_after_first_probing_block(
    blocks: list[str], tools_needing_probing: frozenset[str]
) -> list[str]:
    pattern = _probing_call_pattern(tools_needing_probing)
    for i, block in enumerate(blocks):
        if pattern.search(block):
            return blocks[: i + 1]
    return blocks


def _truncate_after_first_probing_line(code: str, tools_needing_probing: frozenset[str]) -> str:
    """Cut a single recovered code blob after the first probing *statement*.
    Recovery paths produce one contiguous block (no fence boundaries to split
    on), so block-level truncation is a no-op here — fall back to statement
    granularity to keep the probe from running with later dependent code.

    The cut extends past the matching line to the end of the statement it
    belongs to: a multiline call such as ``res = await file_readfile(\\n "x"\\n)``
    must be kept whole, otherwise truncating at the first line alone yields
    invalid Python (an unclosed paren) and the probe never runs."""
    if not tools_needing_probing:
        return code
    pattern = _probing_call_pattern(tools_needing_probing)
    lines = code.split("\n")
    for i, line in enumerate(lines):
        if pattern.search(line):
            # Grow the cut until everything up to `end` parses, so a multiline
            # probing statement is kept intact. `code` compiled as a whole
            # upstream, so a complete boundary is guaranteed to exist.
            for end in range(i + 1, len(lines) + 1):
                candidate = "\n".join(lines[:end])
                try:
                    compile(candidate.replace("await ", ""), "<string>", "exec")
                except SyntaxError:
                    continue
                return candidate
            return code
    return code


def extract_code_from_model_response(
    content: Optional[str],
    reasoning_content: Optional[str],
    tools_needing_probing: frozenset[str] = frozenset(),
) -> str:
    """Extract code from a model response, falling back to reasoning.

    Tries fenced/raw code in ``content`` first; only if that yields nothing
    does it look at ``reasoning_content``. Mirrors the (previously
    duplicated) logic in the Lite and Supervisor loop nodes.
    """
    code = extract_and_combine_codeblocks(content, tools_needing_probing) if content else ""
    if not code and reasoning_content:
        code = extract_and_combine_codeblocks(reasoning_content, tools_needing_probing)
    return code


def make_tool_awaitable(func: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a tool function so it is always awaitable.

    Sync functions are run in the default executor; async functions are
    wrapped (never returned as-is). In both cases a Pydantic ``BaseModel``
    return value is converted to a dict via ``.model_dump()``.
    """

    async def wrapper_with_pydantic(*args: Any, **kwargs: Any) -> Any:
        result = await func(*args, **kwargs) if inspect.iscoroutinefunction(func) else func(*args, **kwargs)

        if isinstance(result, BaseModel):
            return result.model_dump()

        return result

    if inspect.iscoroutinefunction(func):
        return wrapper_with_pydantic

    async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: func(*args, **kwargs))

        if isinstance(result, BaseModel):
            return result.model_dump()

        return result

    return async_wrapper


def _recover_non_closing_python_fence(text: str) -> str:
    """Recover code from an unclosed ```python fence (#204); compile-guarded."""
    # ``\s*`` (not ``\s*\n``) tolerates same-line fences like ```python print("x").
    unclosed = re.search(r"```python\s*(.*)", text, re.DOTALL)
    if not unclosed:
        return ""
    # Strip a trailing full OR partial markdown fence (1–3 backticks).
    candidate = re.sub(r"\n?`{1,3}\s*$", "", unclosed.group(1)).strip()
    if not candidate:
        return ""
    # Walk back line-by-line so trailing prose after otherwise-valid code
    # is salvageable: ``print("x")\nhope this helps`` returns ``print("x")``.
    lines = candidate.split("\n")
    for end in range(len(lines), 0, -1):
        attempt = "\n".join(lines[:end]).rstrip()
        if not attempt:
            continue
        try:
            compile(attempt.replace("await ", ""), "<string>", "exec")
            return attempt
        except SyntaxError:
            continue
    return ""
