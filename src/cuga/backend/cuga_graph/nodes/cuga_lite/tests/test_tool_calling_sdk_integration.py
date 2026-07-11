"""End-to-end: CugaAgent(tool_calling=ToolCalling(...)) drives native FC (issue #471 P1).

Uses a deterministic scripted model (no API keys) that emits two parallel tool
calls behind a text preamble — exercising the D1 + D2 fixes through the public
SDK surface — and confirms the default (no tool_calling) path is unaffected.
"""

from typing import Any, List, Optional

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool

from cuga import CugaAgent, ToolCalling

NOTIFIED: List[str] = []


@tool
def notify(customer: str) -> str:
    """Send a notification to a customer."""
    NOTIFIED.append(customer)
    return f"notification sent to {customer}"


class _ScriptedFCModel(BaseChatModel):
    script: List[AIMessage] = []
    calls_made: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted-fc-sdk"

    def bind_tools(self, tools: Any, **kwargs: Any):
        return self

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        idx = min(self.calls_made, len(self.script) - 1)
        object.__setattr__(self, "calls_made", self.calls_made + 1)
        return ChatResult(generations=[ChatGeneration(message=self.script[idx])])


TWO_CALLS = [
    {"name": "notify", "args": {"customer": "acme"}, "id": "1", "type": "tool_call"},
    {"name": "notify", "args": {"customer": "globex"}, "id": "2", "type": "tool_call"},
]
FINAL = AIMessage(content="Done — acme and globex notified.")
TASK = "notify acme and globex"


def _agent(script):
    return CugaAgent(
        tools=[notify],
        model=_ScriptedFCModel(script=script),
        auto_load_policies=False,
        enable_knowledge=False,
        enable_skills=False,
    )


@pytest.fixture(autouse=True)
def _reset():
    NOTIFIED.clear()
    yield


@pytest.mark.asyncio
async def test_native_tool_calling_via_constructor():
    agent = _agent([AIMessage(content="I'll notify both now.", tool_calls=TWO_CALLS), FINAL])
    result = await agent.invoke(TASK, tool_calling=ToolCalling(mode="native"))
    assert sorted(NOTIFIED) == ["acme", "globex"]
    assert result.error is None


@pytest.mark.asyncio
async def test_per_invoke_tool_calling_overrides_default():
    # constructor default is code; per-invoke native turns it on
    agent = _agent([AIMessage(content="", tool_calls=TWO_CALLS), FINAL])
    result = await agent.invoke(TASK, tool_calling=ToolCalling(mode="native", native_tools=["notify"]))
    assert sorted(NOTIFIED) == ["acme", "globex"]
    assert result.error is None


@pytest.mark.asyncio
async def test_default_is_unaffected_without_tool_calling():
    # No tool_calling: the empty-content tool_calls take the LEGACY single-call
    # path (only the first), proving FC stays off unless opted in.
    agent = _agent([AIMessage(content="", tool_calls=TWO_CALLS), FINAL])
    await agent.invoke(TASK)
    assert NOTIFIED == ["acme"]
