"""Observed-shape capture for weak-schema tools in the sandbox node (issue #272)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node import (
    _describe_observed_shape,
    _needs_shape_tracking,
    _record_weak_schema_shapes,
)


def test_describe_observed_shape_dict():
    assert "dict with keys" in _describe_observed_shape({"a": 1, "b": 2})


def test_describe_observed_shape_list():
    assert "list of 3 items" in _describe_observed_shape(["x", "y", "z"])


def test_describe_observed_shape_empty_list():
    assert _describe_observed_shape([]) == "empty list"


def test_describe_observed_shape_str():
    assert "str of 11 chars" in _describe_observed_shape("hello world")


def test_describe_observed_shape_other_type():
    assert _describe_observed_shape(42) == "int"


def test_record_weak_schema_shapes_stores_first_observation():
    adapter = SimpleNamespace(_weak_schema_tool_names=frozenset({"file_readfile"}), _observed_tool_shapes={})
    _record_weak_schema_shapes(adapter, [{"name": "file_readfile", "result": ["a", "b"], "error": None}])
    assert "file_readfile" in adapter._observed_tool_shapes


def test_record_weak_schema_shapes_skips_non_weak_tools():
    adapter = SimpleNamespace(_weak_schema_tool_names=frozenset({"file_readfile"}), _observed_tool_shapes={})
    _record_weak_schema_shapes(adapter, [{"name": "other_tool", "result": "x", "error": None}])
    assert adapter._observed_tool_shapes == {}


def test_record_weak_schema_shapes_first_observation_wins():
    adapter = SimpleNamespace(
        _weak_schema_tool_names=frozenset({"file_readfile"}),
        _observed_tool_shapes={"file_readfile": "old"},
    )
    _record_weak_schema_shapes(adapter, [{"name": "file_readfile", "result": ["z"], "error": None}])
    assert adapter._observed_tool_shapes["file_readfile"] == "old"


def test_record_weak_schema_shapes_skips_errored_calls():
    adapter = SimpleNamespace(_weak_schema_tool_names=frozenset({"file_readfile"}), _observed_tool_shapes={})
    _record_weak_schema_shapes(adapter, [{"name": "file_readfile", "result": None, "error": "boom"}])
    assert adapter._observed_tool_shapes == {}


def test_record_weak_schema_shapes_noop_when_no_weak_schema_tools():
    adapter = SimpleNamespace(_weak_schema_tool_names=frozenset(), _observed_tool_shapes={})
    _record_weak_schema_shapes(adapter, [{"name": "file_readfile", "result": ["a"], "error": None}])
    assert adapter._observed_tool_shapes == {}


def test_needs_shape_tracking_true_when_weak_tools_unobserved():
    adapter = SimpleNamespace(_weak_schema_tool_names=frozenset({"file_readfile"}), _observed_tool_shapes={})
    assert _needs_shape_tracking(adapter) is True


def test_needs_shape_tracking_false_when_all_weak_tools_observed():
    adapter = SimpleNamespace(
        _weak_schema_tool_names=frozenset({"file_readfile"}),
        _observed_tool_shapes={"file_readfile": "list of 3 items"},
    )
    assert _needs_shape_tracking(adapter) is False


def test_needs_shape_tracking_false_when_no_weak_tools():
    adapter = SimpleNamespace(_weak_schema_tool_names=frozenset(), _observed_tool_shapes={})
    assert _needs_shape_tracking(adapter) is False


def test_needs_shape_tracking_true_when_some_observed_some_not():
    adapter = SimpleNamespace(
        _weak_schema_tool_names=frozenset({"a", "b"}),
        _observed_tool_shapes={"a": "observed"},
    )
    assert _needs_shape_tracking(adapter) is True


def _build_mock_adapter_for_sandbox() -> MagicMock:
    adapter = MagicMock()
    adapter._tools_context = {}
    adapter._weak_schema_tool_names = frozenset({"file_readfile"})
    adapter._observed_tool_shapes = {}
    adapter._tracker = MagicMock()
    adapter._tracker.collect_step = MagicMock()
    adapter.messages_key = "chat_messages"
    adapter.get_messages = MagicMock(return_value=[])
    adapter.resolve_max_steps = MagicMock(return_value=1000)
    return adapter


def _build_sandbox_state() -> SimpleNamespace:
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
async def test_sandbox_tracks_weak_schema_tool_without_exposing_tool_calls_when_opt_out_default():
    """Reproduces and proves the fix for the dead-on-arrival Stage 1 bug:

    With ``track_tool_calls`` absent from configurable (the production
    default of ``False``) and a weak-schema tool name registered on the
    adapter, the sandbox node must still internally enable tracking so
    ``adapter._observed_tool_shapes`` gets populated -- while leaving the
    returned ``tool_calls`` update untouched (empty) for callers who never
    opted into tracking, so we don't grow their persisted state.
    """
    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node import create_sandbox_node
    from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import ToolCallTracker

    adapter = _build_mock_adapter_for_sandbox()
    state = _build_sandbox_state()

    async def fake_eval_with_tools_async(*args, **kwargs):
        # Side effect: simulate the executed script calling the weak-schema tool.
        ToolCallTracker.record_call(
            tool_name="file_readfile",
            arguments={},
            result=["line1", "line2"],
            app_name=None,
            operation_id=None,
        )
        return "ok", {}

    with (
        patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node.CodeExecutor.eval_with_tools_async",
            new=AsyncMock(side_effect=fake_eval_with_tools_async),
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
        # configurable deliberately omits "track_tool_calls" -> defaults to False
        result = await node(state, config={"configurable": {}})

    # The bug: tracking never started, so this would stay empty without the fix.
    assert adapter._observed_tool_shapes.get("file_readfile") is not None, (
        "Weak-schema tool shape must be observed even when track_tool_calls is not opted into"
    )

    # Behavior-preservation: callers who never asked for tracking must not see
    # the tracked call surface in the returned tool_calls update.
    assert result["tool_calls"] == [], (
        "tool_calls update must stay empty for callers who didn't opt into track_tool_calls"
    )
