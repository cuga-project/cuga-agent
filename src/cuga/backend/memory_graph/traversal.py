from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from .graph import MemoryGraph, MemoryGraphError
from .schemas import MemoryEdge, MemoryNode, RelationType


# Relations that identify another representation/reference of effectively the
# same proposition. Traversing one of these does not consume bounded traversal
# depth, but it also does NOT restore already-consumed budget. If A reaches B at
# bounded depth 1 and B is equivalent/coreferential with C, C is reached at
# bounded depth 1 as well.
#
# SAME_ENTITY and SAME_EVENT are intentionally excluded: sharing an entity/event
# is too broad to justify recursive evidence expansion.
ZERO_COST_RESET_RELATIONS = frozenset(
    {
        RelationType.COREFERS_WITH,
        RelationType.EQUIVALENT_TO,
    }
)

# Directed relations are traversed according to the context needed to understand
# the currently selected proposition, rather than simply treating every incident
# edge as bidirectional.
#
# REQUIRES is encoded as dependent -> prerequisite, so from the dependent we
# follow the outgoing edge to its prerequisite.
OUTGOING_CONTEXT_RELATIONS = frozenset(
    {
        RelationType.REQUIRES,
    }
)

# These relations are encoded from supporting/qualifying/enabling/earlier/causal
# context toward the proposition they inform. From the current proposition we
# therefore follow the incoming edge back to that context.
INCOMING_CONTEXT_RELATIONS = frozenset(
    {
        RelationType.SUPPORTS,
        RelationType.QUALIFIES,
        RelationType.SERVES_GOAL,
        RelationType.IMPLIES,
        RelationType.ENABLES,
        RelationType.PRECEDES,
        RelationType.CAUSES,
        RelationType.SUPERSEDES,
    }
)

# Hard logical relations that should be followed to closure rather than stopped
# by an arbitrary hop count. They do not consume bounded traversal depth.
CLOSURE_RELATIONS = frozenset(
    {
        RelationType.IMPLIES,
        RelationType.REQUIRES,
        RelationType.SUPERSEDES,
    }
)

# Softer reasoning/context relations use bounded expansion. The value is the
# maximum bounded depth at which an edge of that relation may be traversed.
#
# Bounded depth is global along a path since the most recent zero-cost reset.
# For example:
#
#   A --ENABLES--> B --RELATED_TO--> C
#
# reaches B at depth 1. RELATED_TO has a limit of 1, so C is not reached.
#
# But:
#
#   A --RELATED_TO--> B --ENABLES--> C
#
# reaches B at depth 1 and C at depth 2 because ENABLES allows depth 2.
DEFAULT_BOUNDED_HOPS: Mapping[RelationType, int] = MappingProxyType(
    {
        RelationType.ENABLES: 2,
        RelationType.PRECEDES: 2,
        RelationType.QUALIFIES: 2,
        RelationType.CAUSES: 2,
        RelationType.SUPPORTS: 2,
        RelationType.CONTRADICTS: 1,
        RelationType.SERVES_GOAL: 1,
    }
)


@dataclass(frozen=True)
class TraversalConfig:
    """Configuration for evidence mini-graph expansion."""

    max_nodes_per_mini_graph: int = 50
    bounded_hops: Mapping[RelationType, int] = field(
        default_factory=lambda: DEFAULT_BOUNDED_HOPS
    )
    zero_cost_reset_relations: frozenset[RelationType] = (
        ZERO_COST_RESET_RELATIONS
    )
    closure_relations: frozenset[RelationType] = CLOSURE_RELATIONS

    def __post_init__(self) -> None:
        if self.max_nodes_per_mini_graph <= 0:
            raise ValueError(
                "max_nodes_per_mini_graph must be positive"
            )

        overlap = (
            set(self.zero_cost_reset_relations)
            & set(self.closure_relations)
        )
        if overlap:
            raise ValueError(
                "A relation cannot be both zero-cost-reset and closure: "
                + ", ".join(
                    sorted(relation.value for relation in overlap)
                )
            )

        for relation, limit in self.bounded_hops.items():
            if limit <= 0:
                raise ValueError(
                    f"Traversal hop limit for {relation.value!r} "
                    "must be positive"
                )

        configured = (
            set(self.zero_cost_reset_relations)
            | set(self.closure_relations)
            | set(self.bounded_hops)
        )

        if RelationType.DECOMPOSES_INTO in configured:
            raise ValueError(
                "DECOMPOSES_INTO is hierarchical and cannot be a "
                "lateral traversal relation"
            )


@dataclass(frozen=True)
class TraversalStep:
    """One accepted traversal step used to construct a mini-graph."""

    from_node_id: str
    to_node_id: str
    edge_id: str
    relation: RelationType
    bounded_depth_before: int
    bounded_depth_after: int
    reset_applied: bool
    closure_applied: bool


@dataclass(frozen=True)
class EvidenceMiniGraph:
    """One coverage-aware evidence neighborhood rooted at an anchor.

    ``node_depths`` stores the best bounded depth at which each selected node
    was reached. Lower depth means more bounded traversal budget remains.
    """

    anchor_id: str
    node_ids: tuple[str, ...]
    edge_ids: tuple[str, ...]
    steps: tuple[TraversalStep, ...]
    truncated: bool = False
    node_depths: tuple[tuple[str, int], ...] = ()

    def contains(self, node_id: str) -> bool:
        return node_id in self.node_ids

    def best_depth(self, node_id: str) -> int | None:
        for current_node_id, depth in self.node_depths:
            if current_node_id == node_id:
                return depth
        return None


@dataclass(frozen=True)
class TraversalBatchResult:
    """Mini-graphs produced from a ranked sequence of candidate anchors."""

    mini_graphs: tuple[EvidenceMiniGraph, ...]
    skipped_anchor_ids: tuple[str, ...]
    covered_node_ids: frozenset[str]

    @property
    def truncated(self) -> bool:
        return any(mini_graph.truncated for mini_graph in self.mini_graphs)

    @property
    def all_node_ids(self) -> frozenset[str]:
        return self.covered_node_ids

    @property
    def all_edge_ids(self) -> frozenset[str]:
        return frozenset(
            edge_id
            for mini_graph in self.mini_graphs
            for edge_id in mini_graph.edge_ids
        )


def build_coverage_aware_mini_graphs(
    graph: MemoryGraph,
    ranked_anchor_ids: list[str],
    *,
    config: TraversalConfig | None = None,
) -> TraversalBatchResult:
    """Expand ranked anchors while skipping anchors already covered.

    ``ranked_anchor_ids`` is expected to come from deterministic top-k
    retrieval in descending relevance order.

    Example:

        ranked anchors = [A, B, C, D]

        expand(A) -> {A, B, C, X}

    B and C are skipped as roots only if A's expansion already reached them
    at bounded depth 0. If A reached B at depth 1, B is still expanded as its
    own root because a fresh root starts at depth 0 and therefore has more
    remaining traversal budget.

    Skipping an anchor only prevents expansion that cannot improve traversal
    reachability. Nodes covered through an earlier mini-graph remain part of
    the final evidence set.
    """
    traversal_config = config or TraversalConfig()

    covered_node_ids: set[str] = set()
    skipped_anchor_ids: list[str] = []
    mini_graphs: list[EvidenceMiniGraph] = []

    # Best depth at which each node has already been covered by an earlier
    # ranked-anchor expansion. Lower is better because it leaves more bounded
    # traversal budget available from that node.
    best_covered_depth_by_node: dict[str, int] = {}

    seen_anchor_ids: set[str] = set()

    for anchor_id in ranked_anchor_ids:
        if anchor_id in seen_anchor_ids:
            continue
        seen_anchor_ids.add(anchor_id)

        if anchor_id not in graph.nodes:
            raise MemoryGraphError(
                f"Unknown traversal anchor: {anchor_id}"
            )

        # A fresh anchor expansion always starts at bounded depth 0. Therefore
        # it is redundant only if an earlier mini-graph already reached this
        # node at depth 0. If the node was previously reached at depth > 0,
        # expanding it as a root gives it more remaining traversal budget and
        # may discover additional evidence.
        previous_best_depth = best_covered_depth_by_node.get(anchor_id)
        if previous_best_depth == 0:
            skipped_anchor_ids.append(anchor_id)
            continue

        mini_graph = expand_evidence_neighborhood(
            graph,
            anchor_id,
            config=traversal_config,
        )
        mini_graphs.append(mini_graph)
        covered_node_ids.update(mini_graph.node_ids)

        for node_id, depth in mini_graph.node_depths:
            previous_depth = best_covered_depth_by_node.get(node_id)
            if previous_depth is None or depth < previous_depth:
                best_covered_depth_by_node[node_id] = depth

    return TraversalBatchResult(
        mini_graphs=tuple(mini_graphs),
        skipped_anchor_ids=tuple(skipped_anchor_ids),
        covered_node_ids=frozenset(covered_node_ids),
    )


def expand_evidence_neighborhood(
    graph: MemoryGraph,
    anchor_id: str,
    *,
    config: TraversalConfig | None = None,
) -> EvidenceMiniGraph:
    """Expand one relation-sensitive lateral evidence neighborhood.

    Traversal respects semantic edge direction. Symmetric edges may be followed
    either way. For directed edges, only the direction that provides context for
    the current proposition is followed: REQUIRES follows dependent ->
    prerequisite, while SUPPORTS/QUALIFIES/SERVES_GOAL/IMPLIES/ENABLES/PRECEDES/
    CAUSES/SUPERSEDES are followed from their target back to their source.

    Depth semantics:

    - COREFERS_WITH / EQUIVALENT_TO are zero-cost semantic jumps. They preserve
      the current bounded depth; they never reset consumed budget.

    - SAME_EVENT / SAME_ENTITY / RELATED_TO are not traversed.

    - IMPLIES / REQUIRES / SUPERSEDES are followed to closure without consuming
      bounded depth.

    - ENABLES / PRECEDES / QUALIFIES / CAUSES / SUPPORTS may expand to bounded
      depth 2 by default.

    - CONTRADICTS / SERVES_GOAL are more local and default to depth 1.

    A node may be re-expanded if it is later reached with a lower bounded depth.
    Lower depth means the new path leaves more bounded traversal budget.
    """
    traversal_config = config or TraversalConfig()

    if anchor_id not in graph.nodes:
        raise MemoryGraphError(
            f"Unknown traversal anchor: {anchor_id}"
        )

    selected_node_ids: set[str] = {anchor_id}
    selected_edge_ids: set[str] = set()
    steps: list[TraversalStep] = []

    # Lowest bounded depth at which each node has been reached. Lower is better
    # because it leaves more budget for bounded relations.
    best_depth_by_node: dict[str, int] = {anchor_id: 0}

    queue: deque[tuple[str, int]] = deque([(anchor_id, 0)])
    truncated = False

    while queue:
        node_id, bounded_depth = queue.popleft()

        # The same node can remain queued after a better (lower-depth) path reaches it.
        # Ignore stale queue states.
        if bounded_depth != best_depth_by_node.get(node_id):
            continue

        incident_edges = sorted(
            graph.lateral_edges(node_id),
            key=lambda edge: _edge_priority(
                edge,
                traversal_config,
            ),
        )

        for edge in incident_edges:
            neighbor_id = _contextual_neighbor(edge, node_id)
            if neighbor_id is None:
                continue

            next_depth, reset_applied, closure_applied = (
                _next_bounded_depth(
                    edge.relation,
                    bounded_depth,
                    traversal_config,
                )
            )

            if next_depth is None:
                continue

            is_new_node = neighbor_id not in selected_node_ids

            if (
                is_new_node
                and len(selected_node_ids)
                >= traversal_config.max_nodes_per_mini_graph
            ):
                truncated = True
                continue

            selected_node_ids.add(neighbor_id)
            selected_edge_ids.add(edge.id)

            previous_best = best_depth_by_node.get(neighbor_id)

            step = TraversalStep(
                from_node_id=node_id,
                to_node_id=neighbor_id,
                edge_id=edge.id,
                relation=edge.relation,
                bounded_depth_before=bounded_depth,
                bounded_depth_after=next_depth,
                reset_applied=reset_applied,
                closure_applied=closure_applied,
            )

            # Keep the traversal log focused on paths that actually improve
            # reachability. An edge between already-covered nodes remains in
            # edge_ids but does not need a duplicate expansion step.
            if previous_best is None or next_depth < previous_best:
                best_depth_by_node[neighbor_id] = next_depth
                steps.append(step)
                queue.append((neighbor_id, next_depth))

    return EvidenceMiniGraph(
        anchor_id=anchor_id,
        node_ids=tuple(sorted(selected_node_ids)),
        edge_ids=tuple(sorted(selected_edge_ids)),
        steps=tuple(steps),
        truncated=truncated,
        node_depths=tuple(sorted(best_depth_by_node.items())),
    )


def mini_graph_nodes(
    graph: MemoryGraph,
    mini_graph: EvidenceMiniGraph,
) -> list[MemoryNode]:
    """Materialize mini-graph node IDs into MemoryNode objects."""
    return [
        graph.get_node(node_id)
        for node_id in mini_graph.node_ids
    ]


def mini_graph_edges(
    graph: MemoryGraph,
    mini_graph: EvidenceMiniGraph,
) -> list[MemoryEdge]:
    """Materialize mini-graph edge IDs into MemoryEdge objects."""
    return [
        graph.edges[edge_id]
        for edge_id in mini_graph.edge_ids
    ]


def _next_bounded_depth(
    relation: RelationType,
    current_depth: int,
    config: TraversalConfig,
) -> tuple[int | None, bool, bool]:
    """Return next depth plus traversal-mode flags.

    A None depth means that relation is not traversable from the current state.
    """
    if relation in config.zero_cost_reset_relations:
        # Zero-cost identity/reference hops preserve the budget already consumed
        # on the path. They do not grant a fresh traversal radius.
        return current_depth, False, False

    if relation in config.closure_relations:
        return current_depth, False, True

    limit = config.bounded_hops.get(relation)
    if limit is None:
        return None, False, False

    next_depth = current_depth + 1
    if next_depth > limit:
        return None, False, False

    return next_depth, False, False



def _contextual_neighbor(
    edge: MemoryEdge,
    node_id: str,
) -> str | None:
    """Return the semantically useful neighbor for traversal from ``node_id``.

    Symmetric relations may be traversed in either direction. Directed relations
    are intentionally relation-sensitive:

    - REQUIRES: dependent -> prerequisite (outgoing from the current node).
    - SUPPORTS/QUALIFIES/SERVES_GOAL/IMPLIES/ENABLES/PRECEDES/CAUSES/SUPERSEDES:
      target -> source (incoming context for the current node).

    Relations not listed in either directional policy are not traversed when the
    stored edge is directed. This keeps future relation additions conservative.
    """
    if edge.source_id != node_id and edge.target_id != node_id:
        raise MemoryGraphError(
            f"Edge {edge.id} is not incident to node {node_id}"
        )

    if not edge.directed:
        return _other_endpoint(edge, node_id)

    relation = edge.relation

    if relation in OUTGOING_CONTEXT_RELATIONS:
        if edge.source_id == node_id:
            return edge.target_id
        return None

    if relation in INCOMING_CONTEXT_RELATIONS:
        if edge.target_id == node_id:
            return edge.source_id
        return None

    return None

def _other_endpoint(
    edge: MemoryEdge,
    node_id: str,
) -> str:
    if edge.source_id == node_id:
        return edge.target_id

    if edge.target_id == node_id:
        return edge.source_id

    raise MemoryGraphError(
        f"Edge {edge.id} is not incident to node {node_id}"
    )


def _edge_priority(
    edge: MemoryEdge,
    config: TraversalConfig,
) -> tuple[int, int, str, str]:
    """Deterministic traversal ordering favoring stronger relations first."""
    relation = edge.relation

    if relation in config.zero_cost_reset_relations:
        return (0, 0, relation.value, edge.id)

    if relation in config.closure_relations:
        return (1, 0, relation.value, edge.id)

    bounded_limit = config.bounded_hops.get(relation)
    if bounded_limit is not None:
        # Prefer the bounded relations with larger permitted reach.
        return (2, -bounded_limit, relation.value, edge.id)

    # Unknown/non-traversed lateral relations sort last. They will subsequently
    # be ignored by _next_bounded_depth.
    return (3, 0, relation.value, edge.id)