"""Function-calling tool approval through the real SDK path.

A policy stored with ``agent.policies.add_tool_approval`` interrupts a native tool
call exactly as it interrupts CodeAct code: the call is translated into a block,
the approval check matches the tool name in it, the run pauses before anything
runs, and resuming with the approval executes the call and answers its id. A
denial runs nothing.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool

from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import ActionResponse, ActionType

pytestmark = pytest.mark.unit


class _Bound:
    """What ``bind_tools`` returns: a runnable wrapping the model."""

    def __init__(self, model):
        self._model = model

    async def ainvoke(self, messages, config=None, **kwargs):
        return await self._model.ainvoke(messages, config=config, **kwargs)


class _ScriptedModel:
    def __init__(self, responses):
        self._responses = list(responses)
        self.invocations = 0
        self.seen: list = []

    def bind_tools(self, tools, **kwargs):
        return _Bound(self)  # a real bind returns a new runnable, never the model itself

    async def ainvoke(self, messages, config=None, **kwargs):
        self.invocations += 1
        self.seen.append(list(messages))
        if not self._responses:
            raise AssertionError("model asked for more responses than scripted")
        return self._responses.pop(0)


RAN: list = []
_CALL = {"name": "delete_record", "args": {"record_id": 1}, "id": "c1", "type": "tool_call"}


def _delete_record():
    async def delete_record(record_id: int) -> str:
        """Delete a record."""
        RAN.append(record_id)
        return "deleted"

    return StructuredTool.from_function(
        coroutine=delete_record, name="delete_record", description="Delete a record."
    )


def _response(confirmed: bool) -> ActionResponse:
    return ActionResponse(
        action_id="tool_approval",
        response_type=ActionType.CONFIRMATION,
        confirmed=confirmed,
        timestamp=datetime.now().isoformat(),
    )


async def _guarded_fc_agent(model):
    from cuga.sdk import CugaAgent

    agent = CugaAgent(
        tools=[_delete_record()], model=model, execution_mode="function_calling", reset_policy_storage=True
    )
    await agent.policies.add_tool_approval(
        name="Approve deletions",
        required_tools=["delete_record"],
        approval_message="Deleting needs approval.",
    )
    return agent


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    from cuga.config import settings

    monkeypatch.setattr(settings.policy, "enabled", True, raising=False)
    monkeypatch.setattr(settings.evolve, "enabled", False, raising=False)
    RAN.clear()
    yield
    RAN.clear()


@pytest.mark.asyncio
async def test_fc_tool_approval_pauses_then_the_approved_call_runs_and_answers_its_id():
    model = _ScriptedModel(
        [AIMessage(content="", tool_calls=[_CALL]), AIMessage(content="Deleted record 1.")]
    )
    agent = await _guarded_fc_agent(model)

    paused = await agent.invoke("delete record 1", thread_id="fc-approval")

    assert model.invocations == 1 and RAN == [], "paused before the tool ran"
    assert "Deleting needs approval." in paused.answer, paused.answer

    resumed = await agent.invoke(None, thread_id="fc-approval", action_response=_response(True))

    assert RAN == [1] and resumed.answer == "Deleted record 1."
    second = model.seen[1]  # the model turn after the approved call ran
    ai = [m for m in second if isinstance(m, AIMessage) and m.tool_calls]
    tool = [m for m in second if isinstance(m, ToolMessage)]
    assert ai and ai[0].tool_calls[0]["id"] == "c1", "the paused assistant turn kept its tool_calls"
    assert tool and (tool[0].tool_call_id, tool[0].content) == ("c1", "deleted")


@pytest.mark.asyncio
async def test_fc_tool_approval_denied_runs_nothing():
    model = _ScriptedModel([AIMessage(content="", tool_calls=[_CALL])])
    agent = await _guarded_fc_agent(model)

    await agent.invoke("delete record 1", thread_id="fc-denied")
    denied = await agent.invoke(None, thread_id="fc-denied", action_response=_response(False))

    assert RAN == [] and model.invocations == 1
    assert "cancelled" in denied.answer.lower(), denied.answer


@pytest.mark.asyncio
async def test_codeact_on_the_same_agent_still_asks():
    """Both modes meet the same approval check, on the block each one sends to the sandbox."""
    codeact_model = _ScriptedModel(
        [AIMessage(content="```python\nprint(await delete_record(record_id=1))\n```")]
    )
    agent = await _guarded_fc_agent(codeact_model)

    result = await agent.invoke("delete record 1", thread_id="codeact-approval", execution_mode="codeact")

    assert codeact_model.invocations == 1 and RAN == []
    assert "Deleting needs approval." in result.answer, result.answer
