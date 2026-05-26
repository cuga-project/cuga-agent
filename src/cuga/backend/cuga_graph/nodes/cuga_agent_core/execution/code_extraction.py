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


def extract_and_combine_codeblocks(text: str) -> str:
    """Extract all ```python codeblocks from text and combine them."""
    code_blocks = re.findall(BACKTICK_PATTERN, text, re.DOTALL)

    if code_blocks:
        return "\n\n".join(block.strip() for block in code_blocks)

    recovered = _recover_non_closing_python_fence(text)
    if recovered:
        return recovered

    stripped_text = text.strip()

    if "print(" not in stripped_text:
        return ""

    try:
        compile(stripped_text.replace("await ", ""), "<string>", "exec")
        return stripped_text
    except SyntaxError:
        return ""


def extract_code_from_model_response(content: Optional[str], reasoning_content: Optional[str]) -> str:
    """Extract code from a model response, falling back to reasoning.

    Tries fenced/raw code in ``content`` first; only if that yields nothing
    does it look at ``reasoning_content``. Mirrors the (previously
    duplicated) logic in the Lite and Supervisor loop nodes.
    """
    code = extract_and_combine_codeblocks(content) if content else ""
    if not code and reasoning_content:
        code = extract_and_combine_codeblocks(reasoning_content)
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
