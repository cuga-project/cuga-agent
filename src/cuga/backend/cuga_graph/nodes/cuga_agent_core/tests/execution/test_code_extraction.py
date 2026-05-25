"""Focused unit guards for the shared code-extraction utilities.

Comprehensive extract cases live in
``cuga_lite/executors/tests/test_extract_codeblocks.py`` and the
make_tool_awaitable integration cases in
``cuga_lite/executors/tests/test_sync_async_tools.py`` (both repointed to
this module). This file only adds what those suites do NOT cover:

- The unification-decision regression guard: fenced ```python blocks are
  returned even with no ``print(`` call (canonical Lite behavior — the
  user explicitly chose this over Supervisor/code_act's print() gate).
- Direct (non-integration) contract tests for ``make_tool_awaitable``:
  always a coroutine function, Pydantic results ``.model_dump()``-ed.
"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel

from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.code_extraction import (
    extract_and_combine_codeblocks,
    extract_code_from_model_response,
    make_tool_awaitable,
)


def test_model_response_prefers_content_over_reasoning() -> None:
    code = extract_code_from_model_response(
        "```python\nprint('from content')\n```",
        "```python\nprint('from reasoning')\n```",
    )
    assert code == "print('from content')"


def test_model_response_falls_back_to_reasoning_when_content_has_no_code() -> None:
    code = extract_code_from_model_response(
        "just prose, no code here",
        "```python\nprint('from reasoning')\n```",
    )
    assert code == "print('from reasoning')"


def test_model_response_empty_content_uses_reasoning() -> None:
    assert extract_code_from_model_response("", "```python\nx = compute()\n```") == "x = compute()"
    assert extract_code_from_model_response(None, "```python\nx = compute()\n```") == "x = compute()"


def test_model_response_no_code_anywhere_returns_empty() -> None:
    assert extract_code_from_model_response("prose only", None) == ""
    assert extract_code_from_model_response("prose only", "also prose") == ""


def test_fenced_block_without_print_is_still_returned() -> None:
    """Regression guard for the Lite-vs-Supervisor unification decision.

    Supervisor/code_act required ``print(`` even inside fenced blocks; the
    canonical (Lite) behavior does not. If this flips, the unification was
    silently reverted.
    """
    text = "```python\nx = compute_value()\n```"
    assert extract_and_combine_codeblocks(text) == "x = compute_value()"


def test_async_function_is_wrapped_not_returned_as_is() -> None:
    """Supervisor's old impl returned coroutine funcs unchanged; canonical
    (Lite) always wraps so Pydantic conversion applies to async tools too."""

    async def fetch(x: int) -> int:
        return x * 10

    wrapped = make_tool_awaitable(fetch)
    assert wrapped is not fetch
    assert asyncio.iscoroutinefunction(wrapped)
    assert asyncio.run(wrapped(4)) == 40


def test_sync_function_becomes_awaitable() -> None:
    def add(a: int, b: int) -> int:
        return a + b

    wrapped = make_tool_awaitable(add)
    assert asyncio.iscoroutinefunction(wrapped)
    assert asyncio.run(wrapped(2, 3)) == 5


def test_pydantic_result_is_model_dumped_for_sync_and_async() -> None:
    class Result(BaseModel):
        value: int

    def make_sync() -> BaseModel:
        return Result(value=7)

    async def make_async() -> BaseModel:
        return Result(value=9)

    assert asyncio.run(make_tool_awaitable(make_sync)()) == {"value": 7}
    assert asyncio.run(make_tool_awaitable(make_async)()) == {"value": 9}
