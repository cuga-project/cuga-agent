"""AgentLoop and AgentRunner read and write graph state only through the async API.

A durable async saver (``AsyncSqliteSaver``, ``AsyncPostgresSaver``) raises when its
sync methods are called from the event loop. ``AsyncOnlySaver`` simulates that: its
sync ``get_tuple`` / ``put`` / ``put_writes`` / ``list`` raise, and its async
twins still work. A turn that touches a sync method fails loudly.

The graph is a minimal two-node ``StateGraph(AgentState)`` rather than the full
``CugaEntryGraph``, which needs LLMs and tools. The node names match the
ones ``AgentLoop.get_output`` routes on, so the turn still goes through the
migrated lines: ``get_output``'s state read (from both ``run`` and ``run_stream``),
and ``AgentRunner.get_current_state`` plus ``aupdate_state`` in both browser loops
(``run_task_generic`` and ``run_task_generic_yield``).
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph

from cuga.backend.activity_tracker.tracker import ActivityTracker
from cuga.backend.cuga_graph.state.agent_state import AgentState, default_state
from cuga.backend.cuga_graph.utils.agent_loop import AgentLoop, AgentLoopAnswer
from tests.fixtures.async_only_saver import AsyncOnlySaver

pytestmark = pytest.mark.unit

_TOOL_CALL = {"name": "click", "args": {"bid": "1"}, "id": "t1", "type": "tool_call"}


def _answer_graph():
    def final_answer(state: AgentState):
        return {"final_answer": "done", "messages": [AIMessage(content="done")]}

    g = StateGraph(AgentState)
    g.add_node("FinalAnswerAgent", final_answer)
    g.add_edge(START, "FinalAnswerAgent")
    g.add_edge("FinalAnswerAgent", END)
    return g.compile(checkpointer=AsyncOnlySaver())


def _browser_graph():
    """ActionAgent emits a tool call and pauses; the runner resumes to FinalAnswerAgent."""

    def action(state: AgentState):
        return {"messages": [AIMessage(content="", tool_calls=[_TOOL_CALL])]}

    def final_answer(state: AgentState):
        return {"final_answer": f"saw {state.feedback[-1]['message']}"}

    g = StateGraph(AgentState)
    g.add_node("ActionAgent", action)
    g.add_node("FinalAnswerAgent", final_answer)
    g.add_edge(START, "ActionAgent")
    g.add_edge("ActionAgent", "FinalAnswerAgent")
    g.add_edge("FinalAnswerAgent", END)
    return g.compile(checkpointer=AsyncOnlySaver(), interrupt_after=["ActionAgent"])


def _loop(graph, thread_id):
    return AgentLoop(thread_id=thread_id, langfuse_handler=None, graph=graph, tracker=ActivityTracker())


async def test_run_stream_reads_final_state_through_async_saver():
    loop = _loop(_answer_graph(), "stream-thread")
    events = [e async for e in loop.run_stream(state=default_state(page=None, observation=None, goal="q"))]
    final = events[-1]
    assert isinstance(final, AgentLoopAnswer)
    assert final.end is True and final.answer == "done"


_FEEDBACK = [{"status": "success", "message": "clicked"}]


@contextmanager
def _browser_runner(thread_id):
    """An AgentRunner over ``_browser_graph`` with the entry graph, tool execution and env faked."""
    from cuga.backend.cuga_graph.utils import controller

    runner = controller.AgentRunner(browser_enabled=False, thread_id=thread_id)
    runner.env = SimpleNamespace(
        page=None,
        tool_implementation_provider=None,
        chat=None,
        messages=[],
        step=AsyncMock(return_value=(None, 0.0, False, False, {})),
    )
    entry = SimpleNamespace(graph=_browser_graph(), policy_system=None, build_graph=AsyncMock())
    with (
        patch.object(controller, "CugaEntryGraph", return_value=entry),
        patch.object(controller.AgentRunner, "process_event_async", AsyncMock(return_value=_FEEDBACK)),
        patch.object(controller.tracker, "steps", []),
    ):
        yield runner


async def test_agent_runner_browser_loop_uses_async_state_access():
    with _browser_runner("runner-thread") as runner:
        result = await runner.run_task_generic(goal="click it")

    assert result.answer == "saw clicked"
    state = await runner.get_current_state()
    assert state.final_answer == "saw clicked"
    assert state.feedback == _FEEDBACK
