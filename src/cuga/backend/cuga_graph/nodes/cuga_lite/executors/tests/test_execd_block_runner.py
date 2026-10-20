"""The execd kernel runner keeps what the local executor keeps.

The runner is code for the kernel, so it is run here for real: the same source
the sandbox receives is exec'd with the block, and its framed outcome is parsed
and rendered the way ``ExecdExecutor`` does.
"""

from __future__ import annotations

import asyncio
import datetime as dt

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common import CodeWrapper
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.execd import block_runner

pytestmark = pytest.mark.unit


def _run_kernel(
    code: str, *, timeout: int = 5, variables: dict | None = None, extra: str = ""
) -> tuple[dict, str]:
    """Exec variables + wrapped block + runner as one cell; return (outcome, rendered text)."""
    import contextlib
    import io

    source = (
        block_runner.variables_code(variables or {})
        + extra
        + CodeWrapper.wrap_code(code)
        + block_runner.runner_code(timeout)
    )
    # Compiled as IPython compiles a cell: module level, top-level await allowed,
    # so names the cell binds are globals exactly as they are in the kernel.
    import ast as _ast
    import types

    code_obj = compile(source, "<cell>", "exec", flags=_ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    namespace: dict = {"__name__": "__main__"}
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        coro = types.FunctionType(code_obj, namespace)()
        if coro is not None:
            asyncio.run(coro)
    raw = out.getvalue()
    outcome = block_runner.parse_outcome(raw)
    assert outcome is not None, raw
    return outcome, block_runner.render(outcome, timeout=timeout)


def test_a_normal_block_returns_its_output_and_new_variables():
    outcome, text = _run_kernel("y = x + 1\nprint('y is', y)", variables={"x": 41})
    assert outcome["status"] == "ok"
    # x came in as a kernel global; only what the block itself bound comes back.
    assert outcome["locals"] == {"y": 42}
    assert text.strip() == "y is 42"


def test_a_timeout_keeps_the_variables_computed_before_it():
    outcome, text = _run_kernel(
        "import asyncio\npartial = [1, 2]\nprint('started')\nawait asyncio.sleep(10)\nnever = 1", timeout=1
    )
    assert outcome["status"] == "timeout"
    assert outcome["locals"]["partial"] == [1, 2]
    assert "never" not in outcome["locals"]
    assert text.startswith("Error during execution: Execution timed out after 1 seconds.")
    assert "kept variables from this block: partial" in text
    assert "started" in text


def test_exit_keeps_variables_and_says_the_block_ended_early():
    outcome, text = _run_kernel("found = 'nothing'\nexit('no candidate found')\nunreachable = 1")
    assert outcome["status"] == "exit"
    assert outcome["locals"] == {"found": "nothing"}
    assert "Block ended early" in text and "no candidate found" in text


def test_a_refused_tool_call_keeps_the_result_of_the_call_that_ran():
    """Step discipline: the second call is refused, the first call's result must survive."""
    stubs = (
        "class _CugaToolError(Exception):\n"
        "    def __init__(self, type_, message):\n"
        "        super().__init__(message)\n"
        "        self.type = type_\n"
        "_calls = [0]\n"
        "async def ping():\n"
        "    _calls[0] += 1\n"
        "    if _calls[0] > 1:\n"
        "        raise _CugaToolError('BlockToolCallBudgetExceeded', 'One tool call per step: refused')\n"
        "    return 'pong'\n"
    )
    outcome, text = _run_kernel("a = await ping()\nb = await ping()\nprint('never')", extra=stubs)
    assert outcome["status"] == "budget"
    assert outcome["locals"] == {"a": "pong"}
    assert text.startswith(
        "Error during execution: BlockToolCallBudgetExceeded('One tool call per step: refused')"
    )
    assert "never" not in text


def test_an_error_keeps_the_output_printed_before_it():
    outcome, text = _run_kernel("print('discovered tools')\nraise ValueError('bad input')")
    assert outcome["status"] == "error"
    assert outcome["error_type"] == "ValueError"
    assert text.startswith("Output before error:\ndiscovered tools")
    assert "ValueError('bad input')" in text


def test_variables_without_a_literal_repr_no_longer_break_the_next_block():
    class Opaque:
        def __repr__(self):
            return "<Opaque at 0x1>"  # not Python source

        def __str__(self):
            return "opaque value"

    outcome, _ = _run_kernel(
        "seen = (str(o), d)",
        variables={"o": Opaque(), "d": dt.date(2026, 1, 2)},
    )
    assert outcome["status"] == "ok"
    assert outcome["locals"]["seen"] == ["opaque value", "2026-01-02"]


def test_non_identifier_variable_names_are_skipped():
    code = block_runner.variables_code({"ok_name": 1, "not-valid": 2})
    assert "ok_name = " in code and "not-valid" not in code.split("_cuga_v = ")[0]


def test_a_missing_outcome_line_is_reported_as_none():
    assert block_runner.parse_outcome("just output\nno marker") is None


def test_exit_raises_even_where_the_kernel_shadows_it():
    """IPython's exit/quit return None in a kernel; the runner must give the block plain-Python exit()."""

    class _IPythonExit:
        def __call__(self, *a):
            return None

    shadow = "exit = quit = _IPythonExit()\n"
    import builtins

    builtins._IPythonExit = _IPythonExit
    try:
        outcome, text = _run_kernel("found = 'nothing'\nexit('stop here')\nafter = 1", extra=shadow)
    finally:
        del builtins._IPythonExit
    assert outcome["status"] == "exit"
    assert outcome["locals"] == {"found": "nothing"}
    assert "stop here" in text
