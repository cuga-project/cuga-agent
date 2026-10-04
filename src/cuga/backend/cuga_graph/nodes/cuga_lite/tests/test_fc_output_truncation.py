"""Function-calling turns cut off at the output-token limit.

Calls whose JSON arguments all parsed are complete and run as usual. A call whose
arguments never closed (provider puts it in ``invalid_tool_calls``) or a half-written
answer is discarded — not persisted, so no dangling tool-call id — and the request is
sent again with the note appended to the outbound copy only.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END

from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.model_call_recovery import (
    OUTPUT_TRUNCATED_KEY,
    OUTPUT_TRUNCATED_NOTE,
)
from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.graph_adapter import AgentGraphAdapter

pytestmark = pytest.mark.unit

FC = {"cuga_lite_execution_mode": "function_calling"}
_CALL = {"name": "add", "args": {"a": 1, "b": 2}, "id": "c1", "type": "tool_call"}
_CUT_CALL = {
    "name": "add",
    "args": '{"a": 1, "b"',
    "id": "c2",
    "error": "bad json",
    "type": "invalid_tool_call",
}
_CUT = {"finish_reason": "length"}


class _Model:
    def __init__(self, response: Any):
        self.response = response
        self.seen: List[list] = []

    async def ainvoke(self, messages, config=None, **kwargs):
        self.seen.append(list(messages))
        return self.response


def _adapter() -> AgentGraphAdapter:
    return AgentGraphAdapter(
        tracker=MagicMock(),
        base_callbacks=[],
        task_todos_ref=[],
        tools_context_ref={},
        base_tool_provider=None,
    )


def _state(metadata=None, step_count=0):
    return SimpleNamespace(
        chat_messages=[HumanMessage(content="what is 1+2?")],
        step_count=step_count,
        cuga_lite_metadata=metadata if metadata is not None else {},
        mcp_few_shot_messages=[],
        cuga_lite_max_steps=None,
    )


async def _turn(model, state):
    return await _adapter().execute_call_model_fc(
        state=state,
        config=None,
        configurable=FC,
        active_model=object(),
        bound=model,
        invoke_config={},
        system_content="SYS",
        modified_messages=list(state.chat_messages),
        budget_exhausted=False,
        playbook_fired=False,
    )


@pytest.fixture(autouse=True)
def _policy_off(monkeypatch):
    from cuga.config import settings

    monkeypatch.setattr(settings.policy, "enabled", False, raising=False)
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_output_truncation_retries", 2, raising=False)


@pytest.mark.asyncio
async def test_complete_tool_calls_run_even_when_the_reply_was_cut():
    cmd = await _turn(_Model(AIMessage(content="", tool_calls=[_CALL], response_metadata=_CUT)), _state())
    assert cmd.goto == "tool_exec"


@pytest.mark.asyncio
async def test_a_call_with_unclosed_arguments_is_discarded_and_retried():
    response = AIMessage(
        content="", tool_calls=[_CALL], invalid_tool_calls=[_CUT_CALL], response_metadata=_CUT
    )
    state = _state()

    cmd = await _turn(_Model(response), state)

    assert cmd.goto == "call_model"
    assert cmd.update["chat_messages"] == state.chat_messages, "no dangling tool-call ids are persisted"
    assert cmd.update["cuga_lite_metadata"][OUTPUT_TRUNCATED_KEY] == 1
    assert cmd.update["step_count"] == 1


@pytest.mark.asyncio
async def test_a_cut_answer_is_retried():
    cmd = await _turn(_Model(AIMessage(content="The answer is", response_metadata=_CUT)), _state())
    assert cmd.goto == "call_model"


@pytest.mark.asyncio
async def test_the_retry_carries_the_note_outbound_only():
    model = _Model(AIMessage(content="It is 3."))
    cmd = await _turn(model, _state(metadata={OUTPUT_TRUNCATED_KEY: 1}, step_count=1))

    (outbound,) = model.seen
    assert OUTPUT_TRUNCATED_NOTE in str(outbound[-1].content)
    assert cmd.goto == END
    assert OUTPUT_TRUNCATED_KEY not in cmd.update["cuga_lite_metadata"]
    assert all(OUTPUT_TRUNCATED_NOTE not in str(m.content) for m in cmd.update["chat_messages"])


@pytest.mark.asyncio
async def test_no_retry_once_the_retries_are_spent():
    cmd = await _turn(
        _Model(AIMessage(content="The answer is", response_metadata=_CUT)),
        _state(metadata={OUTPUT_TRUNCATED_KEY: 2}, step_count=2),
    )
    assert cmd.goto == END and cmd.update["final_answer"] == "The answer is"
