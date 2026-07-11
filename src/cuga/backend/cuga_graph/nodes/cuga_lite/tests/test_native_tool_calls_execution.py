"""Executable documentation: native tool_calls are mishandled in cuga-lite (issue #471).

When ``cuga_lite_bind_tools_mode`` binds tools (as the gpt-oss-20b runtime
profile does automatically), a model that answers with native
``AIMessage.tool_calls`` — the encoding FC-trained models naturally emit —
hits three defects on the execution side:

  D1  only ``tool_calls[0]`` is transpiled to code; parallel calls are
      silently dropped (adapter/response_utils.py: ``tool_calls[0]``), and
      the model then reports FALSE SUCCESS for work that never happened.
  D2  a non-empty text preamble ("I'll notify both customers now.") makes
      ``normalize_response`` skip tool_call recovery entirely
      (adapter/graph_adapter.py: ``if not content``); zero tools run and the
      announcement becomes the final answer.
  D9  a model whose ``bind_tools`` raises ``NotImplementedError`` (the
      langchain_core BaseChatModel default) crashes the run instead of
      falling back to the unbound model: ``NotImplementedError`` is a
      subclass of ``RuntimeError``, so the deliberate cap re-raise in
      helpers/bind_tools.py catches it first.

These tests are ``xfail(strict=True)``: they assert the CORRECT behavior and
are expected to fail until the defects are fixed — at which point they flip
to hard failures, forcing removal of the markers in the fixing PR. The
code-act control test at the bottom passes today and must keep passing.
"""

from typing import Any, List, Optional

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool

from cuga.sdk import CugaAgent

NOTIFIED: List[str] = []  # ground truth — what actually executed


@tool
def notify(customer: str) -> str:
    """Send a notification to a customer."""
    NOTIFIED.append(customer)
    return f"notification sent to {customer}"


class ScriptedFCModel(BaseChatModel):
    """Deterministic model that plays a fixed sequence of AIMessages.

    ``bind_tools`` accepts the binding (returns self) like an FC-capable
    provider, so ``resolve_model_with_bind_tools`` proceeds to invoke it.
    """

    script: List[AIMessage] = []
    calls_made: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted-fc"

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


class NoBindToolsModel(ScriptedFCModel):
    """Model without native FC support: langchain's default bind_tools raises."""

    def bind_tools(self, tools: Any, **kwargs: Any):
        raise NotImplementedError


TWO_TOOL_CALLS = [
    {"name": "notify", "args": {"customer": "acme"}, "id": "call_1", "type": "tool_call"},
    {"name": "notify", "args": {"customer": "globex"}, "id": "call_2", "type": "tool_call"},
]
FINAL_ANSWER = AIMessage(content="Done — both acme and globex have been notified.")
BIND_ALL = {"configurable": {"cuga_lite_bind_tools_mode": "all"}}
TASK = "notify customers acme and globex"


def _make_agent(model: ScriptedFCModel) -> CugaAgent:
    return CugaAgent(
        tools=[notify],
        model=model,
        auto_load_policies=False,
        enable_knowledge=False,
        enable_skills=False,
    )


@pytest.fixture(autouse=True)
def _reset_ground_truth():
    NOTIFIED.clear()
    yield


@pytest.mark.xfail(
    strict=True,
    reason="issue #471 D1: only tool_calls[0] is transpiled; parallel native tool "
    "calls are silently dropped and the final answer claims false success",
)
@pytest.mark.asyncio
async def test_parallel_tool_calls_all_execute():
    model = ScriptedFCModel(script=[AIMessage(content="", tool_calls=TWO_TOOL_CALLS), FINAL_ANSWER])
    agent = _make_agent(model)

    await agent.invoke(TASK, config=BIND_ALL)

    assert sorted(NOTIFIED) == ["acme", "globex"]


@pytest.mark.xfail(
    strict=True,
    reason="issue #471 D2: a text preamble alongside tool_calls makes normalize_response "
    "ignore the tool_calls entirely; zero tools run and the announcement becomes the answer",
)
@pytest.mark.asyncio
async def test_text_preamble_with_tool_calls_still_executes():
    model = ScriptedFCModel(
        script=[
            AIMessage(content="I'll notify both customers now.", tool_calls=TWO_TOOL_CALLS),
            FINAL_ANSWER,
        ]
    )
    agent = _make_agent(model)

    result = await agent.invoke(TASK, config=BIND_ALL)

    assert NOTIFIED, "the announced tool calls never executed"
    assert result.answer != "I'll notify both customers now."


@pytest.mark.xfail(
    strict=True,
    reason="issue #471 D9: NotImplementedError from bind_tools is a RuntimeError subclass, "
    "so the cap's 'except RuntimeError: raise' re-raises it and call_model crashes instead "
    "of falling back to the unbound model",
)
@pytest.mark.asyncio
async def test_model_without_bind_tools_falls_back_gracefully():
    model = NoBindToolsModel(
        script=[
            AIMessage(content="```python\nr = await notify(customer='acme')\nprint(r)\n```"),
            FINAL_ANSWER,
        ]
    )
    agent = _make_agent(model)

    result = await agent.invoke(TASK, config=BIND_ALL)

    assert result.error is None
    assert NOTIFIED == ["acme"]


@pytest.mark.asyncio
async def test_code_act_control_executes_both():
    """Control: the same task succeeds in default code-act mode (no binding)."""
    model = ScriptedFCModel(
        script=[
            AIMessage(
                content="```python\nr1 = await notify(customer='acme')\n"
                "r2 = await notify(customer='globex')\nprint(r1, r2)\n```"
            ),
            FINAL_ANSWER,
        ]
    )
    agent = _make_agent(model)

    result = await agent.invoke(TASK)

    assert sorted(NOTIFIED) == ["acme", "globex"]
    assert "notified" in result.answer
