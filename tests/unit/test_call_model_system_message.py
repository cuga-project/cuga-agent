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
