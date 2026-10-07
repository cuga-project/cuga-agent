from __future__ import annotations

from loguru import logger

from .graph import MemoryGraph
from .schemas import MemoryEdge


# Cross-source relation inference is disabled. GraphBuilder retains relations
# explicitly supported by each source's decomposition and normalization.


def link_new_nodes(
    graph: MemoryGraph,
    new_node_ids: set[str],
    *,
    max_candidates: int = 12,
) -> list[MemoryEdge]:
    """Do not infer new lateral relations between separately built atomic nodes.

    Retain this hook because graph-update callers invoke it. Inferring lateral
    relations between independent sources could create unsupported links that
    later appear as verifier evidence.
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

    logger.debug(
        "Cross-section relation linking disabled: new_nodes={} existing_nodes={} ",
        len(new_node_ids),
        len(graph.nodes),
    )
    return []
