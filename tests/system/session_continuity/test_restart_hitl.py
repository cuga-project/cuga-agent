"""A pending approval survives an app restart and can be resumed (#774).

The graphs here use the real ``WaitForResponse`` node, the real approval action and response
models, and the durable checkpointer with CUGA's strict serializer. The pause comes in the two forms
the product uses: a static breakpoint (``interrupt_after`` / ``interrupt_before``) and a dynamic
``interrupt()``. Each is run on the single-agent state and on the supervisor state.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

import pytest
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_state import CugaSupervisorState
from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import (
    ActionResponse,
    ActionType,
    create_tool_approval_action,
)
from cuga.backend.cuga_graph.nodes.human_in_the_loop.wait_for_response import WaitForResponse
from cuga.backend.cuga_graph.state.agent_state import AgentState

pytestmark = pytest.mark.slow

APPROVED = "approved"
ACTED = "acted"
WAIT = "WaitForResponse"


def _approval_action():
    return create_tool_approval_action(
        policy_name="Approve deletes",
        required_tools=["delete_database"],
        code_preview=["delete_database('users')"],
        full_code="delete_database('users')",
    )


def _approval() -> ActionResponse:
    return ActionResponse(
        action_id="tool_approval",
        response_type=ActionType.CONFIRMATION,
        confirmed=True,
        timestamp=datetime.now().isoformat(),
    )


def _dynamic_graph(saver, state_cls=AgentState, **breakpoints):
    """Ask -> WaitForResponse (``interrupt()``) -> Ask, which answers once it finds the response."""

    async def ask(state) -> Command[Literal["WaitForResponse", "FinalAnswerAgent"]]:
        if state.hitl_response is not None:
            return Command(update={"hitl_action": None}, goto="FinalAnswerAgent")
        return Command(
            update={
                "hitl_action": _approval_action(),
                "sender": "Ask",
                "messages": [AIMessage(content="This needs your approval.")],
            },
            goto=WAIT,
        )

    async def final_answer(state):
        return {"final_answer": APPROVED if state.hitl_response.confirmed else "declined"}

    async def unused(state):
        return {}

    builder = StateGraph(state_cls)
    builder.add_node("Ask", ask)
    builder.add_node(WAIT, WaitForResponse().node)
    builder.add_node("FinalAnswerAgent", final_answer)
    # WaitForResponse declares these as its possible destinations; the graph must contain them.
    for declared in ("ChatAgent", "CugaLite", "EntryRouter"):
        builder.add_node(declared, unused)
    builder.add_edge(START, "Ask")
    builder.add_edge("FinalAnswerAgent", END)
    return builder.compile(checkpointer=saver, **breakpoints)


def _static_graph(saver):
    """Ask -> Act with a breakpoint after Ask, the shape of the browser ``interrupt_after`` nodes."""

    async def ask(state):
        return {"hitl_action": _approval_action(), "messages": [AIMessage(content="Approve?")]}

    async def act(state):
        return {"final_answer": ACTED, "hitl_action": None}

    builder = StateGraph(AgentState)
    builder.add_node("Ask", ask)
    builder.add_node("Act", act)
    builder.add_edge(START, "Ask")
    builder.add_edge("Ask", "Act")
    builder.add_edge("Act", END)
    return builder.compile(checkpointer=saver, interrupt_after=["Ask"])


# id -> (graph factory, node waiting after the pause, resume input, answer after resuming)
_CASES = {
    "single-agent-static": (_static_graph, "Act", lambda: None, ACTED),
    "single-agent-dynamic": (
        lambda saver: _dynamic_graph(saver),
        WAIT,
        lambda: Command(resume=_approval().model_dump()),
        APPROVED,
    ),
    "supervisor-static": (
        lambda saver: _dynamic_graph(saver, CugaSupervisorState, interrupt_before=[WAIT]),
        WAIT,
        lambda: Command(resume=_approval().model_dump()),
        APPROVED,
    ),
    "supervisor-dynamic": (
        lambda saver: _dynamic_graph(saver, CugaSupervisorState),
        WAIT,
        lambda: Command(resume=_approval().model_dump()),
        APPROVED,
    ),
}


@pytest.mark.parametrize("case", list(_CASES))
async def test_pending_approval_survives_restart_and_resumes(checkpoints, case):
    build, waiting_node, resume_input, answer = _CASES[case]
    config = {"configurable": {"thread_id": "pending-approval"}}

    graph = build((await checkpoints.open()).saver)
    await graph.ainvoke({"input": "delete the users table"}, config)
    paused = await graph.aget_state(config)
    assert paused.next == (waiting_node,)

    graph = build((await checkpoints.open()).saver)  # a new saver on the same file, a new graph

    restored = await graph.aget_state(config)
    assert restored.next == (waiting_node,)
    assert restored.values["input"] == "delete the users table"
    assert restored.values["hitl_action"].action_id == "tool_approval"  # a model, not a plain dict
    assert bool(restored.interrupts) == case.endswith("dynamic")

    result = await graph.ainvoke(resume_input(), config)

    assert result["final_answer"] == answer
    assert (await graph.aget_state(config)).next == ()


async def test_pending_approval_is_resumed_through_stream_after_restart(conversation_server):
    server = conversation_server
    server.graph_factory = lambda saver: _dynamic_graph(saver)
    await server.restart()
    thread_id = await server.create()

    paused = await server.send(thread_id, "delete the users table")
    assert paused.status_code == 200, paused.text
    assert "Error" not in server.event_names(paused)
    assert (await server.graph.aget_state(server.config(thread_id))).next == (WAIT,)

    await server.restart()

    assert (await server.graph.aget_state(server.config(thread_id))).next == (WAIT,)
    resumed = await server.client.post(
        "/stream", headers={"X-Thread-ID": thread_id}, json=_approval().model_dump(mode="json")
    )
    assert resumed.status_code == 200, resumed.text
    assert "Error" not in server.event_names(resumed)
    assert APPROVED in resumed.text

    snapshot = await server.graph.aget_state(server.config(thread_id))
    assert snapshot.next == ()
    assert snapshot.values["final_answer"] == APPROVED
