"""Function-calling mode across two SDK turns on one thread.

The SDK wrapper hands state between the CugaLite subgraph and the outer
``AgentState`` graph through ``state.model_dump()``. ``AgentState.chat_messages``
is declared ``List[BaseMessage]``, so pydantic serialises every message by the
*declared* type: ``AIMessage.tool_calls`` and ``ToolMessage.tool_call_id`` are
dropped and revalidation yields bare ``BaseMessage`` objects. CodeAct never
notices (its serializer keys on ``msg.type``); function-calling replays history
as message objects, so turn 2 must still send the provider a valid transcript.
"""

from __future__ import annotations


import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool

pytestmark = pytest.mark.unit

CALLS: list = []


class _ScriptedModel:
    def __init__(self, responses):
        self._responses = list(responses)
        self.seen: list = []

    def bind_tools(self, tools, **kwargs):
        return self

    async def ainvoke(self, messages, config=None, **kwargs):
        self.seen.append(list(messages))
        if not self._responses:
            raise AssertionError("model asked for more responses than scripted")
        return self._responses.pop(0)


def _echo():
    async def echo(value: int) -> int:
        """Echo a value."""
        CALLS.append(value)
        return value

    return StructuredTool.from_function(coroutine=echo, name="echo", description="Echo a value.")


def _tc(value, call_id):
    return {"name": "echo", "args": {"value": value}, "id": call_id, "type": "tool_call"}


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    from cuga.config import settings

    monkeypatch.setattr(settings.policy, "enabled", False, raising=False)
    CALLS.clear()
    yield
    CALLS.clear()


def _describe(m):
    kind = type(m).__name__
    extra = ""
    if getattr(m, "tool_calls", None):
        extra = f" tool_calls={[c['id'] for c in m.tool_calls]}"
    if getattr(m, "tool_call_id", None):
        extra = f" tool_call_id={m.tool_call_id}"
    return f"{kind}({getattr(m, 'type', '?')}){extra}"


@pytest.mark.asyncio
async def test_second_turn_replays_a_valid_function_calling_transcript():
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

    turn1 = await agent.invoke("echo 7", thread_id="fc-two-turn")
    assert turn1.answer == "The value is 7." and CALLS == [7]

    turn2 = await agent.invoke("now echo 8", thread_id="fc-two-turn")
    assert turn2.answer == "And now 8." and CALLS == [7, 8]

    outbound = model.seen[2]  # first model call of turn 2
    shapes = [_describe(m) for m in outbound]
    print("\nTURN 2 outbound:", shapes)

    assert isinstance(outbound[0], SystemMessage)
    assert all(type(m) is not BaseMessage for m in outbound), (
        f"bare BaseMessage replayed to the provider: {shapes}"
    )
    ai_with_calls = [m for m in outbound if isinstance(m, AIMessage) and m.tool_calls]
    tool_replies = [m for m in outbound if isinstance(m, ToolMessage)]
    dangling = {c["id"] for m in ai_with_calls for c in m.tool_calls} - {t.tool_call_id for t in tool_replies}
    assert not dangling, f"tool_calls without a ToolMessage reply: {dangling} in {shapes}"
    assert isinstance(outbound[-1], HumanMessage) and outbound[-1].content == "now echo 8"
