"""Partition candidate atoms into contiguous verification bulks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from cuga.backend.memory_graph import MemoryGraph, NodeKind

from .errors import PromptVerificationError


def _one_line(text: str) -> str:
    """Normalize a statement for the line-oriented verifier projection."""
    return re.sub(r"\s+", " ", str(text or "")).strip()


@dataclass(frozen=True)
class _CandidateVerificationBulk:
    """One contiguous Stanza-derived slice of a non-code candidate.

    The candidate graph is still built once for the complete candidate. Bulks
    only control which candidate leaves are allowed to initiate retrieval for one
    verifier call and which untouched source span is shown as RAW_CANDIDATE.
    Nothing is committed between bulks; the original candidate is committed only
    after every bulk has been approved.
    """

    index: int
    start: int
    end: int
    content: str
    atom_ids: tuple[str, ...]
    grouping_node_id: str


def _candidate_atom_order_key(node: Any) -> tuple[int, int, str]:
    starts = [
        ref.span.start for ref in getattr(node, "source_refs", []) if getattr(ref, "span", None) is not None
    ]
    return (min(starts) if starts else 10**12, int(getattr(node, "depth", 0)), node.id)


def _hierarchical_ancestor_ids_including_self(
    graph: MemoryGraph,
    node_id: str,
) -> set[str]:
    """Return hierarchical ancestors of ``node_id`` plus the node itself."""
    result = {node_id}
    frontier = [node_id]
    while frontier:
        current_id = frontier.pop()
        for parent in graph.parents(current_id):
            if parent.id in result:
                continue
            result.add(parent.id)
            frontier.append(parent.id)
    return result


def _node_source_bounds(node: Any) -> tuple[int, int] | None:
    """Return the outer source span for one candidate hierarchy node."""
    spans = [
        (int(ref.span.start), int(ref.span.end))
        for ref in list(getattr(node, "source_refs", []) or [])
        if getattr(ref, "span", None) is not None
    ]
    if not spans:
        return None
    return min(start for start, _ in spans), max(end for _, end in spans)


def _candidate_semantic_ancestor_ids(
    *,
    graph: MemoryGraph,
    node_id: str,
) -> set[str]:
    """Return candidate hierarchy ancestors while excluding RAW_SOURCE."""
    return {
        ancestor_id
        for ancestor_id in _hierarchical_ancestor_ids_including_self(graph, node_id)
        if ancestor_id in graph.nodes and graph.nodes[ancestor_id].kind != NodeKind.RAW_SOURCE
    }


def _candidate_bulk_wrapper_id(
    *,
    graph: MemoryGraph,
    candidate_atoms: list[Any],
) -> str | None:
    """Find the highest Stanza semantic wrapper shared by all candidate leaves.

    Candidate graphs commonly contain one broad composite node spanning the whole
    response, with paragraph/statement-like semantic units beneath it.  We use the
    highest common non-RAW ancestor as that wrapper and form bulks from its direct
    semantic children.  If Stanza produced multiple independent top-level units,
    there is no common semantic wrapper and each atom is assigned to its own
    highest semantic ancestor instead.
    """
    if not candidate_atoms:
        return None

    common_ids: set[str] | None = None
    for atom in candidate_atoms:
        ancestor_ids = _candidate_semantic_ancestor_ids(
            graph=graph,
            node_id=atom.id,
        )
        common_ids = set(ancestor_ids) if common_ids is None else common_ids.intersection(ancestor_ids)
        if not common_ids:
            return None

    assert common_ids is not None
    if not common_ids:
        return None

    # Lowest depth is the broadest/highest semantic node beneath RAW_SOURCE.
    return min(
        common_ids,
        key=lambda node_id: (
            int(getattr(graph.nodes[node_id], "depth", 0)),
            node_id,
        ),
    )


def _candidate_bulk_grouping_node_id(
    *,
    graph: MemoryGraph,
    atom: Any,
    wrapper_id: str | None,
) -> str:
    """Choose the Stanza hierarchy node whose source span defines one bulk."""
    semantic_ancestor_ids = _candidate_semantic_ancestor_ids(
        graph=graph,
        node_id=atom.id,
    )

    if wrapper_id is None:
        # Multiple top-level Stanza units: use the highest semantic ancestor for
        # this leaf. This remains fully decomposition-driven and preserves order.
        return min(
            semantic_ancestor_ids,
            key=lambda node_id: (
                int(getattr(graph.nodes[node_id], "depth", 0)),
                node_id,
            ),
        )

    if atom.id == wrapper_id:
        return atom.id

    # Prefer an actual direct hierarchy child of the shared wrapper.  This is the
    # paragraph/composite boundary Stanza already created, without inventing a new
    # semantic classifier or fixed atom-count window.
    direct_children: list[str] = []
    for node_id in semantic_ancestor_ids:
        if node_id == wrapper_id:
            continue
        parents = list(graph.parents(node_id))
        if any(parent.id == wrapper_id for parent in parents):
            direct_children.append(node_id)

    if direct_children:
        return min(
            direct_children,
            key=lambda node_id: (
                int(getattr(graph.nodes[node_id], "depth", 0)),
                node_id,
            ),
        )

    # Defensive fallback for a non-tree hierarchy: choose the semantic ancestor
    # closest below the wrapper by depth.  The atom itself is always available.
    wrapper_depth = int(getattr(graph.nodes[wrapper_id], "depth", 0))
    below_wrapper = [
        node_id
        for node_id in semantic_ancestor_ids
        if node_id != wrapper_id and int(getattr(graph.nodes[node_id], "depth", 0)) > wrapper_depth
    ]
    if not below_wrapper:
        return atom.id
    return min(
        below_wrapper,
        key=lambda node_id: (
            int(getattr(graph.nodes[node_id], "depth", 0)),
            node_id,
        ),
    )


def _build_candidate_verification_bulks(
    *,
    candidate: str,
    candidate_graph: MemoryGraph,
    candidate_atoms: list[Any],
) -> list[_CandidateVerificationBulk]:
    """Build contiguous Stanza-derived verification bulks in source order.

    No candidate text is regenerated. Stanza decides the semantic grouping; the
    verifier sees the corresponding untouched original source slice. Candidate
    leaves remain the retrieval queries for that bulk.
    """
    ordered_atoms = sorted(candidate_atoms, key=_candidate_atom_order_key)
    if not ordered_atoms:
        return []

    wrapper_id = _candidate_bulk_wrapper_id(
        graph=candidate_graph,
        candidate_atoms=ordered_atoms,
    )

    provisional: list[dict[str, Any]] = []
    for atom in ordered_atoms:
        grouping_node_id = _candidate_bulk_grouping_node_id(
            graph=candidate_graph,
            atom=atom,
            wrapper_id=wrapper_id,
        )
        if provisional and provisional[-1]["grouping_node_id"] == grouping_node_id:
            provisional[-1]["atoms"].append(atom)
        else:
            provisional.append(
                {
                    "grouping_node_id": grouping_node_id,
                    "atoms": [atom],
                }
            )

    bulks: list[_CandidateVerificationBulk] = []
    candidate_length = len(candidate)
    for index, group in enumerate(provisional, start=1):
        grouping_node_id = str(group["grouping_node_id"])
        grouping_node = candidate_graph.nodes.get(grouping_node_id)
        bounds = _node_source_bounds(grouping_node) if grouping_node is not None else None

        atom_bounds = [_node_source_bounds(atom) for atom in group["atoms"]]
        atom_bounds = [item for item in atom_bounds if item is not None]

        if bounds is None:
            if not atom_bounds:
                raise PromptVerificationError(
                    "Could not determine an original source span for a Stanza "
                    f"candidate bulk: grouping_node_id={grouping_node_id}"
                )
            start = min(item[0] for item in atom_bounds)
            end = max(item[1] for item in atom_bounds)
        else:
            start, end = bounds
            if atom_bounds:
                atom_start = min(item[0] for item in atom_bounds)
                atom_end = max(item[1] for item in atom_bounds)
                # Never let a malformed/incomplete composite source span omit a
                # leaf whose retrieval evidence is being judged in this bulk.
                start = min(start, atom_start)
                end = max(end, atom_end)

        start = max(0, min(start, candidate_length))
        end = max(start, min(end, candidate_length))
        while start < end and candidate[start].isspace():
            start += 1
        while end > start and candidate[end - 1].isspace():
            end -= 1

        bulk_content = candidate[start:end]
        if not bulk_content.strip():
            raise PromptVerificationError(
                "Stanza candidate bulk resolved to an empty source slice: "
                f"grouping_node_id={grouping_node_id} span=({start}, {end})"
            )

        bulks.append(
            _CandidateVerificationBulk(
                index=index,
                start=start,
                end=end,
                content=bulk_content,
                atom_ids=tuple(atom.id for atom in group["atoms"]),
                grouping_node_id=grouping_node_id,
            )
        )

    return bulks


def _covers_partition(
    *,
    graph: MemoryGraph,
    candidate_ancestor_id: str,
    partition_node_ids: set[str],
) -> bool:
    """Whether one hierarchy node covers every selected node in the partition."""
    return all(
        candidate_ancestor_id in _hierarchical_ancestor_ids_including_self(graph, node_id)
        for node_id in partition_node_ids
    )
