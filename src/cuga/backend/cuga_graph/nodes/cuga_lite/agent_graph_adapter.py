"""Backward-compatible re-export of AgentGraphAdapter.

Implementation lives in ``cuga_lite.adapter`` (graph_adapter, prepare_node, sandbox_node).
"""

from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.graph_adapter import AgentGraphAdapter

__all__ = ["AgentGraphAdapter"]
