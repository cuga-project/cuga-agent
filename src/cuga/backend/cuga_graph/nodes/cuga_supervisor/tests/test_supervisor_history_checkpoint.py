"""The supervisor's own history survives a checkpoint on the server path (#976).

The server runs the supervisor inside an ``AgentState`` graph. ``CugaSupervisorNode``
hands ``supervisor_chat_messages`` to the supervisor subgraph and writes the result
back; the callback node passes state on with ``model_dump()``, so the checkpoint
holds dicts. A follow-up turn reloads them with ``AgentState(**snapshot.values)``
(``server/main.py``), which must rebuild the message classes they were (#777).

Only the supervisor subgraph is replaced: it records the history it is given and
answers, returning channel values as a compiled graph does.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_node import CugaSupervisorNode
from cuga.backend.cuga_graph.state.agent_state import AgentState

pytestmark = pytest.mark.unit


class _RecordingSubgraph:
    def __init__(self):
        self.seen: list = []

    async def ainvoke(self, state, config=None):
        history = list(state.supervisor_chat_messages)
        self.seen.append(history)
        answer = f"answer {len(self.seen)}"
        return {"supervisor_chat_messages": history + [AIMessage(content=answer)], "final_answer": answer}


def _graph(subgraph):
    node = CugaSupervisorNode()
    node.set_subgraph(subgraph)
    graph = StateGraph(AgentState)
    graph.add_node("CugaSupervisor", node.node)
    graph.add_node("CugaSupervisorCallback", node.callback_node)
    graph.add_node("FinalAnswerAgent", lambda state: {})
    graph.add_edge(START, "CugaSupervisor")
    graph.add_edge("FinalAnswerAgent", END)
    return graph.compile(checkpointer=MemorySaver())


async def _follow_up(graph, config, query):
    """Reload the thread for a follow-up question as ``server/main.py`` does: rebuild
    ``AgentState`` from the checkpoint, set the thread and the new input, apply the
    message sliding window."""
    state = AgentState(**(await graph.aget_state(config)).values)
    state.thread_id = config["configurable"]["thread_id"]
    state.input = query
    state.apply_message_sliding_window()
    return await graph.ainvoke(state, config)


@pytest.mark.asyncio
async def test_supervisor_history_comes_back_from_the_checkpoint_as_message_classes():
    subgraph = _RecordingSubgraph()
    graph = _graph(subgraph)
    config = {"configurable": {"thread_id": "supervisor-history"}}

    await graph.ainvoke(AgentState(input="first question", thread_id="supervisor-history"), config)
    stored = (await graph.aget_state(config)).values["supervisor_chat_messages"]
    assert [m["type"] for m in stored] == ["human", "ai"], "the checkpoint holds the dumped dicts"

    result = await _follow_up(graph, config, "second question")

    assert [(type(m), m.content) for m in subgraph.seen[1]] == [
        (HumanMessage, "first question"),
        (AIMessage, "answer 1"),
        (HumanMessage, "second question"),
    ]
    assert result["final_answer"] == "answer 2"
