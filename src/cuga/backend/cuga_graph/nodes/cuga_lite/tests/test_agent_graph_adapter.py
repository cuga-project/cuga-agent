"""Phase 4 — AgentGraphAdapter hook correctness tests.

Pins the hook overrides that AgentGraphAdapter contributes to the shared
call_model node:

1. Class-level attributes (messages_key, execute_node_name, etc.)
2. State-reading hooks: get_messages, get_few_shot_messages, get_pi,
   get_variables_storage, get_variable_manager
3. Lifecycle hooks: get_tracker, get_invoke_config, normalize_response,
   on_response_processed, build_metadata_update, classify_auto_continue
4. System content augmentation: prepare_system_content with/without todos

These tests do NOT test the full prepare_tools_and_apps / sandbox logic —
those are covered by the existing Lite integration tests.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage


def _get_adapter_class():
    from cuga.backend.cuga_graph.nodes.cuga_lite.agent_graph_adapter import AgentGraphAdapter

    return AgentGraphAdapter


def _make_tracker():
    tracker = MagicMock()
    tracker.collect_step = MagicMock()
    return tracker


def _make_adapter(*, task_todos_ref=None, tools_context_ref=None, base_tool_provider=None):
    AgentGraphAdapter = _get_adapter_class()
    return AgentGraphAdapter(
        tracker=_make_tracker(),
        base_callbacks=[],
        task_todos_ref=task_todos_ref or [],
        tools_context_ref=tools_context_ref or {},
        base_tool_provider=base_tool_provider,
    )


# ── 1. Class-level attributes ──────────────────────────────────────────────


def test_messages_key_is_chat_messages():
    AgentGraphAdapter = _get_adapter_class()
    assert AgentGraphAdapter.messages_key == "chat_messages"


def test_execute_node_name_is_sandbox():
    AgentGraphAdapter = _get_adapter_class()
    assert AgentGraphAdapter.execute_node_name == "sandbox"


def test_metadata_key_is_cuga_lite_metadata():
    AgentGraphAdapter = _get_adapter_class()
    assert AgentGraphAdapter.metadata_key == "cuga_lite_metadata"


def test_sender_name_is_cuga_lite():
    AgentGraphAdapter = _get_adapter_class()
    assert AgentGraphAdapter.sender_name == "CugaLite"


# ── 2. State-reading hooks ─────────────────────────────────────────────────


def test_get_messages_returns_chat_messages():
    adapter = _make_adapter()
    msg = HumanMessage(content="hi")
    state = SimpleNamespace(chat_messages=[msg])
    assert adapter.get_messages(state) == [msg]


def test_get_messages_returns_empty_list_when_none():
    adapter = _make_adapter()
    state = SimpleNamespace(chat_messages=None)
    assert adapter.get_messages(state) == []


def test_get_few_shot_messages_returns_mcp_few_shot():
    adapter = _make_adapter()
    examples = [{"role": "user", "content": "example"}]
    state = SimpleNamespace(mcp_few_shot_messages=examples)
    assert adapter.get_few_shot_messages(state) == examples


def test_get_few_shot_messages_returns_empty_when_none():
    adapter = _make_adapter()
    state = SimpleNamespace(mcp_few_shot_messages=None)
    assert adapter.get_few_shot_messages(state) == []


def test_get_pi_returns_state_pi():
    adapter = _make_adapter()
    state = SimpleNamespace(pi="You are helpful.")
    assert adapter.get_pi(state) == "You are helpful."


def test_get_pi_returns_none_when_missing():
    adapter = _make_adapter()
    state = SimpleNamespace(pi=None)
    assert adapter.get_pi(state) is None


def test_get_variables_storage_returns_state_variables_storage():
    adapter = _make_adapter()
    storage = {"x": {"value": 42}}
    state = SimpleNamespace(variables_storage=storage)
    assert adapter.get_variables_storage(state) is storage


# ── 3. Tracker hook ───────────────────────────────────────────────────────


def test_get_tracker_returns_injected_tracker():
    AgentGraphAdapter = _get_adapter_class()
    tracker = _make_tracker()
    adapter = AgentGraphAdapter(
        tracker=tracker,
        base_callbacks=[],
        task_todos_ref=[],
        tools_context_ref={},
        base_tool_provider=None,
    )
    assert adapter.get_tracker() is tracker


# ── 4. invoke_config hook ─────────────────────────────────────────────────


def test_get_invoke_config_returns_callbacks_from_configurable():
    adapter = _make_adapter()
    cb = object()
    config = {"callbacks": [cb]}
    result = adapter.get_invoke_config(config)
    assert result == {"callbacks": [cb]}


def test_get_invoke_config_falls_back_to_base_callbacks():
    AgentGraphAdapter = _get_adapter_class()
    base_cb = object()
    adapter = AgentGraphAdapter(
        tracker=_make_tracker(),
        base_callbacks=[base_cb],
        task_todos_ref=[],
        tools_context_ref={},
        base_tool_provider=None,
    )
    result = adapter.get_invoke_config({})
    assert result == {"callbacks": [base_cb]}


# ── 5. normalize_response hook ────────────────────────────────────────────


def test_normalize_response_strips_empty_content():
    adapter = _make_adapter()
    response = SimpleNamespace(content="  hello  ", additional_kwargs={})
    content, reasoning = adapter.normalize_response(response)
    # normalize_assistant_text strips whitespace
    assert content.strip() == "hello"


def test_normalize_response_extracts_reasoning():
    adapter = _make_adapter()
    response = SimpleNamespace(
        content="hi",
        additional_kwargs={"reasoning_content": "I thought about it"},
    )
    _, reasoning = adapter.normalize_response(response)
    assert reasoning == "I thought about it"


# ── 6. on_response_processed hook ────────────────────────────────────────


def test_on_response_processed_calls_tracker_for_code():
    AgentGraphAdapter = _get_adapter_class()
    tracker = _make_tracker()
    adapter = AgentGraphAdapter(
        tracker=tracker,
        base_callbacks=[],
        task_todos_ref=[],
        tools_context_ref={},
        base_tool_provider=None,
    )
    state = SimpleNamespace()
    adapter.on_response_processed(state, code="print(1)", content="```python\nprint(1)\n```")
    tracker.collect_step.assert_called()


def test_on_response_processed_calls_tracker_for_nl():
    AgentGraphAdapter = _get_adapter_class()
    tracker = _make_tracker()
    adapter = AgentGraphAdapter(
        tracker=tracker,
        base_callbacks=[],
        task_todos_ref=[],
        tools_context_ref={},
        base_tool_provider=None,
    )
    state = SimpleNamespace()
    adapter.on_response_processed(state, code=None, content="The answer is 42.")
    tracker.collect_step.assert_called()


# ── 7. build_metadata_update hook ────────────────────────────────────────


def test_build_metadata_update_cleans_empty_response_meta():
    adapter = _make_adapter()
    state = SimpleNamespace(cuga_lite_metadata={"_empty_response_correction": True, "other_key": 1})
    result = adapter.build_metadata_update(state, playbook_fired=False)
    assert "_empty_response_correction" not in result
    assert result["other_key"] == 1


def test_build_metadata_update_adds_playbook_flag_when_fired():
    adapter = _make_adapter()
    state = SimpleNamespace(cuga_lite_metadata={"some_key": True})
    result = adapter.build_metadata_update(state, playbook_fired=True)
    assert result["playbook_guidance_added"] is True


# ── 8. classify_auto_continue hook ───────────────────────────────────────


@pytest.mark.asyncio
async def test_classify_auto_continue_delegates_to_nl_classifier():
    adapter = _make_adapter()
    state = SimpleNamespace()
    mock_model = MagicMock()

    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.graph_adapter.classify_nl_auto_continue",
        new_callable=AsyncMock,
        return_value=True,
    ) as mock_classify:
        result = await adapter.classify_auto_continue(state, mock_model, "Let me continue.", "thought")
        mock_classify.assert_called_once_with(mock_model, "Let me continue.", "thought")
        assert result is True


@pytest.mark.asyncio
async def test_classify_auto_continue_returns_false_when_not_continuing():
    adapter = _make_adapter()
    state = SimpleNamespace()

    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.graph_adapter.classify_nl_auto_continue",
        new_callable=AsyncMock,
        return_value=False,
    ):
        result = await adapter.classify_auto_continue(state, None, "All done.", None)
        assert result is False


# ── 9. prepare_system_content hook ───────────────────────────────────────


def test_prepare_system_content_no_todos_returns_base_prompt():
    adapter = _make_adapter(task_todos_ref=[])
    state = SimpleNamespace(task_todos=None)
    result = adapter.prepare_system_content(state, {}, "You are an agent.")
    assert result == "You are an agent."


def test_prepare_system_content_appends_todos_ref_when_present():
    todos = [{"title": "Step 1", "status": "pending"}]
    adapter = _make_adapter(task_todos_ref=todos)
    state = SimpleNamespace(task_todos=None)
    result = adapter.prepare_system_content(state, {}, "You are an agent.")
    assert result != "You are an agent."
    assert len(result) > len("You are an agent.")


def test_resolve_max_steps_uses_override_when_given():
    adapter = _make_adapter()
    state = SimpleNamespace(cuga_lite_max_steps=None)
    assert adapter.resolve_max_steps(state, 10) == 10


def test_resolve_max_steps_uses_state_when_set():
    adapter = _make_adapter()
    state = SimpleNamespace(cuga_lite_max_steps=25)
    assert adapter.resolve_max_steps(state, None) == 25
