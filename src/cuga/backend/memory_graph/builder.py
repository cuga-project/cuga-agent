from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from .model_wrapper import (
    call_contextual_chunking_model,
    call_logical_structure_model,
    call_prompt_decomposition_model,
)
from .retrieval import build_retrieval_text, retrieval_text_hash
from .schemas import (
    BuildValidation,
    CreationMethod,
    DecompositionDraft,
    DraftHierarchyEdge,
    DraftLogicAssertion,
    DraftLogicBinding,
    DraftLogicExpression,
    DraftLogicRule,
    DraftNode,
    DraftRelation,
    EdgeFamily,
    GraphBuildRequest,
    GraphBuildResult,
    LocalDecompositionDecision,
    LocalLogicDecision,
    LocalLogicOperand,
    LogicAssertion,
    LogicBinding,
    LogicExpression,
    LogicLayer,
    LogicRule,
    MemoryEdge,
    MemoryNode,
    NodeKind,
    RelationOrigin,
    RelationType,
    RetrievalEmbedding,
    SourceReference,
    SourceSpan,
)
from .validation import DecompositionValidator


class PromptGraphBuildError(RuntimeError):
    pass


class InvalidModelOutputError(PromptGraphBuildError):
    pass


class InvalidDecompositionError(PromptGraphBuildError):
    def __init__(self, validation: BuildValidation) -> None:
        self.validation = validation
        detail = "; ".join(
            f"{issue.code}: {issue.message}"
            for issue in validation.issues
            if issue.severity.value == "error"
        )
        super().__init__(detail or "The decomposition draft is invalid.")


ModelCallable = Callable[
    [GraphBuildRequest],
    LocalDecompositionDecision | dict[str, Any],
]


EmbeddingCallable = Callable[
    [list[str]],
    list[list[float]],
]


@dataclass
class _DraftAccumulator:
    nodes: list[DraftNode] = field(default_factory=list)
    hierarchy: list[DraftHierarchyEdge] = field(default_factory=list)
    relations: list[DraftRelation] = field(default_factory=list)
    logic_assertions: list[DraftLogicAssertion] = field(default_factory=list)
    logic_rules: list[DraftLogicRule] = field(default_factory=list)
    # Compatibility only; V3 construction leaves this empty.
    logic_bindings: list[DraftLogicBinding] = field(default_factory=list)
    deferred_local_relations: list[dict[str, Any]] = field(default_factory=list)
    next_id: int = 0

    def allocate_id(self) -> str:
        temporary_id = f"local-{self.next_id}"
        self.next_id += 1
        return temporary_id


class GraphBuilder:
    """Transforms one prompt/message delta into a validated local graph patch.

    The default build path performs hierarchical decomposition recursively. The
    model is asked about exactly one statement at a time; Python owns traversal,
    temporary IDs, and hierarchy-edge construction.
    """

    def __init__(
        self,
        *,
        model_callable: ModelCallable = call_prompt_decomposition_model,
        validator: DecompositionValidator | None = None,
        max_decomposition_depth: int = 8,
        embedding_callable: EmbeddingCallable | None = None,
        embedding_model_name: str | None = None,
    ) -> None:
        if max_decomposition_depth < 0:
            raise ValueError("max_decomposition_depth must be non-negative")

        if (embedding_callable is None) != (embedding_model_name is None):
            raise ValueError(
                "embedding_callable and embedding_model_name must either "
                "both be provided or both be omitted"
            )

        if embedding_model_name is not None and not embedding_model_name.strip():
            raise ValueError("embedding_model_name cannot be empty")

        self._model_callable = model_callable
        self._validator = validator or DecompositionValidator()
        self._max_decomposition_depth = max_decomposition_depth
        self._embedding_callable = embedding_callable
        self._embedding_model_name = embedding_model_name

    def build(self, request: GraphBuildRequest) -> GraphBuildResult:
        draft = self._build_hierarchical_draft(request)
        validation = self._validator.validate(request=request, draft=draft)

        if not validation.accepted:
            raise InvalidDecompositionError(validation)

        result = self._materialize(
            request=request,
            draft=draft,
            validation=validation,
        )

        return self._attach_retrieval_embeddings(result)

    def _build_hierarchical_draft(
        self,
        request: GraphBuildRequest,
    ) -> DecompositionDraft:
        accumulator = _DraftAccumulator()

        # Coarse chunking is context-aware but source-grounded:
        #
        # - ``source_text`` is the exact primary source slice.
        # - ``context_source_texts`` / ``context_spans`` are exact external
        #   source excerpts selected only when needed to interpret the primary
        #   chunk.
        # - ``contextualized_text`` is derived, self-contained text used only as
        #   input to semantic decomposition.
        #
        # We intentionally do NOT create synthetic "chunk" graph nodes. Each
        # contextualized chunk enters the existing recursive semantic path as a
        # top-level statement beneath the RAW_SOURCE root, while its graph
        # provenance points only to authoritative root-source spans.
        source_chunks = call_contextual_chunking_model(request)

        for chunk_index, chunk in enumerate(source_chunks):
            primary_span = SourceSpan(
                start=chunk.start,
                end=chunk.end,
            )
            context_spans = [
                SourceSpan(start=start, end=end)
                for start, end in chunk.context_spans
            ]
            authoritative_spans = _dedupe_spans(
                sorted(
                    [primary_span, *context_spans],
                    key=lambda span: (span.start, span.end),
                )
            )

            self._expand_statement(
                source_request=request,
                statement=chunk.contextualized_text,
                statement_source_spans=authoritative_spans,
                semantic_role=None,
                statement_metadata={
                    "source_contextualization": (
                        "identity"
                        if (
                            chunk.contextualized_text == chunk.source_text
                            and not chunk.context_spans
                        )
                        else "llm_light_touch"
                    ),
                    "contextual_chunk_index": chunk_index,
                    "primary_source_span": primary_span.model_dump(mode="json"),
                    "context_source_spans": [
                        span.model_dump(mode="json")
                        for span in context_spans
                    ],
                    "authoritative_source_span_count": len(authoritative_spans),
                    "derived_contextualized_text": (
                        chunk.contextualized_text != chunk.source_text
                    ),
                },
                parent_temporary_id=None,
                depth=0,
                ancestors=frozenset(),
                accumulator=accumulator,
            )

        return DecompositionDraft(
            nodes=accumulator.nodes,
            hierarchy=accumulator.hierarchy,
            relations=accumulator.relations,
            logic_assertions=accumulator.logic_assertions,
            logic_rules=accumulator.logic_rules,
            logic_bindings=accumulator.logic_bindings,
            unresolved_references=[],
            metadata={
                "construction": (
                    "contextual_llm_chunked_recursive_local"
                    if len(source_chunks) > 1
                    else "recursive_local"
                ),
                "source_id": request.source_id,
                "source_chunk_count": len(source_chunks),
                "source_chunks": [
                    {
                        "index": index,
                        "primary_start": chunk.start,
                        "primary_end": chunk.end,
                        "primary_chars": chunk.end - chunk.start,
                        "context_spans": [
                            {"start": start, "end": end}
                            for start, end in chunk.context_spans
                        ],
                        "context_count": len(chunk.context_spans),
                        "contextualized_chars": len(chunk.contextualized_text),
                    }
                    for index, chunk in enumerate(source_chunks)
                ],
                "deferred_local_relations": accumulator.deferred_local_relations,
            },
        )

    def _expand_statement(
        self,
        *,
        source_request: GraphBuildRequest,
        statement: str,
        statement_source_spans: list[SourceSpan],
        semantic_role: str | None,
        parent_temporary_id: str | None,
        depth: int,
        ancestors: frozenset[str],
        accumulator: _DraftAccumulator,
        statement_metadata: dict[str, Any] | None = None,
    ) -> str:
        if depth > self._max_decomposition_depth:
            raise InvalidModelOutputError(
                "Maximum recursive decomposition depth exceeded for "
                f"source_id={source_request.source_id}. "
                f"Statement preview={statement[:300]!r}"
            )

        statement = statement.strip()
        if not statement:
            raise InvalidModelOutputError(
                "The decomposition model produced an empty statement for "
                f"source_id={source_request.source_id}."
            )

        statement_key = _statement_key(statement)
        if statement_key in ancestors:
            raise InvalidModelOutputError(
                "The decomposition model produced a cycle/repeated ancestor for "
                f"source_id={source_request.source_id}. "
                f"Statement preview={statement[:300]!r}"
            )

        local_request = source_request.model_copy(
            update={
                "content": statement,
                "metadata": {
                    **source_request.metadata,
                    "decomposition_depth": depth,
                    "decomposition_parent_temporary_id": parent_temporary_id,
                },
            }
        )

        decision = self._parse_local_model_output(
            self._model_callable(local_request)
        )

        temporary_id = accumulator.allocate_id()

        if decision.kind == "atomic":
            if decision.proposition is None:
                raise InvalidModelOutputError(
                    "Atomic decomposition is missing a proposition for "
                    f"source_id={source_request.source_id}."
                )

            draft_node = DraftNode(
                temporary_id=temporary_id,
                kind=NodeKind.ATOMIC_FACT,
                content=statement,
                routing_text=decision.routing_text,
                source_spans=_dedupe_spans(statement_source_spans),
                proposition=decision.proposition,
                metadata={
                    **(statement_metadata or {}),
                    **(
                        {"semantic_role": semantic_role}
                        if semantic_role is not None
                        else {}
                    ),
                },
            )
        else:
            if not decision.children:
                raise InvalidModelOutputError(
                    "Composite decomposition has no children for "
                    f"source_id={source_request.source_id}."
                )

            draft_node = DraftNode(
                temporary_id=temporary_id,
                kind=NodeKind.COMPOSITE,
                content=statement,
                routing_text=decision.routing_text,
                source_spans=_dedupe_spans(statement_source_spans),
                metadata={
                    **(statement_metadata or {}),
                    **(
                        {"semantic_role": semantic_role}
                        if semantic_role is not None
                        else {}
                    ),
                },
            )

        accumulator.nodes.append(draft_node)
        accumulator.hierarchy.append(
            DraftHierarchyEdge(
                parent_temporary_id=parent_temporary_id,
                child_temporary_id=temporary_id,
            )
        )

        if decision.kind == "atomic":
            return temporary_id

        local_logic_decision: LocalLogicDecision = call_logical_structure_model(
            local_request,
            decision,
        )

        next_ancestors = ancestors | {statement_key}
        seen_children: set[str] = set()
        direct_child_ids: list[str] = []

        for child_index, child in enumerate(decision.children):
            child_text = child.content.strip()
            child_key = _statement_key(child_text)

            if not child_key:
                raise InvalidModelOutputError(
                    "Composite decomposition returned an empty direct child for "
                    f"source_id={source_request.source_id}."
                )

            if child_key == statement_key:
                raise InvalidModelOutputError(
                    "Composite decomposition returned its parent unchanged as a "
                    f"child for source_id={source_request.source_id}. "
                    f"Statement preview={statement[:300]!r}"
                )

            if child_key in seen_children:
                raise InvalidModelOutputError(
                    "Composite decomposition returned duplicate direct children for "
                    f"source_id={source_request.source_id}. "
                    f"Child preview={child_text[:300]!r}"
                )

            seen_children.add(child_key)

            child_source_spans, child_provenance_exact = _resolve_excerpt_to_root_spans(
                root_content=source_request.content,
                parent_statement=statement,
                parent_source_spans=statement_source_spans,
                excerpt=child.source_text,
            )

            child_temporary_id = self._expand_statement(
                source_request=source_request,
                statement=child_text,
                statement_source_spans=child_source_spans,
                semantic_role=child.semantic_role.value,
                parent_temporary_id=temporary_id,
                depth=depth + 1,
                ancestors=next_ancestors,
                accumulator=accumulator,
                statement_metadata=None,
            )
            direct_child_ids.append(child_temporary_id)

            if not child_provenance_exact:
                _merge_node_metadata(
                    accumulator=accumulator,
                    temporary_id=child_temporary_id,
                    updates={
                        "source_provenance_precision": "inherited_parent_span",
                        "immediate_source_text": child.source_text,
                    },
                )

        for logic_index, assertion in enumerate(local_logic_decision.assertions):
            try:
                draft_root = _bind_local_logic_expression(
                    root_expression_id=assertion.root_expression_id,
                    decision=local_logic_decision,
                    direct_child_ids=direct_child_ids,
                )
            except (IndexError, KeyError) as exc:
                raise InvalidModelOutputError(
                    "Logical assertion references an invalid expression for "
                    f"source_id={source_request.source_id}: logic_index={logic_index}"
                ) from exc

            logic_spans, logic_provenance_exact = _resolve_excerpt_to_root_spans(
                root_content=source_request.content,
                parent_statement=statement,
                parent_source_spans=statement_source_spans,
                excerpt=assertion.evidence_text,
            )

            accumulator.logic_assertions.append(
                DraftLogicAssertion(
                    root=draft_root,
                    parent_temporary_id=temporary_id,
                    evidence_spans=logic_spans,
                    confidence=assertion.confidence,
                    metadata={
                        "construction": "local_logical_assertion",
                        "immediate_evidence_text": assertion.evidence_text,
                        "source_provenance_precision": (
                            "exact"
                            if logic_provenance_exact
                            else "inherited_parent_span"
                        ),
                    },
                )
            )

        for logic_index, rule in enumerate(local_logic_decision.rules):
            try:
                draft_condition = _bind_local_logic_ref(
                    ref=rule.condition,
                    decision=local_logic_decision,
                    direct_child_ids=direct_child_ids,
                )
                draft_effect = _bind_local_logic_ref(
                    ref=rule.effect,
                    decision=local_logic_decision,
                    direct_child_ids=direct_child_ids,
                )
            except (IndexError, KeyError) as exc:
                raise InvalidModelOutputError(
                    "Logical rule references an invalid child/expression for "
                    f"source_id={source_request.source_id}: logic_index={logic_index}"
                ) from exc

            logic_spans, logic_provenance_exact = _resolve_excerpt_to_root_spans(
                root_content=source_request.content,
                parent_statement=statement,
                parent_source_spans=statement_source_spans,
                excerpt=rule.evidence_text,
            )

            accumulator.logic_rules.append(
                DraftLogicRule(
                    condition=draft_condition,
                    effect=draft_effect,
                    parent_temporary_id=temporary_id,
                    evidence_spans=logic_spans,
                    confidence=rule.confidence,
                    metadata={
                        "construction": "local_logical_rule",
                        "immediate_evidence_text": rule.evidence_text,
                        "source_provenance_precision": (
                            "exact"
                            if logic_provenance_exact
                            else "inherited_parent_span"
                        ),
                    },
                )
            )

        for relation_index, relation in enumerate(decision.local_relations):
            try:
                relation_source_id = direct_child_ids[
                    relation.source_child_index
                ]
                relation_target_id = direct_child_ids[
                    relation.target_child_index
                ]
            except IndexError as exc:
                raise InvalidModelOutputError(
                    "Local relation references a child index outside the direct "
                    f"children for source_id={source_request.source_id}: "
                    f"relation_index={relation_index}"
                ) from exc

            relation_spans, relation_provenance_exact = _resolve_excerpt_to_root_spans(
                root_content=source_request.content,
                parent_statement=statement,
                parent_source_spans=statement_source_spans,
                excerpt=relation.evidence_text,
            )

            source_node = _draft_node_by_id(
                accumulator,
                relation_source_id,
            )
            target_node = _draft_node_by_id(
                accumulator,
                relation_target_id,
            )

            # The persistent MemoryGraph, retrieval layer, relation linker, and
            # verifier traversal are intentionally atomic-centric. Preserve that
            # invariant here: source-explicit relation hints are materialized only
            # when both direct child roots are already atomic. If recursive
            # decomposition turns either endpoint into a composite, retain the
            # hint as draft metadata for diagnostics/future resolution and let
            # the normal atomic relation linker recover relations among the
            # resulting leaves. Do not create composite lateral edges.
            if (
                source_node.kind != NodeKind.ATOMIC_FACT
                or target_node.kind != NodeKind.ATOMIC_FACT
            ):
                accumulator.deferred_local_relations.append(
                    {
                        "source_temporary_id": relation_source_id,
                        "target_temporary_id": relation_target_id,
                        "source_kind": source_node.kind.value,
                        "target_kind": target_node.kind.value,
                        "relation": relation.relation.value,
                        "origin": relation.origin.value,
                        "confidence": relation.confidence,
                        "parent_temporary_id": temporary_id,
                        "immediate_evidence_text": relation.evidence_text,
                        "evidence_spans": [
                            span.model_dump(mode="json")
                            for span in relation_spans
                        ],
                        "reason": "non_atomic_direct_child_endpoint",
                    }
                )
                continue

            accumulator.relations.append(
                DraftRelation(
                    source_temporary_id=relation_source_id,
                    target_temporary_id=relation_target_id,
                    relation=relation.relation,
                    directed=relation.relation not in _SYMMETRIC_RELATIONS,
                    origin=relation.origin,
                    evidence_spans=relation_spans,
                    confidence=relation.confidence,
                    metadata={
                        "construction": "local_decomposition_relation",
                        "parent_temporary_id": temporary_id,
                        "immediate_evidence_text": relation.evidence_text,
                        "source_provenance_precision": (
                            "exact"
                            if relation_provenance_exact
                            else "inherited_parent_span"
                        ),
                    },
                )
            )

        return temporary_id

    @staticmethod
    def _parse_local_model_output(
        raw_output: LocalDecompositionDecision | dict[str, Any],
    ) -> LocalDecompositionDecision:
        if isinstance(raw_output, LocalDecompositionDecision):
            return raw_output

        try:
            return LocalDecompositionDecision.model_validate(raw_output)
        except ValidationError as exc:
            raise InvalidModelOutputError(
                "The decomposition model returned invalid local structured output: "
                f"{exc}"
            ) from exc

    def _materialize(
        self,
        *,
        request: GraphBuildRequest,
        draft: DecompositionDraft,
        validation: BuildValidation,
    ) -> GraphBuildResult:
        root = self._create_root(request)
        analysis = self._validator.analyze_hierarchy(draft)
        temporary_to_canonical = {
            node.temporary_id: str(uuid4()) for node in draft.nodes
        }

        parent_ids: dict[str, list[str]] = defaultdict(list)
        for edge in draft.hierarchy:
            canonical_parent = (
                root.id
                if edge.parent_temporary_id is None
                else temporary_to_canonical[edge.parent_temporary_id]
            )
            parent_ids[edge.child_temporary_id].append(canonical_parent)

        nodes: list[MemoryNode] = [root]
        for draft_node in draft.nodes:
            source_refs = [
                SourceReference(
                    source_id=request.source_id,
                    source_type=request.source_type,
                    span=span,
                    turn_id=request.source_id,
                )
                for span in draft_node.source_spans
            ]
            nodes.append(
                MemoryNode(
                    id=temporary_to_canonical[draft_node.temporary_id],
                    session_id=request.session_id,
                    source_root_id=root.id,
                    kind=draft_node.kind,
                    depth=analysis.depths[draft_node.temporary_id],
                    content=draft_node.content,
                    routing_text=draft_node.routing_text,
                    proposition=draft_node.proposition,
                    confidence=draft_node.confidence,
                    source_refs=source_refs,
                    support_node_ids=list(
                        dict.fromkeys(parent_ids[draft_node.temporary_id])
                    ),
                    token_estimate=estimate_tokens(draft_node.content),
                    created_at=request.timestamp,
                    updated_at=request.timestamp,
                    metadata={
                        **draft_node.metadata,
                        "temporary_id": draft_node.temporary_id,
                    },
                )
            )

        edges: list[MemoryEdge] = []
        seen_hierarchy_edges: set[tuple[str, str]] = set()
        for draft_edge in draft.hierarchy:
            source_id = (
                root.id
                if draft_edge.parent_temporary_id is None
                else temporary_to_canonical[draft_edge.parent_temporary_id]
            )
            target_id = temporary_to_canonical[draft_edge.child_temporary_id]
            key = (source_id, target_id)
            if key in seen_hierarchy_edges:
                continue
            seen_hierarchy_edges.add(key)
            edges.append(
                MemoryEdge(
                    source_id=source_id,
                    target_id=target_id,
                    family=EdgeFamily.HIERARCHICAL,
                    relation=RelationType.DECOMPOSES_INTO,
                    directed=True,
                    confidence=draft_edge.confidence,
                    traversal_weight=draft_edge.traversal_weight,
                    creation_method=CreationMethod.MODEL_EXTRACTED,
                    evidence_node_ids=[root.id],
                    metadata=draft_edge.metadata,
                )
            )

        seen_lateral_edges: set[tuple[str, str, RelationType]] = set()
        for relation in draft.relations:
            source_id = temporary_to_canonical[relation.source_temporary_id]
            target_id = temporary_to_canonical[relation.target_temporary_id]
            key = (source_id, target_id, relation.relation)
            if key in seen_lateral_edges:
                continue
            seen_lateral_edges.add(key)

            relation_metadata = {
                **relation.metadata,
                "origin": relation.origin.value,
                "evidence_spans": [
                    span.model_dump(mode="json")
                    for span in relation.evidence_spans
                ],
            }

            edges.append(
                MemoryEdge(
                    source_id=source_id,
                    target_id=target_id,
                    family=EdgeFamily.LATERAL,
                    relation=relation.relation,
                    directed=relation.directed,
                    confidence=relation.confidence,
                    traversal_weight=relation.traversal_weight,
                    creation_method=(
                        CreationMethod.MODEL_EXTRACTED
                        if relation.origin == RelationOrigin.SOURCE_EXPLICIT
                        else CreationMethod.MODEL_INFERRED
                    ),
                    evidence_node_ids=[root.id],
                    metadata=relation_metadata,
                )
            )

        logic_assertions: list[LogicAssertion] = []
        for draft_assertion in draft.logic_assertions:
            source_refs = [
                SourceReference(
                    source_id=request.source_id,
                    source_type=request.source_type,
                    span=span,
                    turn_id=request.source_id,
                )
                for span in draft_assertion.evidence_spans
            ]
            logic_assertions.append(
                LogicAssertion(
                    root=_materialize_logic_expression(
                        draft_assertion.root,
                        temporary_to_canonical,
                    ),
                    parent_node_id=temporary_to_canonical[
                        draft_assertion.parent_temporary_id
                    ],
                    source_refs=source_refs,
                    confidence=draft_assertion.confidence,
                    metadata=draft_assertion.metadata,
                )
            )

        logic_rules: list[LogicRule] = []
        for draft_rule in draft.logic_rules:
            source_refs = [
                SourceReference(
                    source_id=request.source_id,
                    source_type=request.source_type,
                    span=span,
                    turn_id=request.source_id,
                )
                for span in draft_rule.evidence_spans
            ]
            logic_rules.append(
                LogicRule(
                    condition=_materialize_logic_expression(
                        draft_rule.condition,
                        temporary_to_canonical,
                    ),
                    effect=_materialize_logic_expression(
                        draft_rule.effect,
                        temporary_to_canonical,
                    ),
                    parent_node_id=temporary_to_canonical[
                        draft_rule.parent_temporary_id
                    ],
                    source_refs=source_refs,
                    confidence=draft_rule.confidence,
                    metadata=draft_rule.metadata,
                )
            )

        # Deprecated V1 bindings are intentionally not produced by this V3 builder.
        logic_bindings: list[LogicBinding] = []

        return GraphBuildResult(
            root_id=root.id,
            nodes=nodes,
            edges=edges,
            logic_layer=LogicLayer(
                assertions=logic_assertions,
                rules=logic_rules,
                bindings=logic_bindings,
            ),
            unresolved_references=draft.unresolved_references,
            validation=validation,
        )

    def _attach_retrieval_embeddings(
        self,
        result: GraphBuildResult,
    ) -> GraphBuildResult:
        """Attach one cached retrieval embedding to every atomic node.

        Embeddings are generated in one batch after graph materialization.
        RAW_SOURCE and COMPOSITE nodes remain unembedded because verifier
        retrieval operates over atomic propositions only.

        When no embedding callable is configured, the graph remains valid and
        retrieval falls back to the normalized lexical score. This keeps graph
        construction usable while the runtime embedding adapter is wired.
        """
        if self._embedding_callable is None:
            return result

        embedding_model_name = self._embedding_model_name
        if embedding_model_name is None:
            raise PromptGraphBuildError(
                "Embedding callable is configured without an embedding model name"
            )

        atomic_indexes = [
            index
            for index, node in enumerate(result.nodes)
            if node.kind == NodeKind.ATOMIC_FACT
        ]
        if not atomic_indexes:
            return result

        retrieval_texts = [
            build_retrieval_text(result.nodes[index])
            for index in atomic_indexes
        ]

        try:
            raw_vectors = self._embedding_callable(retrieval_texts)
        except Exception as exc:
            raise PromptGraphBuildError(
                "Failed to generate retrieval embeddings"
            ) from exc

        vectors = [
            [float(value) for value in vector]
            for vector in raw_vectors
        ]

        if len(vectors) != len(atomic_indexes):
            raise PromptGraphBuildError(
                "Embedding callable returned the wrong number of vectors: "
                f"expected {len(atomic_indexes)}, got {len(vectors)}"
            )

        dimensions: int | None = None
        for vector in vectors:
            if not vector:
                raise PromptGraphBuildError(
                    "Embedding callable returned an empty vector"
                )

            if not all(math.isfinite(value) for value in vector):
                raise PromptGraphBuildError(
                    "Embedding callable returned a non-finite vector value"
                )

            if dimensions is None:
                dimensions = len(vector)
            elif len(vector) != dimensions:
                raise PromptGraphBuildError(
                    "Embedding callable returned vectors with inconsistent "
                    "dimensions"
                )

        updated_nodes = list(result.nodes)

        for node_index, retrieval_text, vector in zip(
            atomic_indexes,
            retrieval_texts,
            vectors,
        ):
            node = result.nodes[node_index]
            embedding = RetrievalEmbedding(
                model=embedding_model_name,
                vector=vector,
                dimensions=len(vector),
                text_hash=retrieval_text_hash(retrieval_text),
            )
            updated_nodes[node_index] = node.model_copy(
                update={"retrieval_embedding": embedding}
            )

        return result.model_copy(
            update={"nodes": updated_nodes}
        )

    @staticmethod
    def _create_root(request: GraphBuildRequest) -> MemoryNode:
        root_id = str(uuid4())
        source_ref = SourceReference(
            source_id=request.source_id,
            source_type=request.source_type,
            span=SourceSpan(start=0, end=len(request.content)),
            turn_id=request.source_id,
        )
        return MemoryNode(
            id=root_id,
            session_id=request.session_id,
            source_root_id=root_id,
            kind=NodeKind.RAW_SOURCE,
            depth=0,
            content=request.content,
            routing_text=request.content,
            confidence=1.0,
            source_refs=[source_ref],
            token_estimate=estimate_tokens(request.content),
            created_at=request.timestamp,
            updated_at=request.timestamp,
            metadata=request.metadata,
        )


_SYMMETRIC_RELATIONS = {
    RelationType.RELATED_TO,
    RelationType.EQUIVALENT_TO,
    RelationType.COREFERS_WITH,
    RelationType.SAME_ENTITY,
    RelationType.SAME_EVENT,
    RelationType.CONTRADICTS,
}


def _bind_local_logic_ref(
    *,
    ref: LocalLogicOperand,
    decision: LocalLogicDecision,
    direct_child_ids: list[str],
) -> DraftLogicExpression:
    """Bind one local child/expression reference to semantic temporary IDs."""
    by_id = {node.expression_id: node for node in decision.expressions}

    def materialize_ref(local_ref: LocalLogicOperand) -> DraftLogicExpression:
        if local_ref.child_index is not None:
            return DraftLogicExpression(
                semantic_temporary_id=direct_child_ids[local_ref.child_index]
            )
        if local_ref.expression_id is None:
            raise KeyError("Logical reference has neither child nor expression ID")
        return materialize_expression(local_ref.expression_id)

    def materialize_expression(expression_id: int) -> DraftLogicExpression:
        node = by_id[expression_id]
        return DraftLogicExpression(
            operator=node.operator,
            threshold=node.threshold,
            operands=[materialize_ref(operand) for operand in node.operands],
        )

    return materialize_ref(ref)


def _bind_local_logic_expression(
    *,
    root_expression_id: int,
    decision: LocalLogicDecision,
    direct_child_ids: list[str],
) -> DraftLogicExpression:
    """Bind a local operator-expression ID to draft semantic temporary IDs."""
    return _bind_local_logic_ref(
        ref=LocalLogicOperand(expression_id=root_expression_id),
        decision=decision,
        direct_child_ids=direct_child_ids,
    )


def _materialize_logic_expression(
    expression: DraftLogicExpression,
    temporary_to_canonical: dict[str, str],
) -> LogicExpression:
    """Resolve draft semantic references without another model/inference pass."""
    if expression.semantic_temporary_id is not None:
        return LogicExpression(
            semantic_node_id=temporary_to_canonical[
                expression.semantic_temporary_id
            ]
        )

    return LogicExpression(
        operator=expression.operator,
        threshold=expression.threshold,
        operands=[
            _materialize_logic_expression(operand, temporary_to_canonical)
            for operand in expression.operands
        ],
    )


def _find_all_occurrences(text: str, excerpt: str) -> list[int]:
    """Return all literal occurrence offsets, including repeated evidence."""
    if not excerpt:
        return []

    starts: list[int] = []
    cursor = 0
    while True:
        index = text.find(excerpt, cursor)
        if index < 0:
            break
        starts.append(index)
        cursor = index + max(1, len(excerpt))
    return starts


def _dedupe_spans(spans: list[SourceSpan]) -> list[SourceSpan]:
    seen: set[tuple[int, int]] = set()
    deduped: list[SourceSpan] = []
    for span in spans:
        key = (span.start, span.end)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(span)
    return deduped


def _resolve_excerpt_to_root_spans(
    *,
    root_content: str,
    parent_statement: str,
    parent_source_spans: list[SourceSpan],
    excerpt: str,
) -> tuple[list[SourceSpan], bool]:
    """Map immediate-parent evidence back to root-source coordinates.

    Exact excerpts are translated to root offsets whenever possible.

    ``source_text`` and ``evidence_text`` are provenance aids rather than
    semantic truth. The model wrapper deliberately tolerates minor formatting
    drift in them, so this resolver must not turn the same drift back into a
    fatal graph-build error. When exact alignment is unavailable, we retain the
    already-established parent root span(s) conservatively and return
    ``exact=False``. We never fabricate character offsets.
    """
    excerpt = excerpt.strip()
    if not excerpt:
        raise InvalidModelOutputError(
            "Decomposition provenance excerpt cannot be empty."
        )

    parent_source_spans = _dedupe_spans(parent_source_spans)
    if not parent_source_spans:
        raise InvalidModelOutputError(
            "Cannot preserve decomposition provenance because the parent has no "
            "root source spans."
        )

    # Minor formatting/punctuation drift is non-fatal. The semantic coverage
    # audit already checked that the child meaning is supported by the source.
    # Keep the proven parent span instead of inventing an exact offset.
    if excerpt not in parent_statement:
        return parent_source_spans, False

    exact_spans: list[SourceSpan] = []

    # Strongest case: the parent statement itself maps exactly to a root span.
    for parent_span in parent_source_spans:
        root_slice = root_content[parent_span.start:parent_span.end]
        if root_slice != parent_statement:
            continue

        for local_start in _find_all_occurrences(parent_statement, excerpt):
            exact_spans.append(
                SourceSpan(
                    start=parent_span.start + local_start,
                    end=parent_span.start + local_start + len(excerpt),
                )
            )

    if exact_spans:
        return _dedupe_spans(exact_spans), True

    # The parent may be a paraphrase but its supporting source span can still
    # literally contain the child's excerpt. Restrict this search to established
    # parent provenance so repeated text elsewhere in the root cannot be chosen
    # accidentally.
    for parent_span in parent_source_spans:
        root_slice = root_content[parent_span.start:parent_span.end]
        for local_start in _find_all_occurrences(root_slice, excerpt):
            exact_spans.append(
                SourceSpan(
                    start=parent_span.start + local_start,
                    end=parent_span.start + local_start + len(excerpt),
                )
            )

    if exact_spans:
        return _dedupe_spans(exact_spans), True

    # Conservative fallback for recursively paraphrased statements. The child's
    # semantics are grounded through the parent, but exact root character offsets
    # are no longer recoverable without a separate alignment model.
    return parent_source_spans, False


def _merge_node_metadata(
    *,
    accumulator: _DraftAccumulator,
    temporary_id: str,
    updates: dict[str, Any],
) -> None:
    """Update metadata on one already-created draft node."""
    for index, node in enumerate(accumulator.nodes):
        if node.temporary_id != temporary_id:
            continue
        accumulator.nodes[index] = node.model_copy(
            update={"metadata": {**node.metadata, **updates}}
        )
        return

    raise InvalidModelOutputError(
        f"Could not find draft node {temporary_id!r} while attaching provenance metadata."
    )



def _draft_node_by_id(
    accumulator: _DraftAccumulator,
    temporary_id: str,
) -> DraftNode:
    for node in reversed(accumulator.nodes):
        if node.temporary_id == temporary_id:
            return node
    raise InvalidModelOutputError(
        f"Unknown draft node referenced by local relation: {temporary_id}"
    )



def _statement_key(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def estimate_tokens(text: str) -> int:
    """Cheap dependency-free estimate; replace with the configured model tokenizer later."""
    return max(1, (len(text) + 3) // 4)