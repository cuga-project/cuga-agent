"""Shared agent graph builder.

``build_agent_graph`` wires the canonical 3-node agent graph structure:

    START → prepare --Command--> call_model ↔ execute (loop) → END

CugaLite's function-calling mode uses the same three nodes: a native tool-call
turn is translated into a block the execute node runs.

Both CugaLite and CugaSupervisor share this structure.  The nodes themselves
are provided by the caller (produced by adapter factories), so the graph
builder stays graph-agnostic.

The returned graph is UNCOMPILED — callers are responsible for calling
``.compile(checkpointer=...)`` so each call-site can supply its own
checkpointer (e.g. the SDK applies thread-scoped memory at runtime).
"""

from __future__ import annotations

from typing import Callable, Type

from langchain_core.runnables import RunnableConfig
from langgraph.graph import START, StateGraph
from langgraph.types import Command

from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.graph_nodes import CoreGraphAdapter


def build_agent_graph(
    *,
    adapter: CoreGraphAdapter,
    state_class: Type,
    prepare_node: Callable,
    call_model_node: Callable,
    execute_node: Callable,
) -> StateGraph:
    """Wire and return an UNCOMPILED 3-node agent StateGraph.

    Args:
        adapter: Graph-specific seam; ``adapter.execute_node_name`` is used
            as the name of the third node so both graphs keep their existing
            node names (``sandbox`` / ``execute_agent_tool``).
        state_class: The Pydantic state class for the graph
            (``CugaLiteState`` or ``CugaSupervisorState``).
        prepare_node: Async node function for the prepare step.
        call_model_node: Async node function for the call_model step (use
            ``create_call_model_node`` from ``shared_nodes.py``).
        execute_node: Async node function for the execute/sandbox step.

    Returns:
        An uncompiled ``StateGraph``.  Call ``.compile(checkpointer=...)``
        to produce the runnable graph.
    """
    graph = StateGraph(state_class)

    graph.add_node("prepare", prepare_node)
    graph.add_node("call_model", call_model_node)

    async def execute_and_route(state, config: RunnableConfig):
        result = await execute_node(state, config)
        # Approval and policy pauses choose their own destination. A static
        # execute -> call_model edge would run even when they return goto=END.
        if isinstance(result, Command):
            return result
        return Command(update=result, goto="call_model")

    graph.add_node(adapter.execute_node_name, execute_and_route)

    graph.add_edge(START, "prepare")
    # prepare returns Command(goto=...) — no static edge (avoids call_model after BLOCK_INTENT).
    # Execute state updates loop back for the answer; Commands route explicitly.

    return graph
