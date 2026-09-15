"""Function-calling mode refuses to start while a tool-approval policy is stored.

Through the real SDK path: a policy added with ``agent.policies.add_tool_approval``
lands in policy storage, and the function-calling turn queries that storage before
its first model call. CodeAct on the same agent still goes through the approval
prompt, which is the behaviour the refusal protects.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool

from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.graph_adapter import FC_TOOL_APPROVAL_UNSUPPORTED

pytestmark = pytest.mark.unit


class _ScriptedModel:
    def __init__(self, responses):
        self._responses = list(responses)
        self.invocations = 0

    def bind_tools(self, tools, **kwargs):
        return self

    async def ainvoke(self, messages, config=None, **kwargs):
        self.invocations += 1
        if not self._responses:
            raise AssertionError("model asked for more responses than scripted")
        return self._responses.pop(0)


def _delete_record():
    async def delete_record(record_id: int) -> str:
        """Delete a record."""
        raise AssertionError("a guarded tool ran without approval")

    return StructuredTool.from_function(
        coroutine=delete_record, name="delete_record", description="Delete a record."
    )


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    from cuga.config import settings

    monkeypatch.setattr(settings.policy, "enabled", True, raising=False)
    monkeypatch.setattr(settings.evolve, "enabled", False, raising=False)


@pytest.mark.asyncio
async def test_fc_refuses_with_a_stored_tool_approval_policy_and_codeact_still_asks():
    from cuga.sdk import CugaAgent

    fc_model = _ScriptedModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "delete_record", "args": {"record_id": 1}, "id": "c1", "type": "tool_call"}
                ],
            )
        ]
    )
    agent = CugaAgent(
        tools=[_delete_record()], model=fc_model, execution_mode="function_calling", reset_policy_storage=True
    )
    await agent.policies.add_tool_approval(
        name="Approve deletions",
        required_tools=["delete_record"],
        approval_message="Deleting needs approval.",
    )

    result = await agent.invoke("delete record 1", thread_id="fc-approval")

    assert fc_model.invocations == 0, "refused before the model was invoked"
    assert (
        FC_TOOL_APPROVAL_UNSUPPORTED in (result.error or "") or FC_TOOL_APPROVAL_UNSUPPORTED in result.answer
    )

    # Same agent, CodeAct for this call: the guarded code goes to the approval prompt.
    codeact_model = _ScriptedModel(
        [AIMessage(content="```python\nprint(await delete_record(record_id=1))\n```")]
    )
    agent._model = codeact_model
    agent._graph = None
    agent._compiled_graph = None
    result = await agent.invoke("delete record 1", thread_id="codeact-approval", execution_mode="codeact")

    assert codeact_model.invocations == 1
    assert "approval" in result.answer.lower(), result.answer
