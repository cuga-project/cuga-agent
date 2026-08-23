from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass

from .schemas import (
    BuildValidation,
    DecompositionDraft,
    DraftLogicExpression,
    GraphBuildRequest,
    NodeKind,
    RelationOrigin,
    RelationType,
    ValidationIssue,
    ValidationSeverity,
)


@dataclass(frozen=True)
class HierarchyAnalysis:
    depths: dict[str, int]
    parents: dict[str, set[str | None]]
    children: dict[str | None, set[str]]
    reachable: set[str]
    has_cycle: bool
    inconsistent_depth_nodes: set[str]


_SYMMETRIC_RELATIONS = {
    RelationType.RELATED_TO,
    RelationType.EQUIVALENT_TO,
    RelationType.COREFERS_WITH,
    RelationType.SAME_ENTITY,
    RelationType.SAME_EVENT,
    RelationType.CONTRADICTS,
}


class DecompositionValidator:
    def __init__(
        self,
        *,
        max_nodes: int = 256,
        max_depth: int = 12,
        require_atomic_proposition: bool = True,
        require_source_provenance: bool = True,
        require_explicit_relation_evidence: bool = True,
    ) -> None:
        self.max_nodes = max_nodes
        self.max_depth = max_depth
        self.require_atomic_proposition = require_atomic_proposition
        self.require_source_provenance = require_source_provenance
        self.require_explicit_relation_evidence = require_explicit_relation_evidence

    def validate(
        self,
        *,
        request: GraphBuildRequest,
        draft: DecompositionDraft,
    ) -> BuildValidation:
        issues: list[ValidationIssue] = []

        if len(draft.nodes) > self.max_nodes:
            issues.append(
                self._error(
                    "too_many_nodes",
                    f"Draft has {len(draft.nodes)} nodes; maximum is {self.max_nodes}.",
                )
            )

        id_counts = Counter(node.temporary_id for node in draft.nodes)
        for node_id, count in id_counts.items():
            if count > 1:
                issues.append(
                    self._error(
                        "duplicate_node_definition",
                        (
                            f"Node {node_id!r} is defined {count} times. Define it once "
                            "and reuse it through multiple hierarchy edges."
                        ),
                        node_id,
                    )
                )

        by_id = {node.temporary_id: node for node in draft.nodes}

        edge_counts = Counter(
            (edge.parent_temporary_id, edge.child_temporary_id)
            for edge in draft.hierarchy
        )
        for (parent_id, child_id), count in edge_counts.items():
            if count > 1:
                issues.append(
                    self._warning(
                        "duplicate_hierarchy_edge",
                        (
                            f"Hierarchy edge {parent_id!r} -> {child_id!r} appears "
                            f"{count} times and will be materialized once."
                        ),
                        child_id,
                    )
                )

        for edge in draft.hierarchy:
            if edge.child_temporary_id not in by_id:
                issues.append(
                    self._error(
                        "unknown_hierarchy_child",
                        f"Unknown hierarchy child: {edge.child_temporary_id}",
                        edge.child_temporary_id,
                    )
                )
            if (
                edge.parent_temporary_id is not None
                and edge.parent_temporary_id not in by_id
            ):
                issues.append(
                    self._error(
                        "unknown_hierarchy_parent",
                        f"Unknown hierarchy parent: {edge.parent_temporary_id}",
                        edge.parent_temporary_id,
                    )
                )
            if edge.parent_temporary_id == edge.child_temporary_id:
                issues.append(
                    self._error(
                        "hierarchy_self_edge",
                        "A decomposition node cannot be its own parent.",
                        edge.child_temporary_id,
                    )
                )

        analysis = self.analyze_hierarchy(draft)

        if analysis.has_cycle:
            issues.append(
                self._error(
                    "hierarchy_cycle",
                    "The decomposition hierarchy contains a cycle.",
                )
            )

        for node_id in sorted(set(by_id) - analysis.reachable):
            issues.append(
                self._error(
                    "unreachable_node",
                    "Every node must be reachable from the deterministic raw root.",
                    node_id,
                )
            )

        for node_id in sorted(analysis.inconsistent_depth_nodes):
            issues.append(
                self._error(
                    "inconsistent_layer",
                    (
                        "A shared node is reached at different depths. All of its parents "
                        "must place it in the same decomposition layer."
                    ),
                    node_id,
                )
            )

        for node in draft.nodes:
            depth = analysis.depths.get(node.temporary_id)
            child_ids = analysis.children.get(node.temporary_id, set())

            if depth is not None and depth > self.max_depth:
                issues.append(
                    self._error(
                        "max_depth_exceeded",
                        f"Node depth {depth} exceeds maximum {self.max_depth}.",
                        node.temporary_id,
                    )
                )

            if node.kind == NodeKind.RAW_SOURCE:
                issues.append(
                    self._error(
                        "raw_source_in_model_output",
                        (
                            "The raw source root is created deterministically and must not "
                            "be returned by the model."
                        ),
                        node.temporary_id,
                    )
                )

            if node.kind == NodeKind.ATOMIC_FACT and child_ids:
                issues.append(
                    self._error(
                        "atomic_has_children",
                        "Atomic facts cannot have decomposition children.",
                        node.temporary_id,
                    )
                )

            if node.kind == NodeKind.COMPOSITE and not child_ids:
                issues.append(
                    self._error(
                        "composite_without_children",
                        "Composite nodes must have at least one decomposition child.",
                        node.temporary_id,
                    )
                )

            if (
                self.require_atomic_proposition
                and node.kind == NodeKind.ATOMIC_FACT
                and node.proposition is None
            ):
                issues.append(
                    self._error(
                        "atomic_missing_proposition",
                        "Atomic facts must include a structured proposition payload.",
                        node.temporary_id,
                    )
                )

            if node.kind != NodeKind.ATOMIC_FACT and node.proposition is not None:
                issues.append(
                    self._warning(
                        "non_atomic_has_proposition",
                        (
                            "A non-atomic node contains a proposition payload; it will be "
                            "preserved but is unusual."
                        ),
                        node.temporary_id,
                    )
                )

            if not node.source_spans:
                issue_factory = (
                    self._error
                    if self.require_source_provenance
                    else self._warning
                )
                issues.append(
                    issue_factory(
                        "missing_source_span",
                        (
                            "Node has no root-source provenance. Lossless decomposition "
                            "requires every generated node to remain traceable to the "
                            "source text, either exactly or through an inherited parent "
                            "span."
                        ),
                        node.temporary_id,
                    )
                )

            for span in node.source_spans:
                if span.end == span.start:
                    issues.append(
                        self._error(
                            "empty_node_source_span",
                            (
                                f"Node source span [{span.start}, {span.end}) is empty. "
                                "Provenance spans must cover source text."
                            ),
                            node.temporary_id,
                        )
                    )
                if span.end > len(request.content):
                    issues.append(
                        self._error(
                            "node_source_span_out_of_bounds",
                            (
                                f"Node source span [{span.start}, {span.end}) "
                                f"exceeds source length {len(request.content)}."
                            ),
                            node.temporary_id,
                        )
                    )

        normalized_groups: dict[str, list[str]] = defaultdict(list)
        for node in draft.nodes:
            normalized_groups[self._normalize(node.content)].append(node.temporary_id)

        for node_ids in normalized_groups.values():
            if len(node_ids) > 1:
                issues.append(
                    self._warning(
                        "repeated_content_nodes",
                        (
                            "Multiple node definitions have identical text. This is allowed: "
                            "they may be distinct occurrences. Consider either reusing one "
                            "node through multiple parent edges or adding an EQUIVALENT_TO/"
                            "COREFERS_WITH relation when appropriate."
                        ),
                        node_ids[0],
                    )
                )

        seen_parent_child: set[tuple[str | None, str]] = set()
        for edge in draft.hierarchy:
            key = (edge.parent_temporary_id, edge.child_temporary_id)
            if key in seen_parent_child:
                continue
            seen_parent_child.add(key)
            if edge.parent_temporary_id is None:
                parent_content = request.content
            else:
                parent = by_id.get(edge.parent_temporary_id)
                parent_content = parent.content if parent is not None else None
            child = by_id.get(edge.child_temporary_id)
            if (
                parent_content is not None
                and child is not None
                and self._normalize(parent_content) == self._normalize(child.content)
            ):
                issues.append(
                    self._warning(
                        "non_narrowing_decomposition",
                        (
                            "Parent and child have identical text. This is allowed when the "
                            "child adds structure, provenance, or graph membership, but it "
                            "does not by itself narrow the meaning."
                        ),
                        child.temporary_id,
                    )
                )

        seen_relations: set[tuple[str, str, RelationType, bool]] = set()

        for relation in draft.relations:
            source = by_id.get(relation.source_temporary_id)
            target = by_id.get(relation.target_temporary_id)

            if source is None:
                issues.append(
                    self._error(
                        "unknown_relation_source",
                        f"Unknown relation source: {relation.source_temporary_id}",
                        relation.source_temporary_id,
                    )
                )
                continue

            if target is None:
                issues.append(
                    self._error(
                        "unknown_relation_target",
                        f"Unknown relation target: {relation.target_temporary_id}",
                        relation.target_temporary_id,
                    )
                )
                continue

            if source.temporary_id == target.temporary_id:
                issues.append(
                    self._error(
                        "self_relation",
                        "Lateral relations cannot connect a node to itself.",
                        source.temporary_id,
                    )
                )

            if relation.relation == RelationType.DECOMPOSES_INTO:
                issues.append(
                    self._error(
                        "invalid_lateral_relation_type",
                        (
                            "DECOMPOSES_INTO is created from hierarchy edges and cannot "
                            "be returned as a lateral relation."
                        ),
                        source.temporary_id,
                    )
                )

            expected_directed = relation.relation not in _SYMMETRIC_RELATIONS
            if relation.directed != expected_directed:
                issues.append(
                    self._error(
                        "invalid_relation_directionality",
                        (
                            f"Relation {relation.relation.value!r} must be "
                            f"{'directed' if expected_directed else 'symmetric/undirected'}."
                        ),
                        source.temporary_id,
                    )
                )

            source_depth = analysis.depths.get(source.temporary_id)
            target_depth = analysis.depths.get(target.temporary_id)
            if (
                source_depth is not None
                and target_depth is not None
                and source_depth != target_depth
            ):
                issues.append(
                    self._error(
                        "cross_depth_local_relation",
                        (
                            "Decomposition-local relations must connect direct children "
                            "at the same decomposition depth."
                        ),
                        source.temporary_id,
                    )
                )

            source_parents = analysis.parents.get(source.temporary_id, set())
            target_parents = analysis.parents.get(target.temporary_id, set())
            shared_non_root_parents = {
                parent_id
                for parent_id in source_parents & target_parents
                if parent_id is not None
            }
            shared_root_parent = (
                None in source_parents and None in target_parents
            )
            if not shared_non_root_parents and not shared_root_parent:
                issues.append(
                    self._error(
                        "non_sibling_local_relation",
                        (
                            "Decomposition-local relations must connect sibling nodes "
                            "produced by the same parent decomposition call."
                        ),
                        source.temporary_id,
                    )
                )

            # Keep lateral semantic edges atomic-only. This matches
            # MemoryGraph.add_edge(), retrieval, relation linking, and verifier
            # traversal. GraphBuilder defers source-explicit hints whose direct
            # child endpoints recursively expand into composites instead of
            # materializing structurally unreachable composite lateral edges.
            if (
                source.kind != NodeKind.ATOMIC_FACT
                or target.kind != NodeKind.ATOMIC_FACT
            ):
                issues.append(
                    self._error(
                        "non_atomic_lateral_relation",
                        (
                            "Lateral relations must connect atomic fact nodes. "
                            f"Got {source.kind.value} -> {target.kind.value}."
                        ),
                        source.temporary_id,
                    )
                )

            if not relation.evidence_spans:
                issue_factory = (
                    self._error
                    if (
                        relation.origin == RelationOrigin.SOURCE_EXPLICIT
                        and self.require_explicit_relation_evidence
                    )
                    else self._warning
                )
                issues.append(
                    issue_factory(
                        "relation_missing_evidence",
                        (
                            "Relation has no root-source evidence span. Source-explicit "
                            "relations must remain traceable to the wording that directly "
                            "expresses them."
                        ),
                        relation.source_temporary_id,
                    )
                )

            for span in relation.evidence_spans:
                if span.end == span.start:
                    issues.append(
                        self._error(
                            "empty_relation_evidence_span",
                            (
                                f"Relation evidence span [{span.start}, {span.end}) is "
                                "empty. Evidence spans must cover source text."
                            ),
                            relation.source_temporary_id,
                        )
                    )
                if span.end > len(request.content):
                    issues.append(
                        self._error(
                            "relation_evidence_span_out_of_bounds",
                            (
                                f"Relation evidence span [{span.start}, {span.end}) "
                                f"exceeds source length {len(request.content)}."
                            ),
                            relation.source_temporary_id,
                        )
                    )

            # For exact provenance, verify that the saved immediate evidence is
            # literally present in at least one root-source evidence slice.
            provenance_precision = relation.metadata.get(
                "source_provenance_precision"
            )
            evidence_text = relation.metadata.get("immediate_evidence_text")
            if (
                provenance_precision == "exact"
                and isinstance(evidence_text, str)
                and evidence_text
                and relation.evidence_spans
            ):
                if not any(
                    evidence_text
                    in request.content[span.start:span.end]
                    for span in relation.evidence_spans
                ):
                    issues.append(
                        self._error(
                            "relation_exact_evidence_mismatch",
                            (
                                "Relation is marked with exact provenance, but its "
                                "immediate evidence text is not contained in any saved "
                                "root-source evidence span."
                            ),
                            relation.source_temporary_id,
                        )
                    )

            if relation.relation in _SYMMETRIC_RELATIONS:
                endpoints = tuple(
                    sorted(
                        (
                            relation.source_temporary_id,
                            relation.target_temporary_id,
                        )
                    )
                )
                relation_key = (
                    endpoints[0],
                    endpoints[1],
                    relation.relation,
                    False,
                )
            else:
                relation_key = (
                    relation.source_temporary_id,
                    relation.target_temporary_id,
                    relation.relation,
                    True,
                )

            if relation_key in seen_relations:
                issues.append(
                    self._warning(
                        "duplicate_lateral_relation",
                        (
                            "The same lateral relation appears multiple times and "
                            "should be materialized only once."
                        ),
                        source.temporary_id,
                    )
                )
            else:
                seen_relations.add(relation_key)

        # The logical layer is intentionally separate from lateral semantic edges.
        # Every semantic leaf referenced by a local assertion/rule must resolve to
        # a direct child of the composite parent that produced that structure.
        def validate_logic_common(
            *,
            parent_temporary_id: str,
            expressions: list[tuple[str, DraftLogicExpression]],
            evidence_spans,
            metadata,
        ) -> None:
            parent = by_id.get(parent_temporary_id)
            if parent is None:
                issues.append(
                    self._error(
                        "unknown_logic_parent",
                        f"Unknown logical structure parent: {parent_temporary_id}",
                        parent_temporary_id,
                    )
                )
                return

            if parent.kind != NodeKind.COMPOSITE:
                issues.append(
                    self._error(
                        "logic_parent_not_composite",
                        "Local logical structures must belong to a composite semantic node.",
                        parent_temporary_id,
                    )
                )

            direct_children = analysis.children.get(parent_temporary_id, set())
            for label, expression in expressions:
                for semantic_id in self._logic_semantic_refs(expression):
                    if semantic_id not in by_id:
                        issues.append(
                            self._error(
                                "unknown_logic_operand",
                                f"Logical {label} references unknown semantic node: "
                                f"{semantic_id}",
                                parent_temporary_id,
                            )
                        )
                    elif semantic_id not in direct_children:
                        issues.append(
                            self._error(
                                "non_local_logic_operand",
                                (
                                    "Local logical terms may reference only direct "
                                    "children of their parent decomposition call. "
                                    f"Logical {label} referenced {semantic_id!r}."
                                ),
                                parent_temporary_id,
                            )
                        )

            if not evidence_spans:
                issues.append(
                    self._error(
                        "logic_missing_evidence",
                        "Source-explicit logical structure requires root-source evidence.",
                        parent_temporary_id,
                    )
                )

            for span in evidence_spans:
                if span.end == span.start:
                    issues.append(
                        self._error(
                            "empty_logic_evidence_span",
                            f"Logical evidence span [{span.start}, {span.end}) is empty.",
                            parent_temporary_id,
                        )
                    )
                if span.end > len(request.content):
                    issues.append(
                        self._error(
                            "logic_evidence_span_out_of_bounds",
                            (
                                f"Logical evidence span [{span.start}, {span.end}) "
                                f"exceeds source length {len(request.content)}."
                            ),
                            parent_temporary_id,
                        )
                    )

            provenance_precision = metadata.get("source_provenance_precision")
            evidence_text = metadata.get("immediate_evidence_text")
            if (
                provenance_precision == "exact"
                and isinstance(evidence_text, str)
                and evidence_text
                and evidence_spans
                and not any(
                    evidence_text in request.content[span.start:span.end]
                    for span in evidence_spans
                )
            ):
                issues.append(
                    self._error(
                        "logic_exact_evidence_mismatch",
                        (
                            "Logical structure is marked with exact provenance, but "
                            "its evidence text is not contained in any saved span."
                        ),
                        parent_temporary_id,
                    )
                )

        for assertion_index, assertion in enumerate(draft.logic_assertions):
            if (
                assertion.root.semantic_temporary_id is not None
                or assertion.root.operator is None
            ):
                issues.append(
                    self._error(
                        "logic_assertion_not_operator_expression",
                        (
                            "Logic assertions must root at a Boolean/cardinality "
                            "operator expression. Standalone semantic children are "
                            "already represented by the semantic graph and must not "
                            "be duplicated as assertions."
                        ),
                        assertion.parent_temporary_id,
                    )
                )

            validate_logic_common(
                parent_temporary_id=assertion.parent_temporary_id,
                expressions=[(f"assertion[{assertion_index}] root", assertion.root)],
                evidence_spans=assertion.evidence_spans,
                metadata=assertion.metadata,
            )

        for rule_index, rule in enumerate(draft.logic_rules):
            validate_logic_common(
                parent_temporary_id=rule.parent_temporary_id,
                expressions=[
                    (f"rule[{rule_index}] condition", rule.condition),
                    (f"rule[{rule_index}] effect", rule.effect),
                ],
                evidence_spans=rule.evidence_spans,
                metadata=rule.metadata,
            )

        # Deprecated V1 compatibility structures are still accepted if another
        # caller supplies them, but the V3 builder itself does not produce them.
        for binding_index, binding in enumerate(draft.logic_bindings):
            expressions = [(f"legacy_binding[{binding_index}] expression", binding.expression)]
            if binding.effect_temporary_id is not None:
                expressions.append(
                    (
                        f"legacy_binding[{binding_index}] effect",
                        DraftLogicExpression(
                            semantic_temporary_id=binding.effect_temporary_id
                        ),
                    )
                )
            validate_logic_common(
                parent_temporary_id=binding.parent_temporary_id,
                expressions=expressions,
                evidence_spans=binding.evidence_spans,
                metadata=binding.metadata,
            )

        accepted = not any(issue.severity == ValidationSeverity.ERROR for issue in issues)
        return BuildValidation(accepted=accepted, issues=issues)

    def analyze_hierarchy(self, draft: DecompositionDraft) -> HierarchyAnalysis:
        node_ids = {node.temporary_id for node in draft.nodes}
        parents: dict[str, set[str | None]] = defaultdict(set)
        children: dict[str | None, set[str]] = defaultdict(set)

        for edge in draft.hierarchy:
            if edge.child_temporary_id not in node_ids:
                continue
            if edge.parent_temporary_id is not None and edge.parent_temporary_id not in node_ids:
                continue
            parents[edge.child_temporary_id].add(edge.parent_temporary_id)
            children[edge.parent_temporary_id].add(edge.child_temporary_id)

        depth_sets: dict[str, set[int]] = defaultdict(set)
        reachable: set[str] = set()
        queue: deque[tuple[str, int]] = deque(
            (child_id, 1) for child_id in children.get(None, set())
        )
        seen_states: set[tuple[str, int]] = set()

        # Depth exploration is capped to avoid looping forever when a malformed draft cycles.
        max_exploration_depth = max(self.max_depth + len(node_ids) + 1, 32)
        while queue:
            node_id, depth = queue.popleft()
            state = (node_id, depth)
            if state in seen_states or depth > max_exploration_depth:
                continue
            seen_states.add(state)
            reachable.add(node_id)
            depth_sets[node_id].add(depth)
            for child_id in children.get(node_id, set()):
                queue.append((child_id, depth + 1))

        indegree = {node_id: 0 for node_id in node_ids}
        for parent_id, child_ids in children.items():
            if parent_id is None:
                continue
            for child_id in child_ids:
                indegree[child_id] += 1

        topo_queue = deque(node_id for node_id, degree in indegree.items() if degree == 0)
        visited_count = 0
        while topo_queue:
            node_id = topo_queue.popleft()
            visited_count += 1
            for child_id in children.get(node_id, set()):
                indegree[child_id] -= 1
                if indegree[child_id] == 0:
                    topo_queue.append(child_id)

        has_cycle = visited_count != len(node_ids)
        inconsistent_depth_nodes = {
            node_id for node_id, depths in depth_sets.items() if len(depths) > 1
        }
        depths = {
            node_id: min(values)
            for node_id, values in depth_sets.items()
            if values
        }

        return HierarchyAnalysis(
            depths=depths,
            parents={node_id: set(values) for node_id, values in parents.items()},
            children={node_id: set(values) for node_id, values in children.items()},
            reachable=reachable,
            has_cycle=has_cycle,
            inconsistent_depth_nodes=inconsistent_depth_nodes,
        )

    @staticmethod
    def _logic_semantic_refs(
        expression: DraftLogicExpression,
    ):
        if expression.semantic_temporary_id is not None:
            yield expression.semantic_temporary_id
            return
        for operand in expression.operands:
            yield from DecompositionValidator._logic_semantic_refs(operand)

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(value.casefold().split())

    @staticmethod
    def _error(
        code: str,
        message: str,
        temporary_id: str | None = None,
    ) -> ValidationIssue:
        return ValidationIssue(
            code=code,
            message=message,
            severity=ValidationSeverity.ERROR,
            temporary_id=temporary_id,
        )

    @staticmethod
    def _warning(
        code: str,
        message: str,
        temporary_id: str | None = None,
    ) -> ValidationIssue:
        return ValidationIssue(
            code=code,
            message=message,
            severity=ValidationSeverity.WARNING,
            temporary_id=temporary_id,
        )