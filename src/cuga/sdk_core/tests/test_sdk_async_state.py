"""The supervisor SDK reads graph state only through the async API (#774).

``CugaAgent`` is covered by the cross-process ``AsyncSqliteSaver`` test. Each test here
runs the real compiled supervisor graph on ``AsyncOnlySaver``: a saver whose sync
``get_tuple`` / ``list`` / ``put`` / ``put_writes`` record the call and raise, the way
``AsyncSqliteSaver`` / ``AsyncPostgresSaver`` do on the event loop. ``invoke()`` wraps
its state reads in ``except Exception``, so a sync read would not fail the call: it
would silently drop the thread's history or the pause notice. The tests therefore check
both ``saver.sync_calls == []`` and the result that depends on the state read.
"""

from __future__ import annotations

from typing import Any, List, Optional

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from cuga.sdk import CugaAgent, CugaSupervisor

pytestmark = pytest.mark.unit

PAUSED = "Execution paused for approval"


@pytest.fixture
def saver(async_only_saver_cls):
    # AsyncOnlySaver from tests/fixtures/, loaded by path in this directory's conftest.
    return async_only_saver_cls()


class _FixedChatModel(BaseChatModel):
    """Answers every call with the same plain-text message."""

    answer: str = "There are 3 accounts."

    @property
    def _llm_type(self) -> str:
        return "fixed-fake"

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.answer))])


def _agent(saver, model: Optional[BaseChatModel] = None) -> CugaAgent:
    return CugaAgent(
        tools=[],
        model=model or _FixedChatModel(),
        auto_load_policies=False,
        enable_knowledge=False,
        enable_skills=False,
        checkpointer=saver,
    )


def _supervisor(saver, helper_saver) -> CugaSupervisor:
    return CugaSupervisor(
        agents={"helper": _agent(helper_saver)},
        model=_FixedChatModel(answer="Supervisor answer."),
        auto_load_policies=False,
        checkpointer=saver,
    )


class TestCugaSupervisorInvoke:
    @pytest.mark.asyncio
    async def test_invoke_without_sync_calls(self, saver, async_only_saver_cls):
        supervisor = _supervisor(saver, async_only_saver_cls())

        result = await supervisor.invoke("hello", thread_id="t-async-supervisor")

        assert saver.sync_calls == []
        assert result.answer
        assert PAUSED not in result.answer

    @pytest.mark.asyncio
    async def test_pause_and_resume_without_sync_calls(self, saver, async_only_saver_cls):
        from langgraph.graph import END, START, StateGraph

        from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_state import (
            CugaSupervisorState,
        )

        # A parked graph on the supervisor's own state type: invoke() returns no
        # answer, so the pause notice depends on the aget_state read.
        def produce(_state: CugaSupervisorState) -> dict:
            return {"final_answer": ""}

        def wait_for_response(_state: CugaSupervisorState) -> dict:
            return {"final_answer": "resumed"}

        builder = StateGraph(CugaSupervisorState)
        builder.add_node("Produce", produce)
        builder.add_node("WaitForResponse", wait_for_response)
        builder.add_edge(START, "Produce")
        builder.add_edge("Produce", "WaitForResponse")
        builder.add_edge("WaitForResponse", END)

        supervisor = _supervisor(saver, async_only_saver_cls())
        supervisor._compiled_graph = builder.compile(checkpointer=saver, interrupt_before=["WaitForResponse"])

        paused = await supervisor.invoke("work", thread_id="t-async-supervisor-hitl")
        resumed = await supervisor.invoke(None, thread_id="t-async-supervisor-hitl")

        assert saver.sync_calls == []
        assert PAUSED in paused.answer
        assert resumed.answer == "resumed"
