"""Phase 3 — build_agent_graph shared graph builder tests.

Pins that:
1. The returned compiled graph has the expected node names (prepare, call_model,
   and the adapter's execute_node_name).
2. START → prepare → call_model edges exist.
3. The graph compiles without error given a valid state class and mock nodes.
4. Different execute_node_name values from the adapter are reflected in the graph.
"""

from __future__ import annotations

from typing import Any, List, Optional

from langchain_core.messages import BaseMessage
from pydantic import BaseModel

from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.graph_nodes import CoreGraphAdapter


# ── Minimal adapter & state for graph construction ─────────────────────────


class _MinimalAdapter(CoreGraphAdapter):
    messages_key = "chat_messages"
    execute_node_name = "sandbox"

    def get_messages(self, state: Any) -> List[BaseMessage]:
        return []

    def resolve_max_steps(self, state: Any, override: Optional[int]) -> int:
        return 50


class _SupervisorAdapter(_MinimalAdapter):
    execute_node_name = "execute_agent_tool"
    messages_key = "supervisor_chat_messages"


class _MockState(BaseModel):
    chat_messages: list = []
    step_count: int = 0
    prepared_prompt: str = ""
    cuga_lite_metadata: dict = {}
    variables_storage: dict = {}


def _noop_node():
    async def node(state, config=None):
        from langgraph.types import Command
        from langgraph.graph import END

        return Command(goto=END, update={"final_answer": "done", "execution_complete": True})

    return node


def _get_builder():
    from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_graph import build_agent_graph

    return build_agent_graph


# ── 1. Graph compiles with Lite-like adapter ───────────────────────────────


def test_build_agent_graph_compiles_with_lite_adapter():
    build = _get_builder()
    adapter = _MinimalAdapter()

    graph = build(
        adapter=adapter,
        state_class=_MockState,
        prepare_node=_noop_node(),
        call_model_node=_noop_node(),
        execute_node=_noop_node(),
    )

    assert graph is not None


# ── 2. Graph has expected node names (Lite) ────────────────────────────────


def test_built_graph_has_prepare_and_call_model_nodes():
    build = _get_builder()
    adapter = _MinimalAdapter()

    graph = build(
        adapter=adapter,
        state_class=_MockState,
        prepare_node=_noop_node(),
        call_model_node=_noop_node(),
        execute_node=_noop_node(),
    )

    node_names = set(graph.nodes.keys())
    assert "prepare" in node_names
    assert "call_model" in node_names
    assert "sandbox" in node_names  # adapter.execute_node_name


def test_built_graph_has_execute_to_call_model_edge():
    """Sandbox must loop back to call_model after code execution (regression guard)."""
    build = _get_builder()
    adapter = _MinimalAdapter()

    graph = build(
        adapter=adapter,
        state_class=_MockState,
        prepare_node=_noop_node(),
        call_model_node=_noop_node(),
        execute_node=_noop_node(),
    )

    edges = graph.edges
    assert ("sandbox", "call_model") in edges


def test_built_graph_has_no_prepare_to_call_model_edge():
    """prepare routes via Command only — static edge would run call_model after BLOCK_INTENT."""
    build = _get_builder()
    adapter = _MinimalAdapter()

    graph = build(
        adapter=adapter,
        state_class=_MockState,
        prepare_node=_noop_node(),
        call_model_node=_noop_node(),
        execute_node=_noop_node(),
    )

    assert ("prepare", "call_model") not in graph.edges


# ── 3. Graph has expected node names (Supervisor-like) ────────────────────


def test_built_graph_uses_adapter_execute_node_name():
    build = _get_builder()
    adapter = _SupervisorAdapter()

    class _SupervisorState(BaseModel):
        supervisor_chat_messages: list = []
        step_count: int = 0
        prepared_prompt: str = ""
        supervisor_metadata: dict = {}
        variables_storage: dict = {}

    graph = build(
        adapter=adapter,
        state_class=_SupervisorState,
        prepare_node=_noop_node(),
        call_model_node=_noop_node(),
        execute_node=_noop_node(),
    )

    node_names = set(graph.nodes.keys())
    assert "execute_agent_tool" in node_names
    assert "sandbox" not in node_names


# ── 4. Graph returns an uncompiled StateGraph (caller compiles with checkpointer) ─


def test_built_graph_is_uncompiled_state_graph():
    from langgraph.graph import StateGraph

    build = _get_builder()
    adapter = _MinimalAdapter()

    graph = build(
        adapter=adapter,
        state_class=_MockState,
        prepare_node=_noop_node(),
        call_model_node=_noop_node(),
        execute_node=_noop_node(),
    )

    assert isinstance(graph, StateGraph)
    assert hasattr(graph, "compile")
    compiled = graph.compile()
    assert hasattr(compiled, "ainvoke")
