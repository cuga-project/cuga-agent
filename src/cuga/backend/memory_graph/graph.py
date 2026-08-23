from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from .schemas import (
    CreationMethod,
    EdgeFamily,
    GraphBuildResult,
    MemoryEdge,
    MemoryNode,
    NodeKind,
    NodeStatus,
    RelationType,
)


class MemoryGraphError(ValueError):
    pass


class MemoryGraph:
    """Small in-memory graph container for applying local graph-build results."""

    def __init__(self) -> None:
        self.nodes: dict[str, MemoryNode] = {}
        self.edges: dict[str, MemoryEdge] = {}
        self._outgoing: dict[str, set[str]] = defaultdict(set)
        self._incoming: dict[str, set[str]] = defaultdict(set)

    def apply_build_result(self, result: GraphBuildResult) -> set[str]:
        """Apply one build result and return the node IDs inserted by it."""
        new_node_ids: set[str] = set()

        for node in result.nodes:
            self.add_node(node)
            new_node_ids.add(node.id)

        for edge in result.edges:
            self.add_edge(edge)

        return new_node_ids

    def add_node(self, node: MemoryNode) -> None:
        if node.id in self.nodes:
            raise MemoryGraphError(f"Node already exists: {node.id}")
        self.nodes[node.id] = node

    def add_edge(self, edge: MemoryEdge) -> None:
        if edge.id in self.edges:
            raise MemoryGraphError(f"Edge already exists: {edge.id}")
        if edge.source_id not in self.nodes:
            raise MemoryGraphError(f"Unknown edge source: {edge.source_id}")
        if edge.target_id not in self.nodes:
            raise MemoryGraphError(f"Unknown edge target: {edge.target_id}")
        if edge.source_id == edge.target_id:
            raise MemoryGraphError("Self-edges are not allowed")

        source = self.nodes[edge.source_id]
        target = self.nodes[edge.target_id]

        if edge.family == EdgeFamily.HIERARCHICAL:
            if edge.relation != RelationType.DECOMPOSES_INTO:
                raise MemoryGraphError(
                    "Hierarchical edges must use DECOMPOSES_INTO"
                )
            if target.depth != source.depth + 1:
                raise MemoryGraphError(
                    "Hierarchical edges must connect adjacent decomposition layers"
                )

        elif edge.family == EdgeFamily.LATERAL:
            if edge.relation == RelationType.DECOMPOSES_INTO:
                raise MemoryGraphError(
                    "Lateral edges cannot use DECOMPOSES_INTO"
                )
            if (
                source.kind != NodeKind.ATOMIC_FACT
                or target.kind != NodeKind.ATOMIC_FACT
            ):
                raise MemoryGraphError(
                    "Lateral edges must connect atomic fact nodes"
                )

            # Lateral relations are semantic rather than hierarchical. They may
            # legitimately connect atomic nodes that were produced at different
            # decomposition depths, so no same-depth constraint is imposed here.

        self.edges[edge.id] = edge
        self._outgoing[edge.source_id].add(edge.id)
        self._incoming[edge.target_id].add(edge.id)

        if not edge.directed:
            self._outgoing[edge.target_id].add(edge.id)
            self._incoming[edge.source_id].add(edge.id)

    def add_lateral_edge(
        self,
        *,
        source_id: str,
        target_id: str,
        relation: RelationType,
        directed: bool,
        confidence: float = 1.0,
        traversal_weight: float = 1.0,
        creation_method: CreationMethod = CreationMethod.MODEL_INFERRED,
        evidence_node_ids: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryEdge | None:
        """Create and insert one lateral edge.

        Returns the inserted edge. If an equivalent lateral edge already exists,
        returns ``None`` instead of inserting a duplicate.
        """
        if relation == RelationType.DECOMPOSES_INTO:
            raise MemoryGraphError(
                "DECOMPOSES_INTO is reserved for hierarchical edges"
            )

        # Canonicalize undirected endpoints so equivalent symmetric edges have a
        # stable representation independent of caller ordering.
        if not directed and target_id < source_id:
            source_id, target_id = target_id, source_id

        if self.has_lateral_edge(
            source_id=source_id,
            target_id=target_id,
            relation=relation,
            directed=directed,
        ):
            return None

        edge = MemoryEdge(
            source_id=source_id,
            target_id=target_id,
            family=EdgeFamily.LATERAL,
            relation=relation,
            directed=directed,
            confidence=confidence,
            traversal_weight=traversal_weight,
            creation_method=creation_method,
            evidence_node_ids=list(evidence_node_ids or []),
            metadata=dict(metadata or {}),
        )

        self.add_edge(edge)
        return edge

    def has_lateral_edge(
        self,
        *,
        source_id: str,
        target_id: str,
        relation: RelationType | None = None,
        directed: bool | None = None,
    ) -> bool:
        """Return whether a matching lateral edge already exists.

        When ``relation`` or ``directed`` is omitted, that property is treated
        as a wildcard.
        """
        for edge in self._incident_edges(source_id):
            if edge.family != EdgeFamily.LATERAL:
                continue

            if relation is not None and edge.relation != relation:
                continue
            if directed is not None and edge.directed != directed:
                continue

            if edge.directed:
                if edge.source_id == source_id and edge.target_id == target_id:
                    return True
            else:
                if {
                    edge.source_id,
                    edge.target_id,
                } == {
                    source_id,
                    target_id,
                }:
                    return True

        return False

    def get_node(self, node_id: str) -> MemoryNode:
        """Return one node or raise MemoryGraphError when it is unknown."""
        try:
            return self.nodes[node_id]
        except KeyError as exc:
            raise MemoryGraphError(f"Unknown node: {node_id}") from exc

    def atomic_nodes(
        self,
        *,
        active_only: bool = True,
    ) -> list[MemoryNode]:
        """Return atomic nodes, optionally restricted to active nodes."""
        return [
            node
            for node in self.nodes.values()
            if node.kind == NodeKind.ATOMIC_FACT
            and (not active_only or node.status == NodeStatus.ACTIVE)
        ]

    def children(self, node_id: str) -> list[MemoryNode]:
        return [
            self.nodes[self.edges[edge_id].target_id]
            for edge_id in self._outgoing.get(node_id, set())
            if self.edges[edge_id].family == EdgeFamily.HIERARCHICAL
            and self.edges[edge_id].source_id == node_id
        ]

    def parents(self, node_id: str) -> list[MemoryNode]:
        return [
            self.nodes[self.edges[edge_id].source_id]
            for edge_id in self._incoming.get(node_id, set())
            if self.edges[edge_id].family == EdgeFamily.HIERARCHICAL
            and self.edges[edge_id].target_id == node_id
        ]

    def lateral_edges(
        self,
        node_id: str,
        relation_types: Iterable[RelationType] | None = None,
    ) -> list[MemoryEdge]:
        """Return every lateral edge incident to ``node_id``.

        Edge direction is preserved on each MemoryEdge. This is the preferred
        primitive for traversal because callers can inspect both incoming and
        outgoing relations without losing their original orientation.
        """
        accepted = set(relation_types) if relation_types else None

        return [
            edge
            for edge in self._incident_edges(node_id)
            if edge.family == EdgeFamily.LATERAL
            and (accepted is None or edge.relation in accepted)
        ]

    def outgoing_lateral_edges(
        self,
        node_id: str,
        relation_types: Iterable[RelationType] | None = None,
    ) -> list[MemoryEdge]:
        """Return lateral edges traversable outward from ``node_id``.

        Undirected edges are returned from either endpoint.
        """
        accepted = set(relation_types) if relation_types else None

        return [
            edge
            for edge_id in self._outgoing.get(node_id, set())
            if (edge := self.edges[edge_id]).family == EdgeFamily.LATERAL
            and (accepted is None or edge.relation in accepted)
        ]

    def incoming_lateral_edges(
        self,
        node_id: str,
        relation_types: Iterable[RelationType] | None = None,
    ) -> list[MemoryEdge]:
        """Return lateral edges traversable into ``node_id``.

        Undirected edges are returned from either endpoint.
        """
        accepted = set(relation_types) if relation_types else None

        return [
            edge
            for edge_id in self._incoming.get(node_id, set())
            if (edge := self.edges[edge_id]).family == EdgeFamily.LATERAL
            and (accepted is None or edge.relation in accepted)
        ]

    def lateral_neighbors(
        self,
        node_id: str,
        relation_types: Iterable[RelationType] | None = None,
    ) -> list[MemoryNode]:
        accepted = set(relation_types) if relation_types else None
        neighbor_ids: set[str] = set()

        for edge in self._incident_edges(node_id):
            if edge.family != EdgeFamily.LATERAL:
                continue
            if accepted is not None and edge.relation not in accepted:
                continue
            if edge.source_id == node_id:
                neighbor_ids.add(edge.target_id)
            if edge.target_id == node_id:
                neighbor_ids.add(edge.source_id)

        return [
            self.nodes[neighbor_id]
            for neighbor_id in neighbor_ids
        ]

    def _incident_edges(self, node_id: str) -> list[MemoryEdge]:
        edge_ids = (
            self._outgoing.get(node_id, set())
            | self._incoming.get(node_id, set())
        )
        return [
            self.edges[edge_id]
            for edge_id in edge_ids
        ]