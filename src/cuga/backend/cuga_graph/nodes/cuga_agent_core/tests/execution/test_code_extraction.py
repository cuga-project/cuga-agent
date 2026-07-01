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


def test_truncates_after_first_block_referencing_probing_tool():
    text = (
        "```python\nres = await file_readfile('x')\nprint(res)\n```\n"
        "```python\nres_2 = res[0]\nprint(res_2)\n```"
    )
    code = extract_and_combine_codeblocks(text, tools_needing_probing=frozenset({"file_readfile"}))
    assert code == "res = await file_readfile('x')\nprint(res)"


def test_keeps_all_blocks_when_no_probing_tool_referenced():
    text = (
        "```python\nres = await file_readfile('x')\nprint(res)\n```\n"
        "```python\nres_2 = res[0]\nprint(res_2)\n```"
    )
    code = extract_and_combine_codeblocks(text, tools_needing_probing=frozenset({"some_other_tool"}))
    assert code == "res = await file_readfile('x')\nprint(res)\n\nres_2 = res[0]\nprint(res_2)"


def test_default_tools_needing_probing_preserves_old_combine_behavior():
    text = "```python\na = 1\n```\n```python\nb = 2\n```"
    assert extract_and_combine_codeblocks(text) == "a = 1\n\nb = 2"


def test_truncation_keeps_prefix_blocks_before_the_matching_one():
    text = (
        "```python\nx = 1\nprint(x)\n```\n"
        "```python\nres = await file_readfile('x')\nprint(res)\n```\n"
        "```python\nres_2 = res[0]\n```"
    )
    code = extract_and_combine_codeblocks(text, tools_needing_probing=frozenset({"file_readfile"}))
    assert code == "x = 1\nprint(x)\n\nres = await file_readfile('x')\nprint(res)"


def test_truncation_is_word_boundary_safe():
    """A tool name that's a prefix of a longer identifier must not false-match."""
    text = "```python\nfile_readfile_v2('x')\n```\n```python\ny = 2\n```"
    code = extract_and_combine_codeblocks(text, tools_needing_probing=frozenset({"file_readfile"}))
    assert code == "file_readfile_v2('x')\n\ny = 2"


def test_extract_code_from_model_response_threads_tools_needing_probing_through():
    content = "```python\nres = await file_readfile('x')\nprint(res)\n```\n```python\nres_2 = res[0]\n```"
    code = extract_code_from_model_response(content, None, tools_needing_probing=frozenset({"file_readfile"}))
    assert code == "res = await file_readfile('x')\nprint(res)"


# ── Probing isolation also covers the recovery paths (unclosed fence / raw) ──
def test_unclosed_fence_truncates_at_first_probing_line():
    """An unclosed fence collapses would-be-separate blocks into one blob; the
    probe must still be isolated from later dependent code (line-level cut)."""
    text = "```python\nres = await file_readfile('x')\nres_2 = res[0][0:15]\nprint(res_2)"
    code = extract_and_combine_codeblocks(text, tools_needing_probing=frozenset({"file_readfile"}))
    assert code == "res = await file_readfile('x')"


def test_raw_python_truncates_at_first_probing_line():
    text = "res = await file_readfile('x')\nres_2 = res[0]\nprint(res_2)"
    code = extract_and_combine_codeblocks(text, tools_needing_probing=frozenset({"file_readfile"}))
    assert code == "res = await file_readfile('x')"


def test_raw_python_keeps_full_multiline_probing_statement():
    """A probing call spanning multiple lines must be kept whole — cutting at
    the first matching line alone would return invalid Python (unclosed paren)
    and the probe would never execute."""
    text = 'res = await file_readfile(\n    "x"\n)\nres_2 = res[0]\nprint(res_2)'
    code = extract_and_combine_codeblocks(text, tools_needing_probing=frozenset({"file_readfile"}))
    assert code == 'res = await file_readfile(\n    "x"\n)'
    # The kept code must be valid Python (parseable), unlike a bare line cut.
    compile(code.replace("await ", ""), "<string>", "exec")


def test_unclosed_fence_keeps_full_multiline_probing_statement():
    text = '```python\nres = await file_readfile(\n    "x",\n)\nres_2 = res[0]\nprint(res_2)'
    code = extract_and_combine_codeblocks(text, tools_needing_probing=frozenset({"file_readfile"}))
    assert code == 'res = await file_readfile(\n    "x",\n)'
    compile(code.replace("await ", ""), "<string>", "exec")


def test_recovery_paths_unchanged_without_probing_tools():
    """Regression: with no probing tools the recovery paths keep the full blob."""
    unclosed = "```python\nres = await file_readfile('x')\nprint(res)"
    assert extract_and_combine_codeblocks(unclosed) == "res = await file_readfile('x')\nprint(res)"
    raw = "res = await file_readfile('x')\nprint(res)"
    assert extract_and_combine_codeblocks(raw) == "res = await file_readfile('x')\nprint(res)"


def test_recovery_path_keeps_full_blob_when_probing_tool_absent():
    text = "res = await some_other_tool('x')\nprint(res)"
    code = extract_and_combine_codeblocks(text, tools_needing_probing=frozenset({"file_readfile"}))
    assert code == "res = await some_other_tool('x')\nprint(res)"
