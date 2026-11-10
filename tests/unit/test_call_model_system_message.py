"""A SystemMessage passed to the agent reaches the model as part of the system prompt."""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from tests.unit.test_variables_addendum_not_persisted import _run_turn

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
