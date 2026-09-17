"""Shared agent graph builder.

``build_agent_graph`` wires the canonical 3-node agent graph structure:

    START → prepare --Command--> call_model ↔ execute (loop) → END

CugaLite may add a fourth node, ``tool_exec`` (native function-calling
mode), which routes back into ``call_model`` with a Command.

Both CugaLite and CugaSupervisor share this structure.  The nodes themselves
are provided by the caller (produced by adapter factories), so the graph
builder stays graph-agnostic.

The returned graph is UNCOMPILED — callers are responsible for calling
``.compile(checkpointer=...)`` so each call-site can supply its own
checkpointer (e.g. the SDK applies thread-scoped memory at runtime).
"""

from __future__ import annotations

from typing import Callable, Optional, Type

from langgraph.graph import START, StateGraph

from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.graph_nodes import CoreGraphAdapter


def build_agent_graph(
    *,
    adapter: CoreGraphAdapter,
    state_class: Type,
    prepare_node: Callable,
    call_model_node: Callable,
    execute_node: Callable,
    tool_exec_node: Optional[Callable] = None,
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
        tool_exec_node: Optional node that executes native ``tool_calls`` and
            replies with ``ToolMessage``s (CugaLite function-calling mode). When
            supplied it is added as ``"tool_exec"``; it routes back to
            ``call_model`` (or to END on error) with a Command, and ``call_model``
            only routes to it in that mode, so it is dormant on every CodeAct run.
            Omitted by the Supervisor graph.

    Returns:
        An uncompiled ``StateGraph``.  Call ``.compile(checkpointer=...)``
        to produce the runnable graph.
    """
    graph = StateGraph(state_class)

    graph.add_node("prepare", prepare_node)
    graph.add_node("call_model", call_model_node)
    graph.add_node(adapter.execute_node_name, execute_node)

    graph.add_edge(START, "prepare")
    # prepare returns Command(goto=...) — no static edge (avoids call_model after BLOCK_INTENT).
    # Execute node returns a state update (not Command); loop back for the NL answer.
    graph.add_edge(adapter.execute_node_name, "call_model")

    if tool_exec_node is not None:
        # No static edge back: the node routes with Command (call_model on a normal
        # exit, END on an error exit), so a terminal error never schedules one more
        # model turn the way a static edge would.
        graph.add_node("tool_exec", tool_exec_node)

    return graph
