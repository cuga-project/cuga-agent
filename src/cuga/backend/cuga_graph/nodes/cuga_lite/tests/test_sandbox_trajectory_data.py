"""Verify that sandbox_node records native dict payloads for User_output_variables (issue #585)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node import create_sandbox_node

pytestmark = pytest.mark.unit


def _build_mock_adapter():
    adapter = MagicMock()
    adapter._tools_context = {}
    adapter._weak_schema_tool_names = frozenset()
    adapter._observed_tool_shapes = {}
    adapter._tracker = MagicMock()
    adapter._tracker.collect_step = MagicMock()
    adapter.messages_key = "chat_messages"
    adapter.get_messages = MagicMock(return_value=[])
    adapter.resolve_max_steps = MagicMock(return_value=1000)
    return adapter


def _build_state():
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
        script="x = 1",
        thread_id="test-thread",
        variables_storage={},
        variable_counter_state=0,
        variable_creation_order=[],
        reflection_apps=[],
        reflection_enable_find_tools=False,
        reflection_skills_enabled=False,
        reflection_skills_prompt_section="",
    )


@pytest.mark.asyncio
async def test_sandbox_records_native_dict_for_user_output_variables():
    """sandbox_node must pass new_vars as a native dict to User_output_variables step."""
    adapter = _build_mock_adapter()
    state = _build_state()

    new_vars = {"result": {"id": 1}, "count": 2}

    with (
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node.CodeExecutor.eval_with_tools_async",
            new=AsyncMock(return_value=("some output", new_vars)),
        ),
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node.settings.policy.enabled",
            new=False,
        ),
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node.settings.advanced_features.reflection_enabled",
            new=False,
        ),
    ):
        node = create_sandbox_node(adapter, base_thread_id="base-thread", base_apps_list=[])
        await node(state, config={"configurable": {}})

    variables_step = next(
        call.kwargs["step"]
        for call in adapter._tracker.collect_step.call_args_list
        if call.kwargs["step"].name == "User_output_variables"
    )
    assert variables_step.data == {"result": {"id": 1}, "count": 2}
