"""A pending approval survives an app restart and can be resumed (#774).

The graphs here use the real ``WaitForResponse`` node, the real approval action and response
models, and the durable checkpointer with CUGA's strict serializer. The pause comes in the two forms
the product uses: a static breakpoint (``interrupt_after`` / ``interrupt_before``) and a dynamic
``interrupt()``. Each is run on the single-agent state and on the supervisor state.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Literal
from unittest.mock import patch

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from cuga.backend.cuga_graph.nodes.cuga_supervisor.child_checkpoint import resolve_child_checkpoint_id
from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_state import CugaSupervisorState
from cuga.backend.cuga_graph.nodes.cuga_supervisor.delegation import create_agent_delegation_func
from cuga.backend.cuga_graph.nodes.cuga_supervisor.execution_context import SupervisorExecutionContext
from cuga.backend.cuga_graph.nodes.cuga_supervisor.supervisor_graph_adapter import SupervisorGraphAdapter
from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import (
    ActionResponse,
    ActionType,
    create_tool_approval_action,
)
from cuga.backend.cuga_graph.nodes.human_in_the_loop.wait_for_response import WaitForResponse
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.supervisor_utils.supervisor_config import build_agents_from_list

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


def _delegation_state():
    """The supervisor state fields that child checkpoint identity and ``record_delegation`` read."""
    return SimpleNamespace(
        user_id="u1",
        thread_id="row-2f-parent-thread",
        service_scope={"tenant_id": "t1", "instance_id": "i1"},
        selected_agents=[],
        agent_results={},
        agent_variables={},
        agent_chat_messages={},
        metrics={},
    )


async def _delegate(adapter, agents, name, state, task):
    """Call the real delegation function from a frame that carries the supervisor context."""
    delegate = create_agent_delegation_func(adapter, name, agents[name])
    __supervisor_exec__ = SupervisorExecutionContext(state=state)  # noqa: F841 (found by frame walk)
    return await delegate(task)


async def _build_roster(saver, reply):
    """The same loader the server uses for the YAML roster and the stored sub-agents."""
    model = FakeListChatModel(responses=[reply])
    with patch("cuga.supervisor_utils.supervisor_config._get_model_from_config", return_value=model):
        return await build_agents_from_list(
            [
                {
                    "name": "child-a",
                    "model": {"provider": "openai", "model_name": "fake"},
                    "memory_scope": "conversation",
                    "auto_load_policies": False,
                }
            ],
            checkpointer=saver,
        )


async def _child_messages(agents, adapter, state):
    child_id = resolve_child_checkpoint_id(
        state=state, adapter=adapter, agent_name="child-a", agent_or_config=agents["child-a"]
    )
    snapshot = await agents["child-a"].graph.aget_state({"configurable": {"thread_id": child_id}})
    # The nodes persist ``model_dump()``, so messages come back as plain dicts.
    return [
        m["content"] if isinstance(m, dict) else m.content
        for m in (snapshot.values or {}).get("chat_messages", [])
    ]


async def test_conversation_scoped_child_agent_retains_history_across_restart_and_rebuild(checkpoints):
    """W17.1 row 2f / W04.4: a conversation-scoped child keeps its history across both events.

    The roster is built by ``build_agents_from_list`` with the shared saver, so the child is a real
    ``CugaAgent`` on it, and each delegation goes through ``create_agent_delegation_func`` and
    ``resolve_child_checkpoint_id``. Only the supervisor's own model is left out: the test calls the
    delegation function the generated supervisor code would call. The saver is closed and reopened
    (a restart), then the roster is rebuilt on the same saver (a publish).
    """
    state = _delegation_state()

    # --- first process: one delegation ---
    saver = (await checkpoints.open()).saver
    agents = await _build_roster(saver, "child answer 1")
    adapter = SupervisorGraphAdapter(agents=agents, supervisor_id="test-supervisor")
    assert await _delegate(adapter, agents, "child-a", state, "first message") == "child answer 1"
    assert len(await _child_messages(agents, adapter, state)) == 2

    # --- restart: a new saver on the same file, a rebuilt roster and supervisor ---
    saver = (await checkpoints.open()).saver
    agents = await _build_roster(saver, "child answer 2")
    adapter = SupervisorGraphAdapter(agents=agents, supervisor_id="test-supervisor")
    before = await _child_messages(agents, adapter, state)
    assert len(before) == 2, "child history must survive the restart"
    assert before[-1] == "child answer 1"
    assert await _delegate(adapter, agents, "child-a", state, "second message") == "child answer 2"
    assert (await _child_messages(agents, adapter, state))[-2:] == ["second message", "child answer 2"]

    # --- publish: the roster is rebuilt on the same saver, the old agents are discarded ---
    agents = await _build_roster(saver, "child answer 3")
    adapter = SupervisorGraphAdapter(agents=agents, supervisor_id="test-supervisor")
    assert await _delegate(adapter, agents, "child-a", state, "third message") == "child answer 3"
    history = await _child_messages(agents, adapter, state)
    assert history == [
        "first message",
        "child answer 1",
        "second message",
        "child answer 2",
        "third message",
        "child answer 3",
    ]
