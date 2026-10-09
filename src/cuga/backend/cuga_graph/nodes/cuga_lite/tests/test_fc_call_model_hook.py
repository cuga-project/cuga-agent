"""CugaLite's ``execute_call_model_fc``: the function-calling turn.

Codeact: ``None``, nothing invoked. Function-calling: the bound model gets real
message objects (system prompt, few-shot demos, history normalised into a
provider-valid transcript), and the response routes on ``tool_calls`` — to the
sandbox as a translated block with the assistant turn kept verbatim, or to END
as the final answer. Every id the model issued is answered before the run can
end, an empty reply gets one retry, a fenced code block is a mode violation
(never executed), and a matching tool-approval policy interrupts on the block
exactly as it does for CodeAct code.
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
from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.fc_actions import FC_PENDING_KEY
from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.graph_adapter import (
    FC_BIND_FAILED,
    FC_BUDGET_CALL_REPLY,
    FC_MODE_VIOLATION_CORRECTION,
    FC_STEP_LIMIT_CALL_REPLY,
    FC_UNANSWERED_CALL_REPLY,
    AgentGraphAdapter,
    _looks_like_python_block,
    _normalize_history_for_replay,
)

pytestmark = pytest.mark.unit

FC = {"cuga_lite_execution_mode": "function_calling"}
_UNBOUND = object()
_CALL = {"name": "add", "args": {"a": 1, "b": 2}, "id": "c1", "type": "tool_call"}


class _Model:
    """Bare scripted model: no MagicMock attribute magic, so helper probes see a plain object."""

    def __init__(self, response: Any):
        self.response = response
        self.seen: List[list] = []

    async def ainvoke(self, messages, config=None, **kwargs):
        self.seen.append(list(messages))
        return self.response


async def _add(a: int, b: int) -> int:
    return a + b


def _adapter(tools_context_ref=None, tools=None) -> AgentGraphAdapter:
    adapter = AgentGraphAdapter(
        tracker=MagicMock(),
        base_callbacks=[],
        task_todos_ref=[],
        tools_context_ref=tools_context_ref if tools_context_ref is not None else {},
        base_tool_provider=None,
    )
    adapter._tools_context.update(tools if tools is not None else {"add": _add})
    return adapter


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
        active_model=_UNBOUND,  # what call_model passes when bind_tools succeeded: bound is not the model
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
async def test_tool_calls_route_to_the_sandbox_as_a_block_with_the_assistant_turn_verbatim():
    response = AIMessage(content="", tool_calls=[_CALL])
    model = _Model(response)
    state = _state()

    cmd = await _turn(_adapter(), model, state, FC)

    assert cmd.goto == "sandbox"
    persisted = cmd.update["chat_messages"]
    assert persisted[:-1] == state.chat_messages
    assert persisted[-1] is response, "the assistant turn is persisted untouched, ids included"
    assert 'await add(**{"a": 1, "b": 2})' in cmd.update["script"], cmd.update["script"]
    assert cmd.update["step_count"] == 1
    (entry,) = cmd.update["cuga_lite_metadata"][FC_PENDING_KEY]
    assert entry["id"] == "c1" and entry["var"] == "tool_result_c1" and entry["reply"] is None

    (outbound,) = model.seen
    assert isinstance(outbound[0], SystemMessage) and outbound[0].content == "SYS"
    assert outbound[1:] == state.chat_messages, "history goes out as message objects, not the CodeAct dicts"


@pytest.mark.asyncio
async def test_calls_nothing_can_execute_are_answered_without_the_sandbox():
    """Unknown tool, bad arguments, provider-rejected: every id is answered here, and the
    execute step the sandbox would have taken is still charged."""
    unknown = {"name": "nope", "args": {}, "id": "c9", "type": "tool_call"}
    cmd = await _turn(_adapter(), _Model(AIMessage(content="", tool_calls=[unknown])), _state(), FC)

    assert cmd.goto == "call_model" and cmd.update["script"] is None and cmd.update["step_count"] == 2
    reply = cmd.update["chat_messages"][-1]
    assert isinstance(reply, ToolMessage) and reply.tool_call_id == "c9" and reply.status == "error"
    assert reply.content == "Unknown tool 'nope'. Choose one of the provided tools: add."


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


def _policy_system(match):
    return SimpleNamespace(agent=SimpleNamespace(check_tool_approval_for_code=AsyncMock(return_value=match)))


def _approval_match():
    policy = SimpleNamespace(
        id="p1",
        name="Approve add",
        required_tools=["add"],
        required_apps=[],
        approval_message="Adding needs approval.",
        show_code_preview=True,
    )
    return SimpleNamespace(
        matched=True, policy=policy, confidence=1.0, reasoning="r", trigger_details={"matched_tools": ["add"]}
    )


@pytest.mark.asyncio
async def test_a_matching_tool_approval_policy_interrupts_before_the_sandbox(monkeypatch):
    """The CodeAct approval check runs on the translated block, so a policy on the tool
    name interrupts; the assistant turn is persisted with its tool_calls and the plan
    rides the metadata, so the approved block resumes into the sandbox."""
    from cuga.config import settings

    monkeypatch.setattr(settings.policy, "enabled", True, raising=False)
    response = AIMessage(content="", tool_calls=[_CALL])
    model = _Model(response)
    system = _policy_system(_approval_match())

    with (
        patch(
            "cuga.backend.cuga_graph.policy.configurable.PolicyConfigurable.from_config", return_value=system
        ),
        patch(
            "cuga.backend.cuga_graph.policy.configurable.PolicyConfigurable.create_context_from_state",
            return_value=SimpleNamespace(user_input="q"),
        ),
    ):
        cmd = await _turn(_adapter(), model, _state(), FC)

    assert cmd.goto == END and cmd.update["hitl_action"] is not None
    assert "Adding needs approval." in cmd.update["final_answer"]
    assert 'await add(**{"a": 1, "b": 2})' in cmd.update["script"], "the approved block is what resumes"
    assert cmd.update["chat_messages"][-1] is response, "tool_calls survive the pause"
    meta = cmd.update["cuga_lite_metadata"]
    assert meta["approval_required"] is True and meta[FC_PENDING_KEY][0]["id"] == "c1"
    (code, _context), _ = system.agent.check_tool_approval_for_code.call_args
    assert "add(" in code


@pytest.mark.asyncio
async def test_no_matching_policy_routes_to_the_sandbox(monkeypatch):
    from cuga.config import settings

    monkeypatch.setattr(settings.policy, "enabled", True, raising=False)
    system = _policy_system(SimpleNamespace(matched=False))

    with (
        patch(
            "cuga.backend.cuga_graph.policy.configurable.PolicyConfigurable.from_config", return_value=system
        ),
        patch(
            "cuga.backend.cuga_graph.policy.configurable.PolicyConfigurable.create_context_from_state",
            return_value=SimpleNamespace(user_input="q"),
        ),
    ):
        cmd = await _turn(_adapter(), _Model(AIMessage(content="", tool_calls=[_CALL])), _state(), FC)

    assert cmd.goto == "sandbox" and FC_PENDING_KEY in cmd.update["cuga_lite_metadata"]


@pytest.mark.asyncio
async def test_bind_mode_is_upgraded_from_none_only_in_function_calling_mode():
    captured = []

    async def fake_resolve(active_model, **kwargs):
        captured.append(kwargs["configurable"])
        return "BOUND"

    with patch.object(ga, "resolve_model_with_bind_tools", new=AsyncMock(side_effect=fake_resolve)):
        adapter = _adapter()
        # No executable set recorded: bind nothing (the turn then fails closed) rather
        # than open the registry-wide catalogue to tools the sandbox cannot run.
        assert await adapter.resolve_bind_tools(_state(), object(), dict(FC), None) is None
        await adapter.resolve_bind_tools(
            _state(), object(), {**FC, "cuga_lite_bind_tools_mode": "find_tools"}, None
        )
        await adapter.resolve_bind_tools(_state(), object(), {}, None)

    fc_explicit, codeact = captured
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
async def test_refuses_when_no_tools_are_bound():
    """bind_tools failed / unsupported / nothing to bind: call_model hands the unbound model
    through. Native tool calling is impossible, so stop — do not degrade to text."""
    model = _Model(AIMessage(content="", tool_calls=[_CALL]))
    cmd = await _adapter().execute_call_model_fc(
        state=_state(),
        config=None,
        configurable=FC,
        active_model=model,
        bound=model,  # `resolve_bind_tools(...) or active_model` fell back
        invoke_config={},
        system_content="SYS",
        modified_messages=[HumanMessage(content="q")],
        budget_exhausted=False,
        playbook_fired=False,
    )
    assert cmd.goto == END and cmd.update["error"] == FC_BIND_FAILED and model.seen == []


@pytest.mark.asyncio
async def test_budget_exhausted_grace_turn_runs_unbound_on_purpose():
    """The grace turn deliberately withholds tools, so an unbound model is not a failure there."""
    model = _Model(AIMessage(content="From what I have: 3."))
    cmd = await _adapter().execute_call_model_fc(
        state=_state(),
        config=None,
        configurable=FC,
        active_model=model,
        bound=model,
        invoke_config={},
        system_content="SYS",
        modified_messages=[HumanMessage(content="q")],
        budget_exhausted=True,
        playbook_fired=False,
    )
    assert cmd.goto == END and cmd.update["final_answer"] == "From what I have: 3."


@pytest.mark.asyncio
async def test_mode_violation_is_corrected_once_then_the_fence_is_delivered():
    fence = "```python\nawait add(a=1, b=2)\n```"
    first = await _turn(_adapter(), _Model(AIMessage(content=fence)), _state(), FC)
    assert first.goto == "call_model" and first.update["cuga_lite_metadata"]["fc_mode_violations"] == 1

    again = _state(metadata={"fc_mode_violations": 1})
    second = await _turn(_adapter(), _Model(AIMessage(content=fence)), again, FC)
    assert second.goto == END, "one correction only — never a loop to cuga_lite_max_steps"
    assert second.update["final_answer"] == fence


@pytest.mark.asyncio
async def test_non_python_fences_in_an_answer_are_not_violations():
    answer = "Here is the record:\n```json\n{\"id\": 42}\n```"
    cmd = await _turn(_adapter(), _Model(AIMessage(content=answer)), _state(), FC)
    assert cmd.goto == END and cmd.update["final_answer"] == answer


def test_untagged_fences_are_violations_only_when_they_run_something():
    assert _looks_like_python_block("```python\nx = 1\n```")
    assert _looks_like_python_block("```\nawait add(a=1, b=2)\n```")
    assert _looks_like_python_block("```\nadd(a=1, b=2)\n```", {"add"}), "a bound tool called by name"
    assert not _looks_like_python_block("The function is\n```\nf(x) = 2x + 1\n```", {"add"}), (
        "notation, not code"
    )
    assert not _looks_like_python_block("```json\n{\"a\": 1}\n```", {"add"})


@pytest.mark.asyncio
async def test_watsonx_reasoning_key_is_read_like_the_codeact_path():
    """#796: WatsonX reports the reasoning channel as ``reasoning``, not ``reasoning_content``.
    The FC turn uses it for the empty-content fallback, so it must read both spellings."""
    response = AIMessage(content="", additional_kwargs={"reasoning": "The total is 42."})
    state = _state(metadata={EMPTY_RESPONSE_CORRECTION_KEY: True})  # past the empty-reply retry

    cmd = await _turn(_adapter(), _Model(response), state, FC)

    assert cmd.goto == END and cmd.update["final_answer"] == "The total is 42."
