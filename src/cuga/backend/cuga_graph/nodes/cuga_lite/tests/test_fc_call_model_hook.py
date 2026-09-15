"""CugaLite's ``execute_call_model_fc``: the function-calling turn.

Codeact: ``None``, nothing invoked. Function-calling: the bound model gets real
message objects (system prompt, few-shot demos, history normalised into a
provider-valid transcript), and the response routes on ``tool_calls`` — to
``tool_exec`` with the assistant turn kept verbatim, or to END as the final
answer. Every id the model issued is answered before the run can end, an empty
reply gets one retry, a fenced code block is a mode violation (never executed),
and the mode refuses to start while a tool-approval policy is configured.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END

from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.graph_nodes import (
    EMPTY_RESPONSE_CORRECTION,
    EMPTY_RESPONSE_CORRECTION_KEY,
)
from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_nodes import TOOL_BUDGET_EXHAUSTED_INSTRUCTION
from cuga.backend.cuga_graph.nodes.cuga_lite.adapter import graph_adapter as ga
from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.graph_adapter import (
    FC_BUDGET_CALL_REPLY,
    FC_MODE_VIOLATION_CORRECTION,
    FC_STEP_LIMIT_CALL_REPLY,
    FC_TOOL_APPROVAL_UNSUPPORTED,
    FC_TOOL_APPROVAL_UNVERIFIED,
    FC_UNANSWERED_CALL_REPLY,
    AgentGraphAdapter,
    _normalize_history_for_replay,
)

pytestmark = pytest.mark.unit

FC = {"cuga_lite_execution_mode": "function_calling"}
_CALL = {"name": "add", "args": {"a": 1, "b": 2}, "id": "c1", "type": "tool_call"}


class _Model:
    """Bare scripted model: no MagicMock attribute magic, so helper probes see a plain object."""

    def __init__(self, response: Any):
        self.response = response
        self.seen: List[list] = []

    async def ainvoke(self, messages, config=None, **kwargs):
        self.seen.append(list(messages))
        return self.response


def _adapter(tools_context_ref=None) -> AgentGraphAdapter:
    return AgentGraphAdapter(
        tracker=MagicMock(),
        base_callbacks=[],
        task_todos_ref=[],
        tools_context_ref=tools_context_ref if tools_context_ref is not None else {},
        base_tool_provider=None,
    )


def _state(messages=None, few_shots=None, step_count=0, max_steps=None, metadata=None):
    return SimpleNamespace(
        chat_messages=messages or [HumanMessage(content="what is 1+2?")],
        step_count=step_count,
        cuga_lite_metadata=metadata if metadata is not None else {},
        mcp_few_shot_messages=few_shots or [],
        cuga_lite_max_steps=max_steps,
    )


async def _turn(
    adapter, model, state, configurable, *, budget_exhausted=False, system="SYS", variables_addendum=""
):
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
        variables_addendum=variables_addendum,
    )


@pytest.fixture(autouse=True)
def _policy_off(monkeypatch):
    from cuga.config import settings

    monkeypatch.setattr(settings.policy, "enabled", False, raising=False)


# ── routing ──────────────────────────────────────────────────────────────────


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
async def test_empty_answer_after_a_retry_falls_back_to_the_last_tool_result():
    history = [
        HumanMessage(content="q"),
        AIMessage(content="", tool_calls=[_CALL]),
        ToolMessage(content="3", tool_call_id="c1", name="add"),
    ]
    state = _state(history, metadata={EMPTY_RESPONSE_CORRECTION_KEY: True})
    cmd = await _turn(_adapter(), _Model(AIMessage(content="")), state, FC)

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
    assert state.cuga_lite_metadata == {}, "metadata travels in the update, state is not mutated in place"


@pytest.mark.asyncio
async def test_empty_reply_is_retried_once_then_finalized():
    cmd = await _turn(_adapter(), _Model(AIMessage(content="")), _state(), FC)

    assert cmd.goto == "call_model"
    assert cmd.update["chat_messages"][-1] == HumanMessage(content=EMPTY_RESPONSE_CORRECTION)
    assert cmd.update["cuga_lite_metadata"][EMPTY_RESPONSE_CORRECTION_KEY] is True

    retried = _state(metadata={EMPTY_RESPONSE_CORRECTION_KEY: True})
    cmd2 = await _turn(_adapter(), _Model(AIMessage(content="")), retried, FC)
    assert cmd2.goto == END, "the marker survives exactly one turn — no retry loop"


# ── every issued id is answered before the run can end ───────────────────────


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
    ai, reply = cmd.update["chat_messages"][-2:]
    assert ai.tool_calls == [_CALL] and isinstance(reply, ToolMessage)
    assert (reply.tool_call_id, reply.content) == ("c1", FC_BUDGET_CALL_REPLY), (
        "no dangling call is persisted"
    )


@pytest.mark.asyncio
async def test_step_limit_breach_answers_pending_calls_before_the_error():
    """The persisted transcript must never end on a tool_calls turn nobody answered —
    the next replay of the thread would be a provider 400."""
    model = _Model(AIMessage(content="", tool_calls=[_CALL]))
    state = _state(step_count=1, max_steps=1)

    cmd = await _turn(_adapter(), model, state, FC)

    assert cmd.goto == END and "Maximum step limit" in cmd.update["final_answer"]
    ai, reply, error = cmd.update["chat_messages"][-3:]
    assert ai.tool_calls == [_CALL]
    assert isinstance(reply, ToolMessage) and (reply.tool_call_id, reply.content) == (
        "c1",
        FC_STEP_LIMIT_CALL_REPLY,
    )
    assert isinstance(error, AIMessage) and "Maximum step limit" in error.content


# ── what the model is sent ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_few_shot_demos_go_out_as_chat_messages_before_history():
    model = _Model(AIMessage(content="ok"))
    few = [{"role": "user", "content": "demo question"}, {"role": "assistant", "content": "demo answer"}]

    await _turn(_adapter(), model, _state(few_shots=few), FC)

    (outbound,) = model.seen
    assert outbound[1] == HumanMessage(content="demo question")
    assert outbound[2] == AIMessage(content="demo answer")
    assert outbound[3].content == "what is 1+2?"


@pytest.mark.asyncio
async def test_variables_addendum_rides_the_last_human_turn_outbound_only():
    model = _Model(AIMessage(content="ok"))
    state = _state([HumanMessage(content="first"), AIMessage(content="sure"), HumanMessage(content="last")])
    addendum = "\n\n## Available Variables\n\nvar_1: ..."

    cmd = await _turn(_adapter(), model, state, FC, variables_addendum=addendum)

    (outbound,) = model.seen
    humans = [m for m in outbound if isinstance(m, HumanMessage)]
    assert humans[-1].content == "last" + addendum and humans[0].content == "first"
    assert all(addendum not in m.content for m in cmd.update["chat_messages"]), "never persisted (#600)"


def test_replay_sanitizer_drops_reasoning_and_strips_harmony_without_touching_history(monkeypatch):
    monkeypatch.setattr(ga, "strip_harmony_tokens", lambda s: s.replace("<|channel|>final", ""))
    kept = HumanMessage(content="plain")
    noisy = AIMessage(
        content="<|channel|>finalanswer", additional_kwargs={"reasoning_content": "thinking...", "x": 1}
    )

    out = _normalize_history_for_replay([kept, noisy])

    assert out[0] is kept, "untouched messages are passed through by identity"
    assert out[1].content == "answer" and out[1].additional_kwargs == {"x": 1}
    assert noisy.additional_kwargs["reasoning_content"] == "thinking...", "persisted history is never mutated"


def test_replay_rebuilds_bare_shells_and_closes_dangling_calls():
    """History that crossed the SDK boundary (bare BaseMessage shells, subclass fields
    gone) and a turn that ended on an unanswered call both come back provider-valid."""
    shells = [
        BaseMessage(type="human", content="echo 7"),
        BaseMessage(type="ai", content=""),  # was AIMessage(tool_calls=[...]) before model_dump
        BaseMessage(type="tool", content="7", name="echo"),  # was ToolMessage(tool_call_id="call_1")
        BaseMessage(type="ai", content="The value is 7."),
        HumanMessage(content="again"),
        AIMessage(content="", tool_calls=[_CALL]),  # never answered: step limit / crash
        ToolMessage(content="orphan", tool_call_id="nope", name="add"),  # answers nothing
    ]

    out = _normalize_history_for_replay(shells)

    assert [type(m).__name__ for m in out] == [
        "HumanMessage",
        "HumanMessage",
        "AIMessage",
        "HumanMessage",
        "AIMessage",
        "ToolMessage",
        "HumanMessage",
    ], [type(m).__name__ for m in out]
    assert out[1].content == "Tool result (echo):\n7", "a lost tool result is replayed as text"
    assert out[4].tool_calls == [_CALL] and (out[5].tool_call_id, out[5].content) == (
        "c1",
        FC_UNANSWERED_CALL_REPLY,
    )
    assert out[6].content == "Tool result (add):\norphan"
    assert all(type(m) is not BaseMessage for m in out), "no bare shell reaches the provider"


# ── guards and binding ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refuses_to_start_when_a_tool_approval_policy_exists(monkeypatch):
    """No approval interrupt exists on this path yet: fail closed, before any model call."""
    from cuga.config import settings

    monkeypatch.setattr(settings.policy, "enabled", True, raising=False)
    model = _Model(AIMessage(content="", tool_calls=[_CALL]))

    with patch.object(AgentGraphAdapter, "_tool_approval_policies_exist", new=AsyncMock(return_value=True)):
        cmd = await _turn(_adapter(), model, _state(), FC)
    assert cmd.goto == END and cmd.update["error"] == FC_TOOL_APPROVAL_UNSUPPORTED
    assert model.seen == [], "refused before the model was invoked"

    with patch.object(AgentGraphAdapter, "_tool_approval_policies_exist", new=AsyncMock(return_value=False)):
        cmd = await _turn(_adapter(), model, _state(), FC)
    assert cmd.goto == "tool_exec" and len(model.seen) == 1, "policies without approval rules do not block"


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


@pytest.mark.asyncio
async def test_bind_advertises_the_executable_set_prepare_recorded():
    captured = []

    async def fake_resolve(active_model, **kwargs):
        captured.append(kwargs["configurable"])
        return "BOUND"

    with patch.object(ga, "resolve_model_with_bind_tools", new=AsyncMock(side_effect=fake_resolve)):
        scoped = _adapter(tools_context_ref={"_lc_bind_tools_executable_names": ["echo", "add"]})
        await scoped.resolve_bind_tools(_state(), object(), dict(FC), None)

    (cfg,) = captured
    assert cfg["cuga_lite_bind_tools_mode"] == "tools"
    assert cfg["cuga_lite_bind_tools_tool_names"] == ["echo", "add"], (
        "bind what the sandbox could call, not the catalogue"
    )


@pytest.mark.asyncio
async def test_refuses_when_the_policy_query_fails(monkeypatch):
    """Fail closed on infrastructure failure too: 'could not verify' is not 'no policies'."""
    from cuga.config import settings

    monkeypatch.setattr(settings.policy, "enabled", True, raising=False)
    model = _Model(AIMessage(content="", tool_calls=[_CALL]))

    with patch.object(
        AgentGraphAdapter,
        "_tool_approval_policies_exist",
        new=AsyncMock(side_effect=RuntimeError("backend down")),
    ):
        cmd = await _turn(_adapter(), model, _state(), FC)

    assert cmd.goto == END and model.seen == []
    assert cmd.update["error"] == FC_TOOL_APPROVAL_UNVERIFIED.format(error="backend down")


@pytest.mark.asyncio
async def test_guard_asks_storage_strictly_so_a_backend_failure_is_not_an_empty_list():
    """PolicyStorage.list_policies swallows backend errors by default; the guard must not."""
    from types import SimpleNamespace

    calls = []

    async def list_policies(**kwargs):
        calls.append(kwargs)
        return []

    fake_system = SimpleNamespace(agent=SimpleNamespace(storage=SimpleNamespace(list_policies=list_policies)))
    with patch(
        "cuga.backend.cuga_graph.policy.configurable.PolicyConfigurable.from_config", return_value=fake_system
    ):
        assert await _adapter()._tool_approval_policies_exist({}) is False
    assert calls and calls[0]["strict"] is True
