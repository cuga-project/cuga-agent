"""CugaLite's ``execute_call_model_fc``: the function-calling turn.

Codeact: ``None``, nothing invoked. Function-calling: the bound model gets real
message objects (system prompt, few-shot demos, sanitized history), and the
response routes on ``tool_calls`` — to ``tool_exec`` with the assistant turn
kept verbatim, or to END as the final answer. A fenced code block with no
``tool_calls`` is a mode violation: never executed, one corrective turn.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END

from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_nodes import TOOL_BUDGET_EXHAUSTED_INSTRUCTION
from cuga.backend.cuga_graph.nodes.cuga_lite.adapter import graph_adapter as ga
from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.graph_adapter import (
    FC_MODE_VIOLATION_CORRECTION,
    AgentGraphAdapter,
    _sanitize_for_replay,
)

pytestmark = pytest.mark.unit

FC = {"cuga_lite_execution_mode": "function_calling"}


class _Model:
    """Bare scripted model: no MagicMock attribute magic, so helper probes see a plain object."""

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


def _state(messages=None, few_shots=None):
    return SimpleNamespace(
        chat_messages=messages or [HumanMessage(content="what is 1+2?")],
        step_count=0,
        cuga_lite_metadata={},
        mcp_few_shot_messages=few_shots or [],
        cuga_lite_max_steps=None,
    )


async def _turn(adapter, model, state, configurable, *, budget_exhausted=False, system="SYS"):
    return await adapter.execute_call_model_fc(
        state=state,
        config=None,
        configurable=configurable,
        active_model=model,
        bound=model,
        invoke_config={},
        system_content=system,
        modified_messages=list(state.chat_messages),
        budget_exhausted=budget_exhausted,
        playbook_fired=False,
    )


_CALL = {"name": "add", "args": {"a": 1, "b": 2}, "id": "c1", "type": "tool_call"}


@pytest.mark.asyncio
async def test_codeact_returns_none_and_invokes_nothing():
    model = _Model(AIMessage(content="never"))
    assert await _turn(_adapter(), model, _state(), {}) is None
    assert await _turn(_adapter(), model, _state(), {"cuga_lite_execution_mode": "codeact"}) is None
    assert model.seen == []


@pytest.mark.asyncio
async def test_tool_calls_route_to_tool_exec_with_the_assistant_turn_verbatim():
    response = AIMessage(content="", tool_calls=[_CALL])
    model = _Model(response)
    state = _state()

    cmd = await _turn(_adapter(), model, state, FC)

    assert cmd.goto == "tool_exec"
    persisted = cmd.update["chat_messages"]
    assert persisted[:-1] == state.chat_messages
    assert persisted[-1] is response, "tool_calls must reach tool_exec untouched, ids included"
    assert cmd.update["script"] is None and cmd.update["step_count"] == 1

    (outbound,) = model.seen
    assert isinstance(outbound[0], SystemMessage) and outbound[0].content == "SYS"
    assert outbound[1:] == state.chat_messages, "history goes out as message objects, not the CodeAct dicts"


@pytest.mark.asyncio
async def test_plain_text_is_the_final_answer():
    cmd = await _turn(_adapter(), _Model(AIMessage(content="It is 3.")), _state(), FC)

    assert cmd.goto == END
    assert cmd.update["final_answer"] == "It is 3." and cmd.update["execution_complete"] is True
    assert isinstance(cmd.update["chat_messages"][-1], AIMessage)


@pytest.mark.asyncio
async def test_empty_answer_falls_back_to_the_last_tool_result():
    history = [
        HumanMessage(content="q"),
        AIMessage(content="", tool_calls=[_CALL]),
        ToolMessage(content="3", tool_call_id="c1", name="add"),
    ]
    cmd = await _turn(_adapter(), _Model(AIMessage(content="")), _state(history), FC)

    assert cmd.goto == END and cmd.update["final_answer"] == "3"


@pytest.mark.asyncio
async def test_code_block_without_tool_calls_is_a_mode_violation_never_executed():
    state = _state()
    cmd = await _turn(_adapter(), _Model(AIMessage(content="```python\nawait add(a=1, b=2)\n```")), state, FC)

    assert cmd.goto == "call_model", "one corrective turn, not the sandbox"
    assert cmd.update["script"] is None
    assert cmd.update["chat_messages"][-1] == HumanMessage(content=FC_MODE_VIOLATION_CORRECTION)
    assert cmd.update["cuga_lite_metadata"]["fc_mode_violations"] == 1
    assert cmd.update["step_count"] == 1, "charged as a step so it cannot loop forever"


@pytest.mark.asyncio
async def test_budget_exhausted_turn_ignores_tool_calls_and_sends_the_instruction_outbound_only():
    model = _Model(AIMessage(content="From what I have: 3.", tool_calls=[_CALL]))
    state = _state()

    cmd = await _turn(_adapter(), model, state, FC, budget_exhausted=True)

    assert cmd.goto == END and cmd.update["final_answer"] == "From what I have: 3."
    (outbound,) = model.seen
    assert outbound[-1] == HumanMessage(content=TOOL_BUDGET_EXHAUSTED_INSTRUCTION)
    assert all(m.content != TOOL_BUDGET_EXHAUSTED_INSTRUCTION for m in cmd.update["chat_messages"]), (
        "never persisted"
    )


@pytest.mark.asyncio
async def test_few_shot_demos_go_out_as_chat_messages_before_history():
    model = _Model(AIMessage(content="ok"))
    few = [{"role": "user", "content": "demo question"}, {"role": "assistant", "content": "demo answer"}]

    await _turn(_adapter(), model, _state(few_shots=few), FC)

    (outbound,) = model.seen
    assert outbound[1] == HumanMessage(content="demo question")
    assert outbound[2] == AIMessage(content="demo answer")
    assert outbound[3].content == "what is 1+2?"


def test_replay_sanitizer_drops_reasoning_and_strips_harmony_without_touching_history(monkeypatch):
    monkeypatch.setattr(ga, "strip_harmony_tokens", lambda s: s.replace("<|channel|>final", ""))
    kept = HumanMessage(content="plain")
    noisy = AIMessage(
        content="<|channel|>finalanswer", additional_kwargs={"reasoning_content": "thinking...", "x": 1}
    )

    out = _sanitize_for_replay([kept, noisy])

    assert out[0] is kept, "untouched messages are passed through by identity"
    assert out[1].content == "answer" and out[1].additional_kwargs == {"x": 1}
    assert noisy.additional_kwargs["reasoning_content"] == "thinking...", "persisted history is never mutated"


@pytest.mark.asyncio
async def test_bind_mode_is_upgraded_from_none_only_in_function_calling_mode():
    captured = []

    async def fake_resolve(active_model, **kwargs):
        captured.append(kwargs["configurable"])
        return "BOUND"

    with patch.object(ga, "resolve_model_with_bind_tools", new=AsyncMock(side_effect=fake_resolve)):
        adapter = _adapter()
        assert await adapter.resolve_bind_tools(_state(), object(), dict(FC), None) == "BOUND"
        await adapter.resolve_bind_tools(
            _state(), object(), {**FC, "cuga_lite_bind_tools_mode": "find_tools"}, None
        )
        await adapter.resolve_bind_tools(_state(), object(), {}, None)

    fc_default, fc_explicit, codeact = captured
    assert fc_default["cuga_lite_bind_tools_mode"] == "all", "FC is inert without advertised tools"
    assert fc_explicit["cuga_lite_bind_tools_mode"] == "find_tools", "an explicit choice is respected"
    assert "cuga_lite_bind_tools_mode" not in codeact, "codeact is byte-identical"
