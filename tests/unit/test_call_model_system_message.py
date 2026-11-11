"""A SystemMessage passed to the agent reaches the model as part of the system prompt."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_nodes import create_call_model_node
from tests.unit.test_variables_addendum_not_persisted import _make_adapter, _make_settings, _run_turn, _State

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["Answer in English.", [{"type": "text", "text": "Answer in English."}]])
async def test_system_message_is_appended_to_the_system_prompt(content):
    captured: list[list[dict]] = []
    persisted = await _run_turn(
        [SystemMessage(content=content), HumanMessage(content="Quel temps fait-il ?")], captured
    )

    outbound = captured[0]
    assert outbound[0]["role"] == "system"
    assert outbound[0]["content"].endswith("\n\nAnswer in English.")
    assert [m["role"] for m in outbound].count("system") == 1
    assert any(isinstance(m, SystemMessage) for m in persisted), "kept in history for later turns"


@pytest.mark.asyncio
async def test_a_system_message_sent_every_turn_is_added_once():
    """An SDK caller sends its SystemMessage with every turn, and the thread keeps each copy."""
    from langchain_core.messages import AIMessage

    captured: list[list[dict]] = []
    rule = "Answer in English."
    await _run_turn(
        [
            SystemMessage(content=rule),
            HumanMessage(content="Quel temps fait-il ?"),
            AIMessage(content="It is sunny."),
            SystemMessage(content=rule),
            HumanMessage(content="Et demain ?"),
        ],
        captured,
    )

    assert captured[0][0]["content"].count(rule) == 1


async def _run_with_window(messages, captured, keep_last):
    """Run call_model with summarization replaced by a window over the last ``keep_last`` messages."""
    adapter = _make_adapter(captured)
    node = create_call_model_node(adapter, MagicMock(), _make_settings())
    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_nodes.apply_context_summarization",
        new=AsyncMock(side_effect=lambda msgs, *args, **kwargs: msgs[-keep_last:]),
    ):
        cmd = await node(_State(messages), {"configurable": {}})
    return cmd.update[adapter.messages_key]


@pytest.mark.asyncio
async def test_a_system_message_sent_once_survives_when_older_turns_are_dropped():
    """Summarization keeps only recent turns; the caller's instruction holds for the whole thread."""
    from langchain_core.messages import AIMessage

    captured: list[list[dict]] = []
    rule = "Answer in English."
    persisted = await _run_with_window(
        [
            SystemMessage(content=rule),
            HumanMessage(content="Quel temps fait-il ?"),
            AIMessage(content="It is sunny."),
            HumanMessage(content="Et demain ?"),
        ],
        captured,
        keep_last=2,
    )

    assert captured[0][0]["content"].endswith(f"\n\n{rule}")
    assert isinstance(persisted[0], SystemMessage), "kept in history for later turns"


@pytest.mark.asyncio
async def test_history_keeps_one_copy_of_a_system_message_sent_every_turn():
    """Summarization no longer trims system messages, so the thread must not keep a copy per turn."""
    from langchain_core.messages import AIMessage

    rule = "Answer in English."
    persisted = await _run_turn(
        [
            SystemMessage(content=rule),
            HumanMessage(content="Quel temps fait-il ?"),
            AIMessage(content="It is sunny."),
            SystemMessage(content=rule),
            HumanMessage(content="Et demain ?"),
        ],
        [],
    )

    assert [m.text for m in persisted if isinstance(m, SystemMessage)] == [rule]


@pytest.mark.asyncio
async def test_a_system_text_inside_a_longer_one_is_still_added():
    """Only an exact repeat is skipped, not a text that appears inside an earlier instruction."""
    captured: list[list[dict]] = []
    await _run_turn(
        [
            SystemMessage(content="Answer in English. Be brief."),
            SystemMessage(content="Answer in English."),
            SystemMessage(content="Answer in English."),
            HumanMessage(content="Quel temps fait-il ?"),
        ],
        captured,
    )

    assert captured[0][0]["content"] == "SYSTEM\n\nAnswer in English. Be brief.\n\nAnswer in English."
