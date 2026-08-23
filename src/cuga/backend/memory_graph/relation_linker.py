from __future__ import annotations

from loguru import logger

from .graph import MemoryGraph
from .model_wrapper import call_relation_model
from .retrieval import rank_nodes
from .schemas import (
    CreationMethod,
    MemoryEdge,
    MemoryNode,
    NodeKind,
    RelationBuildRequest,
    RelationCandidate,
    RelationDirection,
    RelationType,
)


# Relation discovery remains bounded, but candidate selection is no longer
# purely semantic. Lossless decomposition gives us hierarchy/provenance signals
# that should be used to guarantee that directly related clauses are shown to
# the relation model even when lexical/embedding ranking is imperfect.
_MAX_RELATION_CANDIDATES = 12

# Reserve part of the bounded candidate budget for structurally related atoms.
# The remainder is filled by the shared semantic retrieval layer.
_MAX_SAME_PARENT_CANDIDATES = 6
_MAX_SOURCE_NEIGHBOR_CANDIDATES = 4


_SYMMETRIC_RELATIONS = {
    RelationType.RELATED_TO,
    RelationType.EQUIVALENT_TO,
    RelationType.COREFERS_WITH,
    RelationType.SAME_ENTITY,
    RelationType.SAME_EVENT,
    RelationType.CONTRADICTS,
}


def link_new_nodes(
    graph: MemoryGraph,
    new_node_ids: set[str],
    *,
    max_candidates: int = _MAX_RELATION_CANDIDATES,
) -> list[MemoryEdge]:
    """Discover and insert lateral relations involving newly added atomic nodes.

    Only pairs containing at least one node from ``new_node_ids`` are considered.

    For new-to-new pairs, each unordered pair is considered once.
    For new-to-existing pairs, the newly added node is always the anchor.

    Candidate selection deliberately combines two signals:

    1. structural/provenance proximity from the decomposition graph;
    2. deterministic semantic retrieval from ``rank_nodes``.

    This prevents source-neighboring clauses such as a procedure and the
    prerequisite it establishes from disappearing merely because one of them
    falls outside a purely semantic top-k neighborhood.

    The persistent lateral graph remains atomic-only. Composite nodes are never
    passed to the relation model and never become lateral edge endpoints.
    """
    if max_candidates <= 0:
        raise ValueError("max_candidates must be positive")

    if not new_node_ids:
        return []

    unknown_ids = sorted(
        node_id
        for node_id in new_node_ids
        if node_id not in graph.nodes
    )
    if unknown_ids:
        raise ValueError(
            "Cannot link unknown graph node IDs: "
            + ", ".join(unknown_ids)
        )

    new_atoms = sorted(
        (
            graph.nodes[node_id]
            for node_id in new_node_ids
            if graph.nodes[node_id].kind == NodeKind.ATOMIC_FACT
        ),
        key=lambda node: node.id,
    )
    if not new_atoms:
        return []

    all_atoms = sorted(
        (
            node
            for node in graph.nodes.values()
            if node.kind == NodeKind.ATOMIC_FACT
        ),
        key=lambda node: node.id,
    )

    new_atom_ids = {node.id for node in new_atoms}
    existing_atom_ids = {
        node.id
        for node in all_atoms
        if node.id not in new_atom_ids
    }

    inserted_edges: list[MemoryEdge] = []

    for anchor_index, anchor in enumerate(new_atoms):
        # Old nodes may all be considered against this new anchor.
        old_candidates = [
            node
            for node in all_atoms
            if node.id in existing_atom_ids
            and not _pair_already_linked(graph, anchor.id, node.id)
        ]

        # A new-to-new pair is considered only once. Using the stable sorted
        # order avoids duplicate LLM work without assigning semantic meaning to
        # UUID ordering.
        later_new_candidates = [
            node
            for node in new_atoms[anchor_index + 1 :]
            if not _pair_already_linked(graph, anchor.id, node.id)
        ]

        candidates = _select_candidates(
            anchor=anchor,
            candidates=[
                *old_candidates,
                *later_new_candidates,
            ],
            max_candidates=max_candidates,
        )
        if not candidates:
            continue

        request = RelationBuildRequest(
            anchor_node_id=anchor.id,
            anchor_content=anchor.content,
            anchor_routing_text=anchor.routing_text,
            anchor_proposition=anchor.proposition,
            candidates=[
                _to_relation_candidate(node)
                for node in candidates
            ],
        )

        logger.debug(
            "Linking lateral relations for anchor_node_id={} candidates={} "
            "same_parent={} source_neighbors={}",
            anchor.id,
            len(candidates),
            sum(1 for node in candidates if _shares_parent(anchor, node)),
            sum(
                1
                for node in candidates
                if _source_span_distance(anchor, node) is not None
            ),
        )

        response = call_relation_model(request)

        for decision in response.relations:
            other = graph.nodes.get(decision.other_node_id)
            if other is None or other.kind != NodeKind.ATOMIC_FACT:
                # model_wrapper already filters unknown IDs; keep the graph-side
                # guard because graph mutation should never trust model output.
                continue

            source_id, target_id, directed = _resolve_edge_direction(
                anchor=anchor,
                other=other,
                relation=decision.relation,
                direction=decision.direction,
            )

            edge = graph.add_lateral_edge(
                source_id=source_id,
                target_id=target_id,
                relation=decision.relation,
                directed=directed,
                confidence=decision.confidence,
                creation_method=CreationMethod.MODEL_INFERRED,
                evidence_node_ids=[source_id, target_id],
                metadata={
                    "construction": "relation_linker",
                    "anchor_node_id": anchor.id,
                    "candidate_selection": _candidate_selection_reason(
                        anchor,
                        other,
                    ),
                },
            )

            if edge is not None:
                inserted_edges.append(edge)

    logger.info(
        "Lateral relation linking complete: new_atomic_nodes={} "
        "inserted_edges={}",
        len(new_atoms),
        len(inserted_edges),
    )

    return inserted_edges


def _select_candidates(
    *,
    anchor: MemoryNode,
    candidates: list[MemoryNode],
    max_candidates: int,
) -> list[MemoryNode]:
    """Select a bounded local neighborhood without losing source structure.

    Selection order:

    1. atomic siblings sharing an immediate decomposition parent;
    2. nearest atoms with traceable source spans from the same source;
    3. semantic/embedding ranking over all remaining candidates.

    Structural candidates are still ranked semantically *within their bucket*.
    The final result is deterministic and contains no duplicate node IDs.
    """
    if not candidates:
        return []

    unique_candidates = {
        node.id: node
        for node in candidates
        if node.id != anchor.id
        and node.kind == NodeKind.ATOMIC_FACT
    }
    pool = list(unique_candidates.values())
    if not pool:
        return []

    selected: list[MemoryNode] = []
    selected_ids: set[str] = set()

    def add(nodes: list[MemoryNode], limit: int) -> None:
        if limit <= 0:
            return
        for node in nodes:
            if len(selected) >= max_candidates or limit <= 0:
                break
            if node.id in selected_ids:
                continue
            selected.append(node)
            selected_ids.add(node.id)
            limit -= 1

    # 1) Direct hierarchy siblings. These are the strongest deterministic clue
    # that two atoms came from the same operative source clause/section.
    same_parent = [
        node
        for node in pool
        if _shares_parent(anchor, node)
    ]
    if same_parent:
        ranked_same_parent = rank_nodes(
            anchor,
            same_parent,
            top_k=min(
                len(same_parent),
                max_candidates,
                _MAX_SAME_PARENT_CANDIDATES,
            ),
        )
        add(
            [item.node for item in ranked_same_parent],
            min(_MAX_SAME_PARENT_CANDIDATES, max_candidates),
        )

    # 2) Nearby source spans. This catches atoms produced under different
    # intermediate composites while still being grounded in adjacent/overlapping
    # source text. Exact and conservatively inherited spans both remain useful
    # for neighborhood construction.
    source_neighbors = [
        node
        for node in pool
        if node.id not in selected_ids
        and _source_span_distance(anchor, node) is not None
    ]
    source_neighbors.sort(
        key=lambda node: (
            _source_span_distance(anchor, node),
            node.id,
        )
    )
    add(
        source_neighbors,
        min(
            _MAX_SOURCE_NEIGHBOR_CANDIDATES,
            max_candidates - len(selected),
        ),
    )

    # 3) Fill the remaining bounded neighborhood with shared semantic retrieval.
    remaining = [
        node
        for node in pool
        if node.id not in selected_ids
    ]
    remaining_slots = max_candidates - len(selected)
    if remaining and remaining_slots > 0:
        ranked = rank_nodes(
            anchor,
            remaining,
            top_k=remaining_slots,
        )
        add([item.node for item in ranked], remaining_slots)

    return selected


def _shares_parent(left: MemoryNode, right: MemoryNode) -> bool:
    """Whether two materialized nodes share an immediate hierarchy parent."""
    if not left.support_node_ids or not right.support_node_ids:
        return False
    return bool(
        set(left.support_node_ids)
        & set(right.support_node_ids)
    )


def _source_span_distance(
    left: MemoryNode,
    right: MemoryNode,
) -> int | None:
    """Minimum root-source character distance between two grounded nodes.

    Returns 0 for overlapping spans. ``None`` means there is no comparable
    source reference (different source IDs/types or missing spans).
    """
    best: int | None = None

    for left_ref in left.source_refs:
        if left_ref.span is None:
            continue
        for right_ref in right.source_refs:
            if right_ref.span is None:
                continue
            if left_ref.source_id != right_ref.source_id:
                continue
            if left_ref.source_type != right_ref.source_type:
                continue

            left_start = left_ref.span.start
            left_end = left_ref.span.end
            right_start = right_ref.span.start
            right_end = right_ref.span.end

            if left_end >= right_start and right_end >= left_start:
                distance = 0
            elif left_end < right_start:
                distance = right_start - left_end
            else:
                distance = left_start - right_end

            if best is None or distance < best:
                best = distance

    return best


def _candidate_selection_reason(
    anchor: MemoryNode,
    candidate: MemoryNode,
) -> str:
    if _shares_parent(anchor, candidate):
        return "same_parent"
    if _source_span_distance(anchor, candidate) is not None:
        return "source_neighbor"
    return "semantic_rank"


def _to_relation_candidate(node: MemoryNode) -> RelationCandidate:
    return RelationCandidate(
        node_id=node.id,
        content=node.content,
        routing_text=node.routing_text,
        proposition=node.proposition,
    )


def _pair_already_linked(
    graph: MemoryGraph,
    left_id: str,
    right_id: str,
) -> bool:
    """Whether any lateral relation already exists between this node pair.

    A source-explicit edge emitted directly by decomposition should not be
    needlessly reclassified by the global linker. Likewise, a pair already
    classified by this linker is not repeatedly sent to the model.
    """
    return (
        graph.has_lateral_edge(
            source_id=left_id,
            target_id=right_id,
        )
        or graph.has_lateral_edge(
            source_id=right_id,
            target_id=left_id,
        )
    )


def _resolve_edge_direction(
    *,
    anchor: MemoryNode,
    other: MemoryNode,
    relation: RelationType,
    direction: RelationDirection,
) -> tuple[str, str, bool]:
    """Translate semantic relation direction into graph edge endpoints."""
    if relation == RelationType.DECOMPOSES_INTO:
        raise ValueError(
            "decomposes_into cannot be materialized as a lateral relation"
        )

    if relation in _SYMMETRIC_RELATIONS:
        source_id, target_id = sorted((anchor.id, other.id))
        return source_id, target_id, False

    if direction == RelationDirection.ANCHOR_TO_CANDIDATE:
        return anchor.id, other.id, True

    if direction == RelationDirection.CANDIDATE_TO_ANCHOR:
        return other.id, anchor.id, True

    raise ValueError(
        f"Directional relation {relation.value!r} cannot use "
        f"direction={direction.value!r}"
    )