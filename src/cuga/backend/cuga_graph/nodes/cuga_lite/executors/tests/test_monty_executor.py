"""
Tests for the Pydantic Monty executor (``advanced_features.monty_sandbox``).

The executor runs model-written code in a Monty worker process instead of the
CUGA process. These tests cover the CUGA side: unwrapping ``CodeWrapper``
output, passing tools and earlier variables in, handing new variables back,
keeping error types the caller relies on, and routing through ``CodeExecutor``.
They also check that the sandbox blocks environment, file and process access.
"""

from unittest.mock import Mock

import pytest

pytest.importorskip("pydantic_monty")

from cuga.backend.cuga_graph.nodes.cuga_lite.executors import CodeExecutor  # noqa: E402
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common.code_wrapper import CodeWrapper  # noqa: E402
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.local.local_executor import LocalExecutor  # noqa: E402
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.monty import MontyExecutor  # noqa: E402
from cuga.backend.cuga_graph.nodes.cuga_lite.executors.monty.monty_executor import (  # noqa: E402
    assigned_names,
    capture_suffix,
    is_plain,
    split_context,
    unwrap_code,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import BlockToolCallBudgetExceeded  # noqa: E402
from cuga.backend.cuga_graph.state.agent_state import AgentState, VariablesManager  # noqa: E402
from cuga.config import settings  # noqa: E402

pytestmark = pytest.mark.unit


@pytest.fixture
async def executor():
    ex = MontyExecutor(max_processes=1)
    yield ex
    await ex.close()


def _wrap(body: str) -> str:
    return CodeWrapper.wrap_code(body)


def test_unwrap_keeps_wrapper_imports_and_drops_return_locals():
    code = unwrap_code(CodeWrapper.wrap_code_for_code_agent('x = 1\nprint(json.dumps({"x": x}))'))
    assert code.splitlines()[:2] == ["import asyncio", "import json"]
    assert "_async_main" not in code and "return locals()" not in code


def test_assigned_names_skips_functions_comprehensions_and_private_names():
    code = (
        "a = 1\nfor i in range(2):\n    b = i\nc = [j for j in range(3)]\ndef f():\n    hidden = 1\n_x = 2\n"
    )
    assert assigned_names(code) == ["a", "i", "b", "c"]
    assert capture_suffix(["a"]).endswith("_cuga_vars")


def test_only_plain_values_are_passed_in():
    async def tool():
        return 1

    tools, inputs, skipped = split_context({"tool": tool, "items": [{"a": 1}], "obj": object(), "_hidden": 1})
    assert list(tools) == ["tool"] and inputs == {"items": [{"a": 1}]} and skipped == ["obj"]
    assert is_plain({"k": (1, 2.0, None)}) and not is_plain([object()])


@pytest.mark.asyncio
async def test_tools_are_host_functions_and_variables_carry_between_blocks(executor):
    async def get_items(memberId: str = ""):
        return {"items": [{"amount": "10.00"}, {"amount": "80.00"}]}

    def double(x):  # a plain function works too
        return x * 2

    ctx = {"get_items": get_items, "double": double}
    out = await executor.execute(_wrap('r = await get_items(memberId="1")\nitems = r["items"]'), ctx)
    assert out == "<code ran, no output printed to stdout>"
    assert ctx["items"] == [{"amount": "10.00"}, {"amount": "80.00"}]

    out = await executor.execute(
        _wrap('total = sum(float(i["amount"]) for i in items)\nprint(f"{double(total):.2f}")'), ctx
    )
    assert out.strip() == "180.00" and ctx["total"] == 90.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code",
    [
        "import os\nprint(os.environ)",
        "print(open('/etc/hosts').read())",
        "import asyncio\np = await asyncio.create_subprocess_exec('echo', 'x')",
        "import pandas",
    ],
)
async def test_environment_files_processes_and_packages_are_out_of_reach(executor, code):
    with pytest.raises(Exception):
        await executor.execute(_wrap(code), {})


@pytest.mark.asyncio
async def test_name_error_keeps_its_type_for_format_error(executor):
    with pytest.raises(NameError) as info:
        await executor.execute(_wrap('print("before")\nmissing_tool()'), {})
    assert info.value.captured_stdout == "before\n"
    message = executor.format_error(info.value, available_tools=["get_items"], code="missing_tool()")
    assert message == LocalExecutor().format_error(
        info.value, available_tools=["get_items"], code="missing_tool()"
    )


@pytest.mark.asyncio
async def test_tool_budget_error_keeps_its_type(executor):
    budget = BlockToolCallBudgetExceeded("tool budget exceeded")

    async def capped():
        raise budget

    with pytest.raises(BlockToolCallBudgetExceeded) as info:
        await executor.execute(_wrap("await capped()"), {"capped": capped})
    assert info.value is budget


@pytest.mark.asyncio
async def test_timeout_returns_guidance_and_partial_output(executor):
    out = await executor.execute(_wrap('print("started")\nwhile True:\n    pass'), {}, timeout=1)
    assert "timed out after 1 seconds" in out and "started" in out


@pytest.mark.asyncio
async def test_frozen_benchmark_clock_is_refused(executor):
    wrapped = CodeWrapper.wrap_code("print(1)", fake_datetime="2025-01-01T00:00:00")
    with pytest.raises(RuntimeError, match="benchmark"):
        await executor.execute(wrapped, {})


@pytest.mark.asyncio
async def test_code_executor_uses_monty_only_when_enabled(monkeypatch):
    state = Mock(spec=AgentState)
    state.variables_manager = VariablesManager()
    state.reflection_skills_enabled = False
    monty = MontyExecutor(max_processes=1)
    monkeypatch.setattr(CodeExecutor, "_monty_executor", monty)
    try:
        monkeypatch.setattr(settings.advanced_features, "monty_sandbox", True, raising=False)
        assert CodeExecutor._get_code_executor() is monty
        assert isinstance(CodeExecutor._get_code_executor(skills_on=True), LocalExecutor)

        result, new_vars = await CodeExecutor.eval_with_tools_async(
            code="x = 5 + 3\nprint(x)", _locals={}, state=state, mode="local"
        )
        assert "8" in result and new_vars.get("x") == 8

        monkeypatch.setattr(settings.advanced_features, "monty_sandbox", False, raising=False)
        assert isinstance(CodeExecutor._get_code_executor(), LocalExecutor)
    finally:
        await monty.close()
