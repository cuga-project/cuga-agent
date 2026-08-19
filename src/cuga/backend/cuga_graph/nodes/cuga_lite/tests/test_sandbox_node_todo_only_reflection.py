"""The sandbox node must not run reflection after a todo-only block (issue #676).

Measured on two AppWorld bundles: 43/193 and 51/313 executor turns were todo-only,
and every one of them triggered a reflection generation that could only report that
nothing had happened — 17.6% and 13.2% of all LLM calls in those runs.

These tests pin both directions (skip on todo-only, still reflect on real work) and,
critically, that the guard fires at all: it depends on ``make_tool_awaitable``
converting the tool's ``TodosOutput`` to a dict, so a change there would silently
turn the optimization into dead code.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

TODO_SCRIPT = 'todos = await create_update_todos([{"text": "Fetch cart", "status": "pending"}])\nprint(todos)'
MIXED_SCRIPT = (
    'await create_update_todos([{"text": "Fetch cart", "status": "completed"}])\n'
    "cart = await amazon_show_cart_cart_get()\n"
    "print(cart)"
)
# Shape produced by make_tool_awaitable: TodosOutput -> .model_dump()
TODOS_VAR = {"todos": [{"text": "Fetch cart", "status": "pending"}]}


def _build_adapter() -> MagicMock:
    adapter = MagicMock()
    adapter._tools_context = {}
    adapter._weak_schema_tool_names = frozenset()
    adapter._observed_tool_shapes = {}
    adapter._task_todos_call_count = 0
    adapter._tracker = MagicMock()
    adapter._tracker.collect_step = MagicMock()
    adapter.messages_key = "chat_messages"
    adapter.get_messages = MagicMock(return_value=[])
    adapter.resolve_max_steps = MagicMock(return_value=1000)
    return adapter


def _build_state(script: str) -> SimpleNamespace:
    variables_manager = MagicMock()
    variables_manager.get_variable_names = MagicMock(return_value=[])
    variables_manager.get_variable = MagicMock(return_value=None)
    variables_manager.remove_variable = MagicMock()
    variables_manager.add_variable = MagicMock()

    return SimpleNamespace(
        variables_manager=variables_manager,
        chat_messages=[],
        tool_calls=[],
        step_count=0,
        script=script,
        thread_id="test-thread",
        variables_storage={},
        variable_counter_state=0,
        variable_creation_order=[],
        reflection_apps=[],
        reflection_enable_find_tools=False,
        reflection_skills_enabled=False,
        reflection_skills_prompt_section="",
    )


async def _run_node(
    script: str,
    new_vars: dict,
    configurable: dict | None = None,
    todos_call_ran: bool = True,
):
    """Invoke the sandbox node with reflection enabled and a mocked reflection agent.

    ``todos_call_ran`` simulates the create_update_todos tool bumping the adapter's
    call counter during execution. Returns (result, reflection_task_mock).
    """
    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node import create_sandbox_node

    adapter = _build_adapter()
    state = _build_state(script)

    reflection_agent = MagicMock()
    reflection_agent.ainvoke = AsyncMock(return_value=SimpleNamespace(content="reflection text"))
    reflection_task_mock = MagicMock(return_value=reflection_agent)

    async def fake_exec(*args, **kwargs):
        if todos_call_ran:
            adapter._task_todos_call_count += 1
        return "ok", new_vars

    base = "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node"
    with (
        patch(
            f"{base}.CodeExecutor.eval_with_tools_async",
            new=AsyncMock(side_effect=fake_exec),
        ),
        patch(f"{base}.settings.policy.enabled", new=False),
        patch(f"{base}.settings.advanced_features.reflection_enabled", new=True),
        patch(f"{base}.reflection_task", new=reflection_task_mock),
        patch(
            f"{base}.prepare_reflection_context",
            new=AsyncMock(return_value=("history", "coder output")),
        ),
        patch(f"{base}.clamp_watsonx_completion_for_messages", new=MagicMock()),
    ):
        node = create_sandbox_node(adapter, base_thread_id="base-thread", base_apps_list=[])
        cfg = {"llm": MagicMock(), **(configurable or {})}
        result = await node(state, config={"configurable": cfg})

    return result, reflection_task_mock


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reflection_skipped_after_todo_only_block():
    result, reflection_task_mock = await _run_node(TODO_SCRIPT, {"todos": TODOS_VAR})

    assert not reflection_task_mock.called, "reflection must not run after a block that only updated todos"
    # The plan still reaches state — skipping reflection must not cost the todo update.
    assert result["task_todos"] == TODOS_VAR["todos"]
    # And no reflection summary is appended to the execution message.
    assert "Summary:" not in result["chat_messages"][-1].content


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reflection_still_runs_when_todo_update_shares_the_block():
    """The Isolation Rule is routinely violated; a block that also did real work
    must keep its reflection pass."""
    _, reflection_task_mock = await _run_node(MIXED_SCRIPT, {"todos": TODOS_VAR, "cart": {"cart_items": []}})

    assert reflection_task_mock.called


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reflection_still_runs_for_ordinary_work():
    _, reflection_task_mock = await _run_node(
        "cart = await amazon_show_cart_cart_get()\nprint(cart)", {"cart": {"cart_items": []}}
    )

    assert reflection_task_mock.called


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reflection_skipped_when_todo_var_is_reassigned():
    """Regression for the first branch run: 12 of 28 todo-only turns still reflected.

    ``filter_new_variables`` returns ``set(locals) - original_keys``, so the second
    ``todos = await create_update_todos(...)`` of a task produces NO new variable.
    Keying the skip off new_vars therefore missed every repeat update — the skip must
    key off the tool's call counter instead.
    """
    _, reflection_task_mock = await _run_node(TODO_SCRIPT, {}, todos_call_ran=True)

    assert not reflection_task_mock.called, (
        "a repeat todo update produces no new variable; the skip must not depend on one"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reflection_still_runs_when_the_todo_call_never_ran():
    """A block that meant to update todos but failed before the tool executed is
    exactly where the model needs reflection's help."""
    _, reflection_task_mock = await _run_node(TODO_SCRIPT, {}, todos_call_ran=False)

    assert reflection_task_mock.called


@pytest.mark.unit
@pytest.mark.asyncio
async def test_skip_can_be_disabled_via_configurable():
    """Lets an evaluation run A/B the optimization without switching branches."""
    _, reflection_task_mock = await _run_node(
        TODO_SCRIPT, {"todos": TODOS_VAR}, configurable={"skip_reflection_on_todo_only": False}
    )

    assert reflection_task_mock.called
