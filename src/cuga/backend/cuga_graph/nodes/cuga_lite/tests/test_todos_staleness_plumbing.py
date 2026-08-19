"""Plan staleness must reach the system prompt, and must not leak across tasks (#676).

The age of the plan is the signal that keeps a stale `in_progress` from being read as
current fact. It is computed from two pieces of state that live in different nodes —
the sandbox node stamps `_task_todos_updated_at_step`, the adapter renders it — so this
pins the wiring rather than the formatting (covered by test_todos_plan_provenance).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.graph_adapter import AgentGraphAdapter

TODOS = [{"text": "Fetch cart", "status": "in_progress"}]
TODO_SCRIPT = 'todos = await create_update_todos([{"text": "Fetch cart", "status": "in_progress"}])'


def _adapter(task_todos_ref):
    return AgentGraphAdapter(
        tracker=MagicMock(),
        base_callbacks=[],
        task_todos_ref=task_todos_ref,
        tools_context_ref=None,
        base_tool_provider=MagicMock(),
        model=MagicMock(),
        prompt_template=MagicMock(),
        instructions="",
        special_instructions=None,
    )


@pytest.mark.unit
def test_adapter_renders_plan_age_from_the_stamp():
    adapter = _adapter(list(TODOS))
    adapter._task_todos_updated_at_step = 2

    content = adapter.prepare_system_content(SimpleNamespace(step_count=5), {}, "BASE")

    assert "You last updated it 3 steps ago." in content
    assert "source of truth" not in content.lower()


@pytest.mark.unit
def test_adapter_omits_age_when_never_stamped():
    adapter = _adapter(list(TODOS))

    content = adapter.prepare_system_content(SimpleNamespace(step_count=5), {}, "BASE")

    assert "updated it" not in content
    assert "Fetch cart" in content


@pytest.mark.unit
def test_adapter_survives_state_without_step_count():
    adapter = _adapter(list(TODOS))
    adapter._task_todos_updated_at_step = 2

    content = adapter.prepare_system_content(SimpleNamespace(), {}, "BASE")

    assert "Fetch cart" in content


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sandbox_node_stamps_the_step_on_a_todo_update():
    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node import create_sandbox_node

    adapter = MagicMock()
    adapter._tools_context = {}
    adapter._weak_schema_tool_names = frozenset()
    adapter._observed_tool_shapes = {}
    adapter._task_todos_call_count = 0
    adapter._task_todos_updated_at_step = None
    adapter._tracker = MagicMock()
    adapter.messages_key = "chat_messages"
    adapter.get_messages = MagicMock(return_value=[])
    adapter.resolve_max_steps = MagicMock(return_value=1000)

    variables_manager = MagicMock()
    variables_manager.get_variable_names = MagicMock(return_value=[])
    state = SimpleNamespace(
        variables_manager=variables_manager,
        chat_messages=[],
        tool_calls=[],
        step_count=4,
        script=TODO_SCRIPT,
        thread_id="t",
        variables_storage={},
        variable_counter_state=0,
        variable_creation_order=[],
        reflection_apps=[],
        reflection_enable_find_tools=False,
        reflection_skills_enabled=False,
        reflection_skills_prompt_section="",
    )

    async def fake_exec(*args, **kwargs):
        adapter._task_todos_call_count += 1
        return "ok", {}

    base = "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node"
    with (
        patch(f"{base}.CodeExecutor.eval_with_tools_async", new=AsyncMock(side_effect=fake_exec)),
        patch(f"{base}.settings.policy.enabled", new=False),
        patch(f"{base}.settings.advanced_features.reflection_enabled", new=False),
    ):
        node = create_sandbox_node(adapter, base_thread_id="t", base_apps_list=[])
        await node(state, config={"configurable": {}})

    # Stamped at the step the block produced, which is the step_count the node returns.
    assert adapter._task_todos_updated_at_step == 5


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sandbox_node_leaves_the_stamp_alone_without_a_todo_update():
    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node import create_sandbox_node

    adapter = MagicMock()
    adapter._tools_context = {}
    adapter._weak_schema_tool_names = frozenset()
    adapter._observed_tool_shapes = {}
    adapter._task_todos_call_count = 0
    adapter._task_todos_updated_at_step = 2
    adapter._tracker = MagicMock()
    adapter.messages_key = "chat_messages"
    adapter.get_messages = MagicMock(return_value=[])
    adapter.resolve_max_steps = MagicMock(return_value=1000)

    variables_manager = MagicMock()
    variables_manager.get_variable_names = MagicMock(return_value=[])
    state = SimpleNamespace(
        variables_manager=variables_manager,
        chat_messages=[],
        tool_calls=[],
        step_count=7,
        script="cart = await amazon_show_cart_cart_get()",
        thread_id="t",
        variables_storage={},
        variable_counter_state=0,
        variable_creation_order=[],
        reflection_apps=[],
        reflection_enable_find_tools=False,
        reflection_skills_enabled=False,
        reflection_skills_prompt_section="",
    )

    base = "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node"
    with (
        patch(
            f"{base}.CodeExecutor.eval_with_tools_async",
            new=AsyncMock(return_value=("ok", {"cart": {"cart_items": []}})),
        ),
        patch(f"{base}.settings.policy.enabled", new=False),
        patch(f"{base}.settings.advanced_features.reflection_enabled", new=False),
    ):
        node = create_sandbox_node(adapter, base_thread_id="t", base_apps_list=[])
        await node(state, config={"configurable": {}})

    assert adapter._task_todos_updated_at_step == 2, "a non-todo block must not refresh the plan's age"
