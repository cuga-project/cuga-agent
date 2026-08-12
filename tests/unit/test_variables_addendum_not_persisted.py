"""Regression tests for #600 — the "## Available Variables" addendum must be sent
to the model but never persisted into conversation history.

Persisting it made every message that was once ``is_last`` retain its own copy
(each capped by ``variables_summary_max_length`` but unbounded in count), inflating
context growth ~5x and eventually tripping the provider's context limit mid-task.

These tests drive the real ``call_model`` node from
``cuga_agent_core.graph.shared_nodes`` — they do not re-implement its logic.
"""

from __future__ import annotations

from typing import Any, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_nodes import create_call_model_node

pytestmark = pytest.mark.unit

ADDENDUM_MARKER = "## Available Variables"
VAR_SUMMARY = "# Variables Summary\n\n## my_var\nvalue preview"


class _State:
    """Minimal stand-in for the graph state used by call_model."""

    def __init__(self, messages: List[Any]):
        self.chat_messages = messages
        self.prepared_prompt = "BASE PROMPT"
        self.variable_counter_state = None
        self.variable_creation_order = None
        self.step_count = 0


def _make_settings() -> MagicMock:
    s = MagicMock()
    s.policy.enabled = False
    s.advanced_features.variables_summary_max_length = 12000
    return s


def _make_adapter(captured_outbound: List[List[dict]]) -> MagicMock:
    a = MagicMock()
    a.messages_key = "chat_messages"
    a.metadata_key = "meta"
    a.execute_node_name = "sandbox"
    a.sender_name = "unit-test"

    a.prepare_system_content.return_value = "SYSTEM"
    a.get_few_shot_messages.return_value = []
    a.get_pi.return_value = None
    a.get_metadata.return_value = {}
    a.build_metadata_update.return_value = {}
    a.get_tools_needing_probing.return_value = []
    a.get_tracker.return_value = None
    a.get_variables_storage.return_value = {}
    a.resolve_max_steps.return_value = 70
    a.get_messages = lambda state: state.chat_messages

    # No code block in the reply -> call_model takes the terminal path and
    # returns the persisted message list, which is what we assert on.
    a.normalize_response.return_value = ("done, no code here", "")
    a.on_response_processed.return_value = None
    a.classify_auto_continue = AsyncMock(return_value=False)
    a.resolve_bind_tools = AsyncMock(return_value=None)

    var_manager = MagicMock()
    var_manager.get_variable_names.return_value = ["my_var"]
    var_manager.get_variables_summary.return_value = VAR_SUMMARY
    a.get_variable_manager.return_value = var_manager

    async def _ainvoke(bound, messages_for_model, invoke_config):
        captured_outbound.append(messages_for_model)
        return MagicMock()

    a.ainvoke_model = AsyncMock(side_effect=_ainvoke)
    return a


async def _run_turn(messages: List[Any], captured: List[List[dict]]):
    """Invoke the real call_model node once; return its persisted message list."""
    adapter = _make_adapter(captured)
    node = create_call_model_node(adapter, MagicMock(), _make_settings())

    async def _passthrough(msgs, *args, **kwargs):
        return msgs

    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_nodes.apply_context_summarization",
        new=AsyncMock(side_effect=_passthrough),
    ):
        cmd = await node(_State(messages), {"configurable": {}})
    return cmd.update[adapter.messages_key]


@pytest.mark.asyncio
async def test_addendum_sent_to_model_but_not_persisted():
    """One turn: model sees the addendum; stored history does not."""
    captured: List[List[dict]] = []
    persisted = await _run_turn([HumanMessage(content="first request")], captured)

    outbound_users = [m for m in captured[0] if m.get("role") == "user"]
    assert any(ADDENDUM_MARKER in m["content"] for m in outbound_users), (
        "the addendum must still reach the model"
    )

    stored = [m for m in persisted if isinstance(m, HumanMessage)]
    assert stored, "expected the human message to be persisted"
    assert not any(ADDENDUM_MARKER in (m.content or "") for m in stored), (
        "the addendum must NOT be written back into history (#600)"
    )


@pytest.mark.asyncio
async def test_addendum_does_not_accumulate_across_turns():
    """Multi-turn: exactly one copy goes out per turn, zero accumulate in history."""
    captured: List[List[dict]] = []
    messages: List[Any] = [HumanMessage(content="turn 1")]

    for turn in range(2, 5):
        persisted = await _run_turn(messages, captured)
        # feed history forward the way the graph does, then add the next user turn
        messages = list(persisted) + [HumanMessage(content=f"turn {turn}")]

    stored_copies = sum(
        1 for m in messages if isinstance(m, HumanMessage) and ADDENDUM_MARKER in (m.content or "")
    )
    assert stored_copies == 0, (
        f"history accumulated {stored_copies} copies of the variables addendum; "
        "it must never be persisted (#600)"
    )

    for turn_idx, outbound in enumerate(captured):
        copies = sum(1 for m in outbound if m.get("role") == "user" and ADDENDUM_MARKER in m["content"])
        assert copies == 1, f"turn {turn_idx}: expected exactly 1 addendum, got {copies}"


@pytest.mark.asyncio
async def test_addendum_absent_when_no_variables_exist():
    """No variables -> no addendum anywhere."""
    captured: List[List[dict]] = []
    adapter = _make_adapter(captured)
    adapter.get_variable_manager.return_value.get_variable_names.return_value = []
    node = create_call_model_node(adapter, MagicMock(), _make_settings())

    async def _passthrough(msgs, *args, **kwargs):
        return msgs

    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_nodes.apply_context_summarization",
        new=AsyncMock(side_effect=_passthrough),
    ):
        cmd = await node(_State([HumanMessage(content="hello")]), {"configurable": {}})

    persisted = cmd.update[adapter.messages_key]
    assert not any(ADDENDUM_MARKER in m.get("content", "") for m in captured[0])
    assert not any(
        ADDENDUM_MARKER in (m.content or "") for m in persisted if isinstance(m, (HumanMessage, AIMessage))
    )
