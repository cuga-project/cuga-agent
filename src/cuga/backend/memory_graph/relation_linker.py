from __future__ import annotations

from loguru import logger

from .graph import MemoryGraph
from .schemas import MemoryEdge


# Cross-node/cross-section relation discovery is intentionally disabled in the
# active memory-graph pipeline. Source-explicit/local relations are still
# materialized by GraphBuilder from decomposition/normalization output.
#
# The previous LLM-backed global relation discovery implementation is preserved
# in ``relation_linker_cross_section_legacy.py`` so it can be restored or
# revisited without reconstructing it from history.


def link_new_nodes(
    graph: MemoryGraph,
    new_node_ids: set[str],
    *,
    max_candidates: int = 12,
) -> list[MemoryEdge]:
    """Do not infer new lateral relations between separately built atomic nodes.

    Historically this hook retrieved candidate atoms from the existing graph and
    asked an LLM to infer relations such as PRECEDES, ENABLES, QUALIFIES, and
    EQUIVALENT_TO across source regions. Those inferred edges could introduce
    speculative cross-section bridges and then amplify during verifier traversal.

    The hook remains as a compatibility no-op because callers already invoke
    ``link_new_nodes`` after graph updates. Keeping the API stable avoids changes
    outside the memory-graph package while ensuring that only relations already
    extracted from the source/decomposition pipeline are retained.
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
