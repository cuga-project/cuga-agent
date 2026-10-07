"""Candidate-conditioned retrieval and Q-statement reconstruction.

The verifier consumes the returned source-level statements, but evidence ranking,
closure, deduplication, and context rendering are owned here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from cuga.backend.memory_graph import MemoryGraph, NodeKind, SourceType
from cuga.backend.memory_graph.retrieval import rank_nodes
from cuga.backend.memory_graph.traversal import (
    TraversalConfig,
    build_coverage_aware_mini_graphs,
)

from .candidate_partition import (
    _candidate_atom_order_key,
    _covers_partition,
    _hierarchical_ancestor_ids_including_self,
    _one_line,
)
from .errors import PromptVerificationError


@dataclass
class _CandidateQueryContextEntry:
    graph_name: str
    source_type: str
    statement_node_id: str
    statement: str
    covered_atomic_node_ids: set[str] = field(default_factory=set)
    triggered_by_candidate_atom_ids: set[str] = field(default_factory=set)
    context_id: str = ""


_VERIFIER_TOP_K = 5


_VERIFIER_TRAVERSAL_CONFIG = TraversalConfig(
    max_nodes_per_mini_graph=50,
)


def _combine_evidence_graphs(
    *graphs: MemoryGraph,
) -> MemoryGraph:
    """Create a temporary disjoint-union view without changing provenance."""
    combined = MemoryGraph()

    for graph in graphs:
        for node in graph.nodes.values():
            if node.id in combined.nodes:
                raise PromptVerificationError(
                    f"Cannot combine evidence graphs with duplicate node ID {node.id}"
                )
            combined.add_node(node)

    for graph in graphs:
        for edge in graph.edges.values():
            if edge.id in combined.edges:
                raise PromptVerificationError(
                    f"Cannot combine evidence graphs with duplicate edge ID {edge.id}"
                )
            combined.add_edge(edge)
        combined.merge_logic_layer(graph.logic_layer)

    return combined


def _select_evidence_for_atom(
    *,
    candidate_atom: Any,
    evidence_graph: MemoryGraph,
    evidence_space: str,
) -> dict[str, Any]:
    """Retrieve top-k anchors and preserve each anchor's expanded mini-graph.

    The mini-graph boundary is semantically important for source reconstruction.
    Expanded nodes are retrieval support for their anchor; they must not later be
    flattened into independent Q statements.
    """
    ranked = rank_nodes(
        candidate_atom,
        evidence_graph.atomic_nodes(active_only=True),
        top_k=_VERIFIER_TOP_K,
    )

    if not ranked:
        return {
            "mini_graphs": [],
            "truncated": False,
        }

    traversal = build_coverage_aware_mini_graphs(
        evidence_graph,
        [item.node.id for item in ranked],
        config=_VERIFIER_TRAVERSAL_CONFIG,
    )

    mini_graphs = [
        {
            "anchor_id": mini_graph.anchor_id,
            "node_ids": list(mini_graph.node_ids),
            "edge_ids": list(mini_graph.edge_ids),
            "node_depths": list(mini_graph.node_depths),
            "truncated": bool(mini_graph.truncated),
        }
        for mini_graph in traversal.mini_graphs
    ]

    return {
        "mini_graphs": mini_graphs,
        "truncated": traversal.truncated,
    }


def _semantic_context_closure_target_id(
    *,
    graph: MemoryGraph,
    node_id: str,
) -> str | None:
    """Resolve one atom's mandatory semantic-context closure target.

    Graph construction marks an atomic leaf when the semantic governor for its
    source unit lies outside the extracted unit.  The marker stores the temporary
    ID of the minimum hierarchy ancestor that encloses that external scope.  This
    function resolves that construction-time ID through the actual ancestor chain
    instead of assuming a fixed number of upward hops.

    Missing metadata means the node is semantically closed under the graph
    version that produced it.  A present-but-unresolvable target is treated as
    graph corruption: silently ignoring it would recreate exactly the evidence
    loss this closure rule is intended to prevent.
    """
    node = graph.nodes.get(node_id)
    if node is None:
        raise PromptVerificationError(f"Cannot resolve semantic-context closure for unknown node {node_id!r}")

    metadata = dict(getattr(node, "metadata", {}) or {})
    if not bool(metadata.get("semantic_context_dependency_external", False)):
        return None

    target_temporary_id = str(metadata.get("semantic_context_closure_ancestor_temporary_id") or "").strip()
    if not target_temporary_id:
        raise PromptVerificationError(
            f"Semantic-context-dependent node is missing its closure ancestor: node_id={node_id}"
        )

    frontier = [node_id]
    visited: set[str] = set()
    while frontier:
        current_id = frontier.pop(0)
        if current_id in visited:
            continue
        visited.add(current_id)

        current = graph.nodes.get(current_id)
        if current is None:
            continue
        if str(current.metadata.get("temporary_id") or "") == target_temporary_id:
            return current_id

        frontier.extend(
            parent.id
            for parent in sorted(
                graph.parents(current_id),
                key=lambda item: (-int(getattr(item, "depth", 0)), item.id),
            )
            if parent.id not in visited
        )

    raise PromptVerificationError(
        "Could not resolve semantic-context closure ancestor through hierarchy: "
        f"node_id={node_id} target_temporary_id={target_temporary_id}"
    )


def _source_sentence_spans(source_text: str) -> list[tuple[int, int]]:
    """Return deterministic source-level sentence/structural-unit spans.

    Atomic graph nodes are retrieval units, not safe evidence units. Reconstruction
    therefore needs a source-text floor that restores at least the complete sentence
    containing each selected atom. We intentionally compute that floor from the
    immutable RAW_SOURCE text and root-relative source spans rather than from the
    atom's decomposed wording.

    Sentence-final punctuation is the primary boundary. Markdown headings, bullets,
    table rows, and blank-line boundaries are also treated as structural sentence
    boundaries so punctuation-free list items do not absorb unrelated following
    content. Existing semantic-context dependency closure remains responsible for
    expanding a self-contained sentence/list item to a larger governing scope when
    necessary.
    """
    text = str(source_text or "")
    if not text:
        return []

    boundaries: set[int] = {0, len(text)}

    # Normal prose sentence endings. Require whitespace/end after punctuation so
    # common inline forms such as ``e.g.,`` do not split at the abbreviation dot.
    for match in re.finditer(r"[.!?](?:[\"')\]]+)?(?=\s|$)", text):
        boundaries.add(match.end())

    # Structural Markdown/source boundaries. A bullet/table/heading line is a
    # complete source unit even when it omits terminal punctuation. Blank lines
    # also separate independent source blocks.
    line_start = 0
    lines = text.splitlines(keepends=True)
    structural_line_re = re.compile(r"^[ \t]{0,3}(?:#{1,6}[ \t]+|[-*+][ \t]+|\d+[.)][ \t]+|\|)")
    for index, line in enumerate(lines):
        line_end = line_start + len(line)
        bare = line.rstrip("\r\n")
        next_line = lines[index + 1] if index + 1 < len(lines) else ""
        if (
            not bare.strip()
            or structural_line_re.match(line) is not None
            or structural_line_re.match(next_line) is not None
        ):
            boundaries.add(line_end)
        line_start = line_end

    ordered = sorted(boundary for boundary in boundaries if 0 <= boundary <= len(text))
    spans: list[tuple[int, int]] = []
    for left, right in zip(ordered, ordered[1:]):
        if left >= right:
            continue
        # Keep root-relative coordinates but trim surrounding whitespace so the
        # required span corresponds to the semantic source sentence itself.
        while left < right and text[left].isspace():
            left += 1
        while right > left and text[right - 1].isspace():
            right -= 1
        if left < right:
            spans.append((left, right))
    return spans


def _source_sentence_spans_for_node(
    *,
    graph: MemoryGraph,
    node_id: str,
) -> list[tuple[str, tuple[int, int]]]:
    """Return complete source-sentence spans touched by one selected node."""
    node = graph.nodes.get(node_id)
    if node is None:
        raise PromptVerificationError(f"Cannot resolve source-sentence closure for unknown node {node_id!r}")

    root = graph.nodes.get(node.source_root_id)
    if root is None:
        raise PromptVerificationError(
            "Cannot resolve source-sentence closure because the source root is "
            f"missing: node_id={node_id} source_root_id={node.source_root_id}"
        )

    source_text = str(root.content or "")
    sentence_spans = _source_sentence_spans(source_text)
    if not sentence_spans:
        return []

    targets: list[tuple[str, tuple[int, int]]] = []
    seen: set[tuple[str, int, int]] = set()
    for source_ref in list(getattr(node, "source_refs", []) or []):
        span = getattr(source_ref, "span", None)
        if span is None:
            continue
        source_id = str(getattr(source_ref, "source_id", "") or "")
        start = int(span.start)
        end = int(span.end)
        for sentence_start, sentence_end in sentence_spans:
            # Exact containment is typical. Overlap is retained as a conservative
            # fallback for an atom whose source span crosses a punctuation boundary.
            overlaps = start < sentence_end and end > sentence_start
            contains_start = sentence_start <= start < sentence_end
            if not overlaps and not contains_start:
                continue
            key = (source_id, sentence_start, sentence_end)
            if key in seen:
                continue
            seen.add(key)
            targets.append((source_id, (sentence_start, sentence_end)))
    return targets


def _node_covers_source_span(
    *,
    node: Any,
    source_id: str,
    target_span: tuple[int, int],
) -> bool:
    """Whether one hierarchy node contains the complete target source span."""
    target_start, target_end = target_span
    for source_ref in list(getattr(node, "source_refs", []) or []):
        span = getattr(source_ref, "span", None)
        if span is None:
            continue
        if source_id and str(getattr(source_ref, "source_id", "") or "") != source_id:
            continue
        if int(span.start) <= target_start and int(span.end) >= target_end:
            return True
    return False


def _source_sentence_closure_target_ids(
    *,
    graph: MemoryGraph,
    node_id: str,
) -> set[str]:
    """Return lowest ancestor IDs that cover every full sentence touched by node."""
    targets: set[str] = set()
    for source_id, sentence_span in _source_sentence_spans_for_node(
        graph=graph,
        node_id=node_id,
    ):
        ancestor_ids = _hierarchical_ancestor_ids_including_self(graph, node_id)
        covering = []
        for ancestor_id in ancestor_ids:
            ancestor = graph.get_node(ancestor_id)
            if _node_covers_source_span(
                node=ancestor,
                source_id=source_id,
                target_span=sentence_span,
            ):
                covering.append(ancestor)
        if not covering:
            raise PromptVerificationError(
                "No hierarchy ancestor covers the selected atom's complete source "
                "sentence: "
                f"node_id={node_id} source_id={source_id!r} "
                f"sentence_span={sentence_span}"
            )

        # Highest depth = lowest/closest hierarchy ancestor that restores the
        # complete source sentence. UUID is only a deterministic tie-breaker.
        target = max(
            covering,
            key=lambda item: (int(getattr(item, "depth", 0)), item.id),
        )
        targets.add(target.id)
    return targets


def _partition_required_cover_ids(
    *,
    graph: MemoryGraph,
    partition_node_ids: set[str],
) -> tuple[set[str], set[str], set[str]]:
    """Return evidence + sentence floor + semantic dependency closure targets."""
    sentence_target_ids = {
        target_id
        for node_id in partition_node_ids
        for target_id in _source_sentence_closure_target_ids(
            graph=graph,
            node_id=node_id,
        )
    }
    semantic_target_ids = {
        target_id
        for node_id in partition_node_ids
        for target_id in [
            _semantic_context_closure_target_id(
                graph=graph,
                node_id=node_id,
            )
        ]
        if target_id is not None
    }
    required = set(partition_node_ids) | sentence_target_ids | semantic_target_ids
    return required, sentence_target_ids, semantic_target_ids


def _closest_covering_ancestor(
    *,
    graph: MemoryGraph,
    partition_node_ids: set[str],
    preferred_start_id: str | None,
) -> Any:
    """Return the lowest hierarchy node satisfying sentence + dependency closure.

    Atomic nodes remain retrieval anchors only. Every selected atom first imposes
    a source-sentence floor: the reconstructed evidence must cover the complete
    original source sentence containing that atom. Construction-time semantic
    dependency metadata may impose an even larger closure target when the sentence
    itself depends on an external governor/list/conditional scope.

    This is deliberately *not* a fixed ``go up one level`` rule. Reconstruction
    climbs only as far as needed to cover selected evidence, complete source
    sentences, and mandatory semantic closure targets. RAW_SOURCE remains legal
    when it is the only common cover.
    """
    if not partition_node_ids:
        raise PromptVerificationError("Cannot reconstruct an empty mini-graph partition")

    (
        required_cover_ids,
        sentence_target_ids,
        semantic_target_ids,
    ) = _partition_required_cover_ids(
        graph=graph,
        partition_node_ids=partition_node_ids,
    )

    if preferred_start_id is not None and preferred_start_id in partition_node_ids:
        frontier = [graph.get_node(preferred_start_id)]
        visited: set[str] = set()
        while frontier:
            current = frontier.pop(0)
            if current.id in visited:
                continue
            visited.add(current.id)
            if _covers_partition(
                graph=graph,
                candidate_ancestor_id=current.id,
                partition_node_ids=required_cover_ids,
            ):
                return current
            parents = sorted(
                graph.parents(current.id),
                key=lambda item: (-int(getattr(item, "depth", 0)), item.id),
            )
            frontier.extend(parents)

    common_ids: set[str] | None = None
    for node_id in sorted(required_cover_ids):
        ancestors = _hierarchical_ancestor_ids_including_self(graph, node_id)
        common_ids = ancestors if common_ids is None else common_ids & ancestors

    if not common_ids:
        raise PromptVerificationError(
            "Mini-graph source-root partition has no common hierarchical ancestor "
            "after source-sentence and semantic-context closure: " + ", ".join(sorted(required_cover_ids))
        )

    candidates = [graph.get_node(node_id) for node_id in common_ids]
    return max(candidates, key=lambda item: (int(getattr(item, "depth", 0)), item.id))


def _partition_mini_graph_by_source_root(
    *,
    graph: MemoryGraph,
    node_ids: list[str],
) -> dict[str, set[str]]:
    """Split an expanded anchor mini-graph into independent source trees."""
    partitions: dict[str, set[str]] = {}
    for node_id in node_ids:
        node = graph.nodes.get(node_id)
        if node is None:
            continue
        source_root_id = str(getattr(node, "source_root_id", "") or node.id)
        partitions.setdefault(source_root_id, set()).add(node_id)
    return partitions


def _node_source_type(node: Any) -> str:
    source_refs = list(getattr(node, "source_refs", []) or [])
    if not source_refs:
        return "unknown"
    source_type = getattr(source_refs[0], "source_type", None)
    return getattr(source_type, "value", str(source_type or "unknown"))


def _merge_query_context_entry_with_hierarchy_subsumption(
    *,
    ordered_entries: list[_CandidateQueryContextEntry],
    graph_name: str,
    graph: MemoryGraph,
    new_entry: _CandidateQueryContextEntry,
) -> None:
    """Insert one reconstructed Q statement with hierarchy-aware deduplication.

    Reconstruction happens independently for every top-k anchor mini-graph.  Only
    after that reconstruction do we compare the resulting hierarchy nodes.  If an
    existing statement is an ancestor of the new statement, the existing broader
    statement subsumes it.  If the new statement is an ancestor of one or more
    existing statements, it replaces those narrower descendants while absorbing
    their coverage/trigger metadata.  Statements from incomparable branches remain
    independent entries.
    """
    new_node = graph.nodes.get(new_entry.statement_node_id)
    if new_node is None:
        raise PromptVerificationError(
            "Cannot deduplicate reconstructed verifier context for unknown node "
            f"{new_entry.statement_node_id!r}"
        )
    new_source_root_id = str(getattr(new_node, "source_root_id", "") or new_node.id)
    new_ancestor_ids = _hierarchical_ancestor_ids_including_self(
        graph,
        new_entry.statement_node_id,
    )

    descendant_indexes: list[int] = []
    for index, existing in enumerate(ordered_entries):
        if existing.graph_name != graph_name:
            continue

        existing_node = graph.nodes.get(existing.statement_node_id)
        if existing_node is None:
            continue
        existing_source_root_id = str(getattr(existing_node, "source_root_id", "") or existing_node.id)
        if existing_source_root_id != new_source_root_id:
            continue

        # Existing is equal to or above the new reconstruction: keep the existing
        # broader statement and only merge bookkeeping from the new retrieval.
        if existing.statement_node_id in new_ancestor_ids:
            existing.covered_atomic_node_ids.update(new_entry.covered_atomic_node_ids)
            existing.triggered_by_candidate_atom_ids.update(new_entry.triggered_by_candidate_atom_ids)
            return

        # New is above the existing reconstruction.  Delay removal until all
        # descendants have been found so one broader statement can absorb several
        # narrower Q candidates produced by different top-k anchors/atoms.
        existing_ancestor_ids = _hierarchical_ancestor_ids_including_self(
            graph,
            existing.statement_node_id,
        )
        if new_entry.statement_node_id in existing_ancestor_ids:
            descendant_indexes.append(index)

    if descendant_indexes:
        insert_at = min(descendant_indexes)
        for index in descendant_indexes:
            existing = ordered_entries[index]
            new_entry.covered_atomic_node_ids.update(existing.covered_atomic_node_ids)
            new_entry.triggered_by_candidate_atom_ids.update(existing.triggered_by_candidate_atom_ids)

        for index in reversed(descendant_indexes):
            del ordered_entries[index]
        ordered_entries.insert(insert_at, new_entry)
        return

    ordered_entries.append(new_entry)


def _normalized_query_statement_key(statement: str) -> str:
    """Return a conservative normalized key for exact Q-statement dedup.

    This intentionally performs only presentation-level normalization: collapse
    whitespace and compare case-insensitively. It does not remove punctuation,
    rewrite Markdown, stem words, or attempt semantic/near-duplicate matching.
    """
    return _one_line(statement).casefold()


def _deduplicate_candidate_query_context_entries(
    entries: list[_CandidateQueryContextEntry],
) -> tuple[list[_CandidateQueryContextEntry], int]:
    """Collapse normalized exact-duplicate Q statements before verifier input.

    Retrieval can surface the same reconstructed statement from multiple source
    roots in one evidence graph, especially when overlapping KB retrieval outputs
    were appended at different times. Hierarchy-aware deduplication above cannot
    merge those entries because their graph nodes intentionally retain distinct
    provenance/source roots.

    This final pass deduplicates only within the same evidence origin
    (``graph_name`` + ``source_type``) so identical wording from semantically
    different sources (for example, user STATE versus KB authority) is never
    conflated. The first occurrence and its original wording are retained, while
    retrieval bookkeeping from later duplicates is merged into it.
    """
    deduplicated: list[_CandidateQueryContextEntry] = []
    by_key: dict[tuple[str, str, str], _CandidateQueryContextEntry] = {}
    duplicate_count = 0

    for entry in entries:
        normalized_statement = _normalized_query_statement_key(entry.statement)
        key = (entry.graph_name, entry.source_type, normalized_statement)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = entry
            deduplicated.append(entry)
            continue

        duplicate_count += 1
        existing.covered_atomic_node_ids.update(entry.covered_atomic_node_ids)
        existing.triggered_by_candidate_atom_ids.update(entry.triggered_by_candidate_atom_ids)

    return deduplicated, duplicate_count


def _build_candidate_query_context(
    *,
    candidate_atoms: list[Any],
    cuga_policy_graph: MemoryGraph,
    playbook_graph: MemoryGraph,
    knowledge_base_graph: MemoryGraph,
    state_graph: MemoryGraph,
    execution_graph: MemoryGraph,
) -> list[_CandidateQueryContextEntry]:
    """Retrieve top-k evidence and reconstruct each anchor independently.

    For each candidate atom and evidence graph:
      1. retrieve top-k anchors;
      2. expand each anchor into its existing relation-sensitive mini-graph;
      3. reconstruct that mini-graph independently (splitting by source root only
         if one traversal genuinely crossed into more than one source tree);
      4. choose the lowest covering hierarchy ancestor for each such partition
         after source-sentence and semantic-context closure;
      5. deduplicate reconstructed statements afterward by hierarchy
         subsumption;
      6. run one final normalized exact-text dedup across reconstructed Qs before
         assigning Q IDs (whitespace-normalized + case-insensitive, while keeping
         distinct evidence origins separate).

    There is deliberately no local-community/PPR partitioning, and top-k
    mini-graphs are deliberately NOT unioned before reconstruction.  Therefore an
    anchor cannot force another independently retrieved branch to share a common
    ancestor.  If separate anchors reconstruct to an ancestor and one of its
    descendants, the ancestor subsumes the descendant only in the final dedup pass.
    Incomparable branches remain separate Q statements.
    """
    graph_specs = [
        ("cuga_policy", cuga_policy_graph),
        ("playbook", playbook_graph),
        ("knowledge_base", knowledge_base_graph),
        ("context", state_graph),
        ("execution", execution_graph),
    ]

    ordered_entries: list[_CandidateQueryContextEntry] = []

    for candidate_atom in sorted(candidate_atoms, key=_candidate_atom_order_key):
        for graph_name, evidence_graph in graph_specs:
            evidence = _select_evidence_for_atom(
                candidate_atom=candidate_atom,
                evidence_graph=evidence_graph,
                evidence_space=graph_name,
            )
            mini_graphs = list(evidence["mini_graphs"])
            if not mini_graphs:
                continue

            # IMPORTANT: each top-k anchor keeps its own reconstruction boundary.
            # Traversal expansion may enrich this mini-graph, but evidence reached
            # from a different top-k anchor is never merged into it before ascent.
            for mini_graph in mini_graphs:
                anchor_id = str(mini_graph.get("anchor_id") or "")
                mini_graph_node_ids = [
                    str(node_id)
                    for node_id in mini_graph.get("node_ids", [])
                    if str(node_id) in evidence_graph.nodes
                ]
                if not mini_graph_node_ids:
                    continue

                source_root_partitions = _partition_mini_graph_by_source_root(
                    graph=evidence_graph,
                    node_ids=mini_graph_node_ids,
                )
                if not source_root_partitions:
                    continue

                anchor_node = evidence_graph.nodes.get(anchor_id)
                anchor_source_root_id = (
                    str(getattr(anchor_node, "source_root_id", "") or anchor_node.id)
                    if anchor_node is not None
                    else None
                )
                ordered_root_ids = sorted(
                    source_root_partitions,
                    key=lambda root_id: (
                        0 if root_id == anchor_source_root_id else 1,
                        root_id,
                    ),
                )

                for source_root_id in ordered_root_ids:
                    partition_node_ids = source_root_partitions[source_root_id]
                    preferred_start_id = anchor_id if anchor_id in partition_node_ids else None
                    ancestor = _closest_covering_ancestor(
                        graph=evidence_graph,
                        partition_node_ids=partition_node_ids,
                        preferred_start_id=preferred_start_id,
                    )

                    if ancestor.kind == NodeKind.RAW_SOURCE:
                        logger.warning(
                            "Prompt verifier per-anchor reconstruction reached "
                            "RAW_SOURCE: candidate_atom_id={} evidence_space={} "
                            "anchor_id={} source_root_id={} partition_nodes={} "
                            "statement_chars={}",
                            candidate_atom.id,
                            graph_name,
                            anchor_id,
                            source_root_id,
                            len(partition_node_ids),
                            len(ancestor.content or ""),
                        )

                    covered_atomic_ids = {
                        node_id
                        for node_id in partition_node_ids
                        if node_id in evidence_graph.nodes
                        and evidence_graph.nodes[node_id].kind == NodeKind.ATOMIC_FACT
                    }
                    new_entry = _CandidateQueryContextEntry(
                        graph_name=graph_name,
                        source_type=_node_source_type(ancestor),
                        statement_node_id=ancestor.id,
                        statement=ancestor.content.strip(),
                        covered_atomic_node_ids=set(covered_atomic_ids),
                        triggered_by_candidate_atom_ids={candidate_atom.id},
                    )
                    _merge_query_context_entry_with_hierarchy_subsumption(
                        ordered_entries=ordered_entries,
                        graph_name=graph_name,
                        graph=evidence_graph,
                        new_entry=new_entry,
                    )

    ordered_entries, _ = _deduplicate_candidate_query_context_entries(
        ordered_entries
    )

    for index, entry in enumerate(ordered_entries, start=1):
        entry.context_id = f"Q{index}"
    return ordered_entries


_VERIFIER_CONTEXT_ID_RE = re.compile(r"\bQ\d+\b", flags=re.IGNORECASE)


def _sanitize_previous_verifier_rejection_result(result: str) -> str:
    """Remove verifier-local Q labels before carrying a rejection forward.

    Q IDs are meaningful only inside the verifier call that created them. A prior
    rejection may say things such as "Q28 and Q17 establish ..."; if that text
    is embedded verbatim into the next call, the model can mistake those stale IDs
    for IDs in the new [CANDIDATE_QUERY_CONTEXT]. Preserve the substantive rejection
    explanation while replacing only the obsolete local labels.
    """
    sanitized = _VERIFIER_CONTEXT_ID_RE.sub(
        "the cited prior context statement",
        str(result or ""),
    )
    return _one_line(sanitized)


def _append_previous_verifier_rejection_context(
    entries: list[_CandidateQueryContextEntry],
    previous_rejection: tuple[str, str] | None,
) -> None:
    """Append one label-sanitized ephemeral statement for the prior rejection.

    This record is deliberately not inserted into any memory graph. It exists
    only in the source-context projection for the current verifier call, so it
    cannot contaminate STATE, authority, execution history, retrieval, or logic.

    Verifier-local Q IDs from the previous call are stripped before insertion.
    The new entry receives exactly one fresh Q ID belonging to the current call.
    """
    if previous_rejection is None:
        return

    previous_candidate, previous_result = previous_rejection
    candidate_text = _one_line(previous_candidate)
    result_text = _sanitize_previous_verifier_rejection_result(previous_result)
    if not candidate_text or not result_text:
        return

    entry = _CandidateQueryContextEntry(
        graph_name="verifier_rejection",
        source_type="verifier_rejection",
        statement_node_id="previous-verifier-rejection",
        statement=(f"Output [{candidate_text}] was rejected with result [{result_text}]."),
        context_id=f"Q{len(entries) + 1}",
    )
    entries.append(entry)

def _candidate_query_origin_label(entry: _CandidateQueryContextEntry) -> str:
    if entry.graph_name in {
        "cuga_policy",
        "playbook",
        "knowledge_base",
        "execution",
        "verifier_rejection",
    }:
        return entry.graph_name

    if entry.graph_name == "context":
        mapping = {
            SourceType.USER_MESSAGE.value: "user",
            SourceType.ASSISTANT_MESSAGE.value: "assistant",
            "reasoning": "reasoning",
        }
        return mapping.get(entry.source_type, entry.source_type or "state")

    return entry.graph_name


def _render_candidate_query_context(
    entries: list[_CandidateQueryContextEntry],
) -> list[str]:
    return [
        f"{entry.context_id} [{_candidate_query_origin_label(entry)}]: {_one_line(entry.statement)}"
        for entry in entries
    ]
