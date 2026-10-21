"""The SDK reads graph state only through the async API (#774, W15.2).

Each test runs the real compiled SDK graph on ``AsyncOnlySaver``: a saver whose sync
``get_tuple`` / ``list`` / ``put`` / ``put_writes`` record the call and raise, the way
``AsyncSqliteSaver`` / ``AsyncPostgresSaver`` do on the event loop. ``invoke()`` wraps
its state reads in ``except Exception``, so a sync read would not fail the call: it
would silently drop the thread's history or the pause notice. The tests therefore check
both ``saver.sync_calls == []`` and the result that depends on the state read.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional
from unittest.mock import patch

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


def _approval(confirmed: bool = True):
    from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import (
        ActionResponse,
        ActionType,
    )

    return ActionResponse(
        action_id="tool_approval",
        response_type=ActionType.CONFIRMATION,
        confirmed=confirmed,
        timestamp=datetime.now().isoformat(),
    )


async def _chat_messages(agent: CugaAgent, thread_id: str) -> list[str]:
    snapshot = await agent.graph.aget_state({"configurable": {"thread_id": thread_id}})
    messages = snapshot.values.get("chat_messages") or []
    # Stored as message objects or as their model_dump() dicts, depending on the update.
    return [str(m["content"] if isinstance(m, dict) else m.content) for m in messages]


class TestCugaAgentInvoke:
    @pytest.mark.asyncio
    async def test_second_turn_loads_history_without_sync_calls(self, saver):
        agent = _agent(saver)

        first = await agent.invoke("first question", thread_id="t-async-history")
        second = await agent.invoke("second question", thread_id="t-async-history")

        assert saver.sync_calls == []
        assert first.answer == "There are 3 accounts."
        assert second.answer == "There are 3 accounts."
        # invoke() appended turn 2 to the history it read with aget_state.
        history = await _chat_messages(agent, "t-async-history")
        assert history[0] == "first question"
        assert "second question" in history
        assert history.index("second question") > 0
        assert saver.sync_calls == []

    @pytest.mark.asyncio
    async def test_run_receipt_path_without_sync_calls(self, saver):
        agent = _agent(saver)

        with patch("cuga.sdk.settings.advanced_features.run_receipt", True):
            result = await agent.invoke("how many accounts?", thread_id="t-async-receipt")

        assert saver.sync_calls == []
        assert result.receipt is not None
        assert result.answer == "There are 3 accounts."


def _approval_subgraph(**_kwargs):
    """Stand-in CugaLite subgraph: asks for tool approval, then answers once approved."""
    from langgraph.graph import END, START, StateGraph

    from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import (
        create_tool_approval_action,
    )
    from cuga.backend.cuga_graph.state.agent_state import AgentState

    async def run(state: AgentState) -> dict:
        metadata = dict(state.cuga_lite_metadata or {})
        if metadata.get("user_approved"):
            return {"final_answer": "deleted after approval", "hitl_action": None}
        metadata.update(policy_name="Approve deletes", required_tools=["delete_database"])
        return {
            "hitl_action": create_tool_approval_action(
                policy_name="Approve deletes",
                required_tools=["delete_database"],
                code_preview=["delete_database('users')"],
                full_code="delete_database('users')",
            ),
            "cuga_lite_metadata": metadata,
        }

    graph = StateGraph(AgentState)
    graph.add_node("Run", run)
    graph.add_edge(START, "Run")
    graph.add_edge("Run", END)
    return graph


class TestCugaAgentHitl:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("run_receipt", [False, True])
    async def test_pause_and_resume_without_sync_calls(self, saver, run_receipt):
        agent = _agent(saver)
        thread_id = f"t-async-hitl-{run_receipt}"

        with (
            patch(
                "cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph.create_cuga_lite_graph",
                _approval_subgraph,
            ),
            patch("cuga.sdk.settings.advanced_features.run_receipt", run_receipt),
        ):
            paused = await agent.invoke("delete the users table", thread_id=thread_id)
            # The pause notice comes from the aget_state read after the turn.
            assert PAUSED in paused.answer
            assert "aget_state" in paused.answer
            snapshot = await agent.graph.aget_state({"configurable": {"thread_id": thread_id}})
            assert snapshot.next == ("WaitForResponse",)

            resumed = await agent.invoke(None, thread_id=thread_id, action_response=_approval())

        assert saver.sync_calls == []
        assert resumed.answer == "deleted after approval"
        snapshot = await agent.graph.aget_state({"configurable": {"thread_id": thread_id}})
        assert snapshot.next == ()

    @pytest.mark.asyncio
    async def test_resume_that_pauses_again_reports_pause(self, saver):
        agent = _agent(saver)
        thread_id = "t-async-hitl-again"

        with patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_graph.create_cuga_lite_graph",
            _approval_subgraph,
        ):
            await agent.invoke("delete the users table", thread_id=thread_id)
            # Resuming without a response re-enters WaitForResponse's interrupt.
            again = await agent.invoke(None, thread_id=thread_id)

        assert saver.sync_calls == []
        assert PAUSED in again.answer
        assert "action_response" in again.answer


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
