"""Function-calling transcripts across checkpoints, modes and the supervisor (#976).

#777 changed how ``AgentState`` serialises messages (by runtime type, revived on the
way in), and function-calling replays history as native message objects. These
tests cross the boundaries a unit test cannot: a real checkpoint whose tool turns
lost their fields, a thread that switches modes between turns, and a supervisor
that delegates twice to a function-calling sub-agent, whose second delegation
resumes the sub-agent's own checkpoint. Each one checks what the model is sent.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool

pytestmark = pytest.mark.unit

CALLS: list = []


class _Bound:
    def __init__(self, model):
        self._model = model

    async def ainvoke(self, messages, config=None, **kwargs):
        return self._model.answer(messages)


class _ScriptedModel:
    def __init__(self, responses):
        self._responses = list(responses)
        self.seen: list = []

    def bind_tools(self, tools, **kwargs):
        return _Bound(self)

    async def ainvoke(self, messages, config=None, **kwargs):
        return self.answer(messages)

    def answer(self, messages):
        self.seen.append(list(messages))
        if not self._responses:
            raise AssertionError(f"model call #{len(self.seen)} was not scripted")
        reply = self._responses.pop(0)
        return reply if isinstance(reply, AIMessage) else AIMessage(content=reply)


def _echo():
    async def echo(value: int) -> int:
        """Echo a value."""
        CALLS.append(value)
        return value

    return StructuredTool.from_function(coroutine=echo, name="echo", description="Echo a value.")


def _tc(value, call_id):
    return {"name": "echo", "args": {"value": value}, "id": call_id, "type": "tool_call"}


def _text(message) -> str:
    content = message["content"] if isinstance(message, dict) else message.content
    return content if isinstance(content, str) else str(content)


def _assert_valid_fc_replay(outbound: list) -> None:
    """What a strict provider requires of a native tool-calling transcript."""
    shapes = [f"{type(m).__name__}:{getattr(m, 'tool_call_id', '') or ''}" for m in outbound]
    assert isinstance(outbound[0], SystemMessage), shapes
    assert all(type(m) is not BaseMessage for m in outbound), f"bare BaseMessage sent: {shapes}"
    open_ids: set = set()
    for m in outbound:
        if isinstance(m, ToolMessage):
            assert m.tool_call_id in open_ids, f"reply to no open call: {m.tool_call_id} in {shapes}"
            open_ids.discard(m.tool_call_id)
            continue
        assert not open_ids, f"calls left unanswered before the next message: {open_ids} in {shapes}"
        if isinstance(m, AIMessage):
            assert m.tool_calls or _text(m).strip(), f"empty assistant turn sent: {shapes}"
            open_ids = {c["id"] for c in m.tool_calls}
    assert not open_ids, f"transcript ends on unanswered calls: {open_ids}"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    from cuga.config import settings

    monkeypatch.chdir(tmp_path)  # SDK policy sync writes ./.cuga
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-used")  # graph build constructs a default client
    monkeypatch.setattr(settings.policy, "enabled", False, raising=False)
    monkeypatch.setattr(settings.evolve, "enabled", False, raising=False)
    CALLS.clear()
    yield
    CALLS.clear()


@pytest.mark.asyncio
async def test_tool_turns_saved_without_their_fields_still_replay_as_a_valid_fc_transcript():
    """A message list dumped by its declared ``BaseMessage`` type, as ``AgentState``'s
    was before #777, keeps only the base fields: the tool-call turn loses its
    ``tool_calls`` and the reply its ``tool_call_id``. A thread saved like that must
    still replay as a transcript a provider accepts."""
    from cuga.sdk import CugaAgent

    model = _ScriptedModel(
        [
            AIMessage(content="", tool_calls=[_tc(7, "call_1")]),
            AIMessage(content="The value is 7."),
            AIMessage(content="", tool_calls=[_tc(8, "call_2")]),
            AIMessage(content="And now 8."),
        ]
    )
    agent = CugaAgent(tools=[_echo()], model=model, execution_mode="function_calling")
    config = {"configurable": {"thread_id": "lossy"}}
    await agent.invoke("echo 7", thread_id="lossy")

    saved = (await agent.graph.aget_state(config)).values["chat_messages"]
    # The checkpoint holds the dicts nodes returned from model_dump(). Before #777 those
    # carried only BaseMessage's own fields; rewrite the saved thread into that shape.
    base_fields = set(BaseMessage.model_fields)
    legacy = [
        {k: v for k, v in (m if isinstance(m, dict) else m.model_dump()).items() if k in base_fields}
        for m in saved
    ]
    assert [m["type"] for m in legacy] == ["human", "ai", "tool", "ai"]
    assert "tool_calls" not in legacy[1] and "tool_call_id" not in legacy[2], "the fields are gone"
    await agent.graph.aupdate_state(config, {"chat_messages": legacy})

    turn2 = await agent.invoke("now echo 8", thread_id="lossy")

    assert turn2.answer == "And now 8." and CALLS == [7, 8]
    replay = model.seen[2]
    _assert_valid_fc_replay(replay)
    texts = [_text(m) for m in replay if isinstance(m, HumanMessage)]
    assert any(t.startswith("Tool result (echo):\n7") for t in texts), "the lost reply is kept as text"
    assert any(isinstance(m, AIMessage) and _text(m) == "The value is 7." for m in replay)


@pytest.mark.asyncio
async def test_a_thread_switches_fc_codeact_fc_and_each_turn_sees_the_earlier_ones():
    from cuga.sdk import CugaAgent

    model = _ScriptedModel(
        [
            AIMessage(content="", tool_calls=[_tc(7, "c1")]),
            AIMessage(content="The value is 7."),
            "```python\nprint('plus one:', tool_result_c1 + 1)\n```",
            "It is 8.",
            AIMessage(content="", tool_calls=[_tc(9, "c3")]),
            AIMessage(content="And 9."),
        ]
    )
    agent = CugaAgent(tools=[_echo()], model=model, execution_mode="function_calling")

    await agent.invoke("echo 7", thread_id="switch")
    turn2 = await agent.invoke("add one to it in code", thread_id="switch", execution_mode="codeact")
    turn3 = await agent.invoke("echo 9", thread_id="switch")

    assert CALLS == [7, 9] and turn2.answer == "It is 8." and turn3.answer == "And 9."
    # Turn 2 (CodeAct) is sent dict turns: the native reply as text, the result as a variable.
    codeact = model.seen[2]
    assert all(isinstance(m, dict) for m in codeact)
    assert {"role": "user", "content": "Tool result (echo):\n7"} in codeact
    assert "plus one: 8" in _text(model.seen[3][-1]), "the CodeAct block read tool_result_c1"
    # Turn 3 (function calling) replays all of it as a valid native transcript.
    replay = model.seen[4]
    _assert_valid_fc_replay(replay)
    assert [c["id"] for m in replay if isinstance(m, AIMessage) for c in m.tool_calls] == ["c1"]
    assert any(isinstance(m, AIMessage) and "tool_result_c1 + 1" in _text(m) for m in replay)


@pytest.mark.asyncio
async def test_supervisor_delegates_twice_to_an_fc_sub_agent_on_one_conversation():
    """A sub-agent keeps its own checkpoint per parent conversation
    (``sub_agent_memory_scope = "conversation"``), so the second delegation resumes
    the sub-agent's function-calling transcript. (``CugaSupervisor.invoke`` starts
    each turn with only the new message in ``supervisor_chat_messages``; the
    supervisor's own history across turns is covered on the server path, in
    ``cuga_supervisor/tests/test_supervisor_history_checkpoint.py``.)"""
    from cuga.sdk import CugaAgent, CugaSupervisor

    sub_model = _ScriptedModel(
        [
            AIMessage(content="", tool_calls=[_tc(7, "s1")]),
            AIMessage(content="The value is 7."),
            AIMessage(content="", tool_calls=[_tc(8, "s2")]),
            AIMessage(content="The value is 8."),
        ]
    )
    sub = CugaAgent(tools=[_echo()], model=sub_model, execution_mode="function_calling")
    sub.memory_scope = "conversation"  # the shipped default, pinned against env overrides
    sup_model = _ScriptedModel(
        [
            "```python\nr = await delegate_to_echoer(task='echo 7')\nprint(r)\n```",
            "The echoer said 7.",
            "```python\nr = await delegate_to_echoer(task='echo 8')\nprint(r)\n```",
            "The echoer said 8.",
        ]
    )
    supervisor = CugaSupervisor(agents={"echoer": sub}, model=sup_model, auto_load_policies=False)

    turn1 = await supervisor.invoke("ask the echoer for 7", thread_id="sup-fc")
    turn2 = await supervisor.invoke("now ask it for 8", thread_id="sup-fc")

    assert CALLS == [7, 8]
    assert turn1.answer == "The echoer said 7." and turn2.answer == "The echoer said 8."
    assert "The value is 7." in _text(sup_model.seen[1][-1]), "the sub-agent's answer reached the supervisor"
    replay = sub_model.seen[2]  # first model call of the second delegation
    _assert_valid_fc_replay(replay)
    assert [c["id"] for m in replay if isinstance(m, AIMessage) for c in m.tool_calls] == ["s1"]
    replies = {m.tool_call_id: _text(m) for m in replay if isinstance(m, ToolMessage)}
    assert replies == {"s1": "7"}, "the first delegation's call and its reply came back from the checkpoint"
