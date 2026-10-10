"""The function-calling one-correction cap is per turn, not per thread.

``fc_mode_violations`` lives in ``cuga_lite_metadata``, which the SDK carries into
the next turn on the same thread. Without a per-turn reset, one correction anywhere
in a thread made every later turn's first Python fence the delivered answer.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool

pytestmark = pytest.mark.unit


class _Bound:
    def __init__(self, model):
        self._model = model

    async def ainvoke(self, messages, config=None, **kwargs):
        return await self._model.ainvoke(messages, config=config, **kwargs)


class _ScriptedModel:
    def __init__(self, responses):
        self._responses = list(responses)

    def bind_tools(self, tools, **kwargs):
        return _Bound(self)

    async def ainvoke(self, messages, config=None, **kwargs):
        if not self._responses:
            raise AssertionError("model asked for more responses than scripted")
        return self._responses.pop(0)


def _echo():
    async def echo(value: int) -> int:
        """Echo a value."""
        return value

    return StructuredTool.from_function(coroutine=echo, name="echo", description="Echo a value.")


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    from cuga.config import settings

    monkeypatch.setattr(settings.policy, "enabled", False, raising=False)
    monkeypatch.setattr(settings.evolve, "enabled", False, raising=False)


@pytest.mark.asyncio
async def test_each_turn_gets_its_own_correction():
    from cuga.sdk import CugaAgent

    fence = "```python\nawait echo(value=1)\n```"
    model = _ScriptedModel(
        [
            AIMessage(content=fence),
            AIMessage(content="one"),
            AIMessage(content=fence),
            AIMessage(content="two"),
        ]
    )
    agent = CugaAgent(tools=[_echo()], model=model, execution_mode="function_calling")

    turn1 = await agent.invoke("first", thread_id="fc-violations")
    turn2 = await agent.invoke("second", thread_id="fc-violations")

    assert turn1.answer == "one"
    assert turn2.answer == "two", "turn 2 must be corrected too, not handed the raw fence"
