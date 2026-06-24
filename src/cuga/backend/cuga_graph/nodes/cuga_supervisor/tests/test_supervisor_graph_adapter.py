"""Phase 5 — SupervisorGraphAdapter hook correctness tests.

Pins the hook overrides that SupervisorGraphAdapter contributes to the shared
call_model node and the node-factory contract:

1. Class-level attributes (messages_key, execute_node_name, etc.)
2. State-reading hooks: get_messages, get_variable_manager, get_variables_storage
3. Step-limit hook: resolve_max_steps with override / state / default
4. Factory methods: build_prepare_node and build_execute_node return async callables
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock


def _get_adapter_class():
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.supervisor_graph_adapter import (
        SupervisorGraphAdapter,
    )

    return SupervisorGraphAdapter


def _make_adapter(**kwargs):
    cls = _get_adapter_class()
    return cls(
        agents=kwargs.get("agents", {}),
        special_instructions=kwargs.get("special_instructions", None),
        tool_provider=kwargs.get("tool_provider", None),
    )


# ── 1. Class-level attributes ──────────────────────────────────────────────


def test_messages_key_is_supervisor_chat_messages():
    cls = _get_adapter_class()
    assert cls.messages_key == "supervisor_chat_messages"


def test_execute_node_name_is_execute_agent_tool():
    cls = _get_adapter_class()
    assert cls.execute_node_name == "execute_agent_tool"


def test_metadata_key_is_supervisor_metadata():
    cls = _get_adapter_class()
    assert cls.metadata_key == "supervisor_metadata"


def test_sender_name_is_cuga_supervisor():
    cls = _get_adapter_class()
    assert cls.sender_name == "CugaSupervisor"


# ── 2. get_messages hook ───────────────────────────────────────────────────


def test_get_messages_returns_supervisor_chat_messages():
    adapter = _make_adapter()
    msgs = [MagicMock()]
    state = SimpleNamespace(supervisor_chat_messages=msgs)
    assert adapter.get_messages(state) is msgs


def test_get_messages_returns_empty_list_when_none():
    adapter = _make_adapter()
    state = SimpleNamespace(supervisor_chat_messages=None)
    assert adapter.get_messages(state) == []


# ── 3. resolve_max_steps hook ──────────────────────────────────────────────


def test_resolve_max_steps_uses_override_when_given():
    adapter = _make_adapter()
    state = SimpleNamespace(cuga_lite_max_steps=None)
    assert adapter.resolve_max_steps(state, 20) == 20


def test_resolve_max_steps_uses_state_when_set():
    adapter = _make_adapter()
    state = SimpleNamespace(cuga_lite_max_steps=30)
    assert adapter.resolve_max_steps(state, None) == 30


def test_resolve_max_steps_falls_back_to_settings_default():
    adapter = _make_adapter()
    state = SimpleNamespace(cuga_lite_max_steps=None)
    result = adapter.resolve_max_steps(state, None)
    assert isinstance(result, int)
    assert result > 0


# ── 4. get_variable_manager hook ──────────────────────────────────────────


def test_get_variable_manager_returns_supervisor_variables_manager():
    adapter = _make_adapter()
    sentinel = object()
    state = SimpleNamespace(supervisor_variables_manager=sentinel)
    assert adapter.get_variable_manager(state) is sentinel


# ── 5. get_variables_storage hook ─────────────────────────────────────────


def test_get_variables_storage_returns_supervisor_variables():
    adapter = _make_adapter()
    storage = {"x": {"value": 1}}
    state = SimpleNamespace(supervisor_variables=storage)
    assert adapter.get_variables_storage(state) is storage


def test_get_variables_storage_returns_none_when_missing():
    adapter = _make_adapter()
    state = SimpleNamespace()
    assert adapter.get_variables_storage(state) is None


# ── 6. Factory methods return async callables ──────────────────────────────


def test_build_prepare_node_returns_async_callable():
    adapter = _make_adapter()
    node = adapter.build_prepare_node()
    assert callable(node)
    assert asyncio.iscoroutinefunction(node)


def test_build_execute_node_returns_async_callable():
    adapter = _make_adapter()
    node = adapter.build_execute_node()
    assert callable(node)
    assert asyncio.iscoroutinefunction(node)


def test_prepare_system_content_injects_run_local_task_todos():
    adapter = _make_adapter()
    state = SimpleNamespace(task_todos=[{"text": "Delegate to CRM", "status": "pending"}])
    content = adapter.prepare_system_content(state, {}, "System")
    assert "Delegate to CRM" in content


def test_prepare_system_content_without_todos_returns_base():
    adapter = _make_adapter()
    state = SimpleNamespace(task_todos=None)
    assert adapter.prepare_system_content(state, {}, "System") == "System"


def test_get_invoke_config_returns_callbacks():
    cb = object()
    adapter = _make_adapter()
    adapter._base_callbacks = [cb]
    assert adapter.get_invoke_config({})["callbacks"] == [cb]


# ── 7. resolve_names_from_caller_frame helper ──────────────────────────────


def test_resolve_names_from_caller_frame_is_accessible():
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.supervisor_graph_adapter import (
        _resolve_names_from_caller_frame,
    )

    assert callable(_resolve_names_from_caller_frame)


def test_resolve_names_from_caller_frame_resolves_locals():
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.supervisor_graph_adapter import (
        _resolve_names_from_caller_frame,
    )

    my_local_var = 42  # noqa: F841 — intentionally referenced by name below
    resolved = _resolve_names_from_caller_frame(["my_local_var"])
    assert resolved.get("my_local_var") == 42
