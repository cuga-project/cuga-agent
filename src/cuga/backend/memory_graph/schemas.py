from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SourceType(str, Enum):
    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    TOOL_RESULT = "tool_result"
    POLICY = "policy"
    DOCUMENT = "document"
    REASONING = "reasoning"


class NodeKind(str, Enum):
    RAW_SOURCE = "raw_source"
    COMPOSITE = "composite"
    ATOMIC_FACT = "atomic_fact"


class NodeStatus(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    RETRACTED = "retracted"
    EXPIRED = "expired"


class EdgeFamily(str, Enum):
    HIERARCHICAL = "hierarchical"
    LATERAL = "lateral"


class RelationType(str, Enum):
    DECOMPOSES_INTO = "decomposes_into"
    RELATED_TO = "related_to"
    EQUIVALENT_TO = "equivalent_to"
    COREFERS_WITH = "corefers_with"
    SAME_ENTITY = "same_entity"
    SAME_EVENT = "same_event"
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    QUALIFIES = "qualifies"
    SERVES_GOAL = "serves_goal"
    REQUIRES = "requires"
    ENABLES = "enables"
    PRECEDES = "precedes"
    CAUSES = "causes"
    SUPERSEDES = "supersedes"


class RelationDirection(str, Enum):
    """Direction of a lateral relation relative to the anchor node."""

    ANCHOR_TO_CANDIDATE = "anchor_to_candidate"
    CANDIDATE_TO_ANCHOR = "candidate_to_anchor"
    SYMMETRIC = "symmetric"


class SemanticRole(str, Enum):
    """Primary semantic role of one locally decomposed statement."""

    FACT = "fact"
    DEFINITION = "definition"
    REQUIREMENT = "requirement"
    PROHIBITION = "prohibition"
    PERMISSION = "permission"
    CONDITION = "condition"
    PROCEDURE = "procedure"
    EXCEPTION = "exception"
    QUALIFICATION = "qualification"
    OBSERVATION = "observation"
    USER_CLAIM = "user_claim"
    INTENDED_ACTION = "intended_action"
    POSTCONDITION = "postcondition"


class RelationOrigin(str, Enum):
    SOURCE_EXPLICIT = "source_explicit"
    SOURCE_IMPLIED = "source_implied"


class LogicalOperator(str, Enum):
    """Boolean/cardinality operators kept outside the semantic memory graph."""

    AND = "and"
    OR = "or"
    NOT = "not"
    AT_LEAST = "at_least"
    AT_MOST = "at_most"
    EXACTLY = "exactly"


class CreationMethod(str, Enum):
    DETERMINISTIC = "deterministic"
    MODEL_EXTRACTED = "model_extracted"
    MODEL_INFERRED = "model_inferred"
    HUMAN = "human"


class ContextMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    source_type: SourceType
    content: str
    timestamp: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class GraphBuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    source_id: str
    source_type: SourceType
    content: str = Field(min_length=1)
    context: list[ContextMessage] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: int = Field(ge=0)
    end: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_order(self) -> SourceSpan:
        if self.end < self.start:
            raise ValueError("source span end must be >= start")
        return self


class SourceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    source_type: SourceType
    span: SourceSpan | None = None
    turn_id: str | None = None
    tool_call_id: str | None = None


class PropositionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str | None = None
    predicate: str | None = None
    object: str | None = None
    polarity: str = "positive"
    modality: str | None = None
    quantifier: str | None = None
    temporal_scope: str | None = None
    condition: str | None = None
    attribution: str | None = None
    qualifiers: dict[str, Any] = Field(default_factory=dict)



class RetrievalEmbedding(BaseModel):
    """Cached semantic embedding used for deterministic node retrieval."""

    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1)
    vector: list[float] = Field(min_length=1)
    dimensions: int = Field(gt=0)
    text_hash: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_dimensions(self) -> Self:
        if len(self.vector) != self.dimensions:
            raise ValueError(
                "Embedding vector length must match embedding dimensions."
            )
        return self


class RelationCandidate(BaseModel):
    """One atomic node that may have a lateral relation to an anchor node."""

    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    routing_text: str = Field(min_length=1)
    proposition: PropositionPayload | None = None


class RelationBuildRequest(BaseModel):
    """Local relation-discovery request centered on one atomic anchor node.

    Candidate retrieval is intentionally performed outside the model wrapper.
    The model receives only a small, preselected semantic neighborhood.
    """

    model_config = ConfigDict(extra="forbid")

    anchor_node_id: str = Field(min_length=1)
    anchor_content: str = Field(min_length=1)
    anchor_routing_text: str = Field(min_length=1)
    anchor_proposition: PropositionPayload | None = None
    candidates: list[RelationCandidate] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_candidates(self) -> Self:
        candidate_ids = [candidate.node_id for candidate in self.candidates]

        if self.anchor_node_id in candidate_ids:
            raise ValueError(
                "Relation candidates cannot contain the anchor node itself."
            )

        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("Relation candidate node IDs must be unique.")

        return self


class RelationDecision(BaseModel):
    """One model-inferred lateral relation between anchor and candidate."""

    model_config = ConfigDict(extra="forbid")

    other_node_id: str = Field(min_length=1)
    relation: RelationType
    direction: RelationDirection
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_lateral_relation(self) -> Self:
        if self.relation == RelationType.DECOMPOSES_INTO:
            raise ValueError(
                "DECOMPOSES_INTO is hierarchical and cannot be returned as a lateral relation."
            )
        return self


class RelationBuildResponse(BaseModel):
    """Sparse relation-model output.

    Unrelated candidates are omitted rather than represented by a synthetic
    NONE relation.
    """

    model_config = ConfigDict(extra="forbid")

    relations: list[RelationDecision] = Field(default_factory=list)


class LocalChildStatement(BaseModel):
    """One direct semantic child of a composite statement.

    ``source_text`` is a verbatim excerpt from the immediate parent statement.
    It is intentionally text rather than offsets: the model supplies the exact
    supporting phrase and the builder deterministically resolves it to
    ``SourceSpan`` coordinates.
    """

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1)
    source_text: str = Field(min_length=1)
    semantic_role: SemanticRole


class LocalLogicOperand(BaseModel):
    """One non-recursive operand in the LLM-facing logical expression graph."""

    model_config = ConfigDict(extra="forbid")

    child_index: int | None = Field(default=None, ge=0)
    expression_id: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_operand(self) -> Self:
        if (self.child_index is None) == (self.expression_id is None):
            raise ValueError(
                "Logical operand must reference exactly one child_index or "
                "expression_id."
            )
        return self


class LocalLogicNode(BaseModel):
    """One Boolean/cardinality operator node returned by the logic LLM.

    The LLM-facing representation is deliberately flat/non-recursive so provider
    function-calling does not need recursive JSON Schema support. Nested logic is
    expressed through ``expression_id`` references between these nodes.
    """

    model_config = ConfigDict(extra="forbid")

    expression_id: int = Field(ge=0)
    operator: LogicalOperator
    operands: list[LocalLogicOperand] = Field(default_factory=list)
    threshold: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_node(self) -> Self:
        if self.operator == LogicalOperator.NOT:
            if len(self.operands) != 1:
                raise ValueError("NOT requires exactly one operand.")
            if self.threshold is not None:
                raise ValueError("NOT cannot have a threshold.")
            return self

        if self.operator in {LogicalOperator.AND, LogicalOperator.OR}:
            if len(self.operands) < 2:
                raise ValueError("AND/OR require at least two operands.")
            if self.threshold is not None:
                raise ValueError("AND/OR cannot have thresholds.")
            return self

        if self.operator in {
            LogicalOperator.AT_LEAST,
            LogicalOperator.AT_MOST,
            LogicalOperator.EXACTLY,
        }:
            if not self.operands:
                raise ValueError("Cardinality operators require operands.")
            if self.threshold is None:
                raise ValueError("Cardinality operators require a threshold.")
            return self

        raise ValueError(f"Unsupported logical operator: {self.operator}")


class LocalLogicAssertion(BaseModel):
    """Standalone Boolean/cardinality expression asserted by the composite parent.

    Assertions are intentionally expression-only. Ordinary standalone semantic
    children already exist in the semantic memory graph and must not be
    duplicated as ``ASSERT(child)`` entries in the logical layer.
    """

    model_config = ConfigDict(extra="forbid")

    root_expression_id: int = Field(ge=0)
    evidence_text: str = Field(min_length=1)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class LocalLogicRule(BaseModel):
    """Source-explicit conditional rule between two arbitrary logical terms.

    ``condition`` and ``effect`` may each reference either one direct semantic
    child or a Boolean/cardinality expression over direct children. Implication
    is represented here rather than as a Boolean operator.
    """

    model_config = ConfigDict(extra="forbid")

    condition: LocalLogicOperand
    effect: LocalLogicOperand
    evidence_text: str = Field(min_length=1)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class LocalLogicBinding(BaseModel):
    """Deprecated V1 compatibility shape; new extraction uses assertions/rules."""

    model_config = ConfigDict(extra="forbid")

    root_expression_id: int = Field(ge=0)
    effect_child_index: int | None = Field(default=None, ge=0)
    evidence_text: str = Field(min_length=1)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class LocalLogicDecision(BaseModel):
    """Sparse flat expression graph plus standalone assertions and rules."""

    model_config = ConfigDict(extra="forbid")

    expressions: list[LocalLogicNode] = Field(default_factory=list)
    assertions: list[LocalLogicAssertion] = Field(default_factory=list)
    rules: list[LocalLogicRule] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_expression_graph(self) -> Self:
        by_id = {node.expression_id: node for node in self.expressions}
        if len(by_id) != len(self.expressions):
            raise ValueError("Logical expression_id values must be unique.")

        for node in self.expressions:
            for operand in node.operands:
                if (
                    operand.expression_id is not None
                    and operand.expression_id not in by_id
                ):
                    raise ValueError(
                        f"Logical expression {node.expression_id} references "
                        f"unknown expression_id={operand.expression_id}."
                    )

        def validate_ref(ref: LocalLogicOperand, *, label: str) -> None:
            if ref.expression_id is not None and ref.expression_id not in by_id:
                raise ValueError(
                    f"{label} references unknown expression_id={ref.expression_id}."
                )

        for index, assertion in enumerate(self.assertions):
            if assertion.root_expression_id not in by_id:
                raise ValueError(
                    f"Logical assertion[{index}] references unknown "
                    f"root_expression_id={assertion.root_expression_id}."
                )

        for index, rule in enumerate(self.rules):
            validate_ref(rule.condition, label=f"Logical rule[{index}] condition")
            validate_ref(rule.effect, label=f"Logical rule[{index}] effect")

        visiting: set[int] = set()
        visited: set[int] = set()

        def visit(expression_id: int) -> None:
            if expression_id in visited:
                return
            if expression_id in visiting:
                raise ValueError("Logical expression graph contains a cycle.")
            visiting.add(expression_id)
            for operand in by_id[expression_id].operands:
                if operand.expression_id is not None:
                    visit(operand.expression_id)
            visiting.remove(expression_id)
            visited.add(expression_id)

        for expression_id in by_id:
            visit(expression_id)

        return self


class LocalRelationHint(BaseModel):
    """Source-grounded relation explicitly visible within one parent.

    Child indices refer to positions in ``LocalDecompositionDecision.children``.
    These are local hints only; ``GraphBuilder`` decides how to materialize them
    into canonical graph edges.
    """

    model_config = ConfigDict(extra="forbid")

    source_child_index: int = Field(ge=0)
    target_child_index: int = Field(ge=0)
    relation: RelationType
    origin: RelationOrigin = RelationOrigin.SOURCE_EXPLICIT
    evidence_text: str = Field(min_length=1)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_relation(self) -> Self:
        if self.source_child_index == self.target_child_index:
            raise ValueError(
                "Local relation hint cannot relate a child to itself."
            )
        if self.relation == RelationType.DECOMPOSES_INTO:
            raise ValueError(
                "DECOMPOSES_INTO is owned by deterministic hierarchy construction."
            )
        return self


class LocalDecompositionDecision(BaseModel):
    """Decomposition decision for exactly one statement.

    The model does not create canonical graph topology or node IDs. It decides
    whether the current statement is atomic or composite, and for a composite it
    may also expose source-grounded relations that are explicit in the parent.

    - atomic:
        proposition is required
        children and local_relations must be empty

    - composite:
        proposition must be absent
        children must contain at least one direct child
        every local relation must reference valid child indices
    """

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def sanitize_optional_local_relations(cls, raw: Any) -> Any:
        """Drop malformed optional relation hints before nested validation.

        Local relation hints enrich decomposition but are not required to
        preserve the child statements themselves. A single malformed hint from
        the LLM therefore must not abort an otherwise valid decomposition.

        Hard graph invariants remain enforced by ``LocalRelationHint`` for
        relations that survive this sanitization and again by the builder /
        graph layers before materialization.
        """
        if not isinstance(raw, dict):
            return raw

        data = dict(raw)
        children = data.get("children")
        relations = data.get("local_relations")

        if not isinstance(children, list) or not isinstance(relations, list):
            return data

        child_count = len(children)
        sanitized: list[Any] = []
        seen: set[tuple[int, int, str]] = set()

        for relation in relations:
            if not isinstance(relation, dict):
                # Let Pydantic report genuinely malformed non-object values.
                sanitized.append(relation)
                continue

            source_index = relation.get("source_child_index")
            target_index = relation.get("target_child_index")
            relation_type = relation.get("relation")

            if not isinstance(source_index, int) or not isinstance(target_index, int):
                sanitized.append(relation)
                continue

            if source_index == target_index:
                continue

            if not (0 <= source_index < child_count):
                continue
            if not (0 <= target_index < child_count):
                continue

            relation_value = (
                relation_type.value
                if isinstance(relation_type, RelationType)
                else str(relation_type)
            )
            if relation_value == RelationType.DECOMPOSES_INTO.value:
                continue

            key = (source_index, target_index, relation_value)
            if key in seen:
                continue
            seen.add(key)
            sanitized.append(relation)

        data["local_relations"] = sanitized
        return data

    kind: Literal["atomic", "composite"]
    routing_text: str = Field(min_length=1)
    proposition: PropositionPayload | None = None
    children: list[LocalChildStatement] = Field(default_factory=list)
    local_relations: list[LocalRelationHint] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        if self.kind == "atomic":
            if self.proposition is None:
                raise ValueError(
                    "Atomic decomposition requires a proposition."
                )
            if self.children:
                raise ValueError(
                    "Atomic decomposition cannot contain children."
                )
            if self.local_relations:
                raise ValueError(
                    "Atomic decomposition cannot contain local relations."
                )
            return self

        if self.proposition is not None:
            raise ValueError(
                "Composite decomposition cannot contain a proposition."
            )
        if not self.children:
            raise ValueError(
                "Composite decomposition requires at least one child."
            )

        max_index = len(self.children) - 1
        seen_relations: set[tuple[int, int, RelationType]] = set()
        for relation in self.local_relations:
            if relation.source_child_index > max_index:
                raise ValueError(
                    "Local relation source_child_index is outside children."
                )
            if relation.target_child_index > max_index:
                raise ValueError(
                    "Local relation target_child_index is outside children."
                )
            key = (
                relation.source_child_index,
                relation.target_child_index,
                relation.relation,
            )
            if key in seen_relations:
                raise ValueError("Duplicate local relation hint.")
            seen_relations.add(key)

        return self


class MemoryNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    source_root_id: str
    kind: NodeKind
    depth: int = Field(ge=0)
    content: str = Field(min_length=1)
    routing_text: str = Field(min_length=1)
    proposition: PropositionPayload | None = None
    retrieval_embedding: RetrievalEmbedding | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    status: NodeStatus = NodeStatus.ACTIVE
    source_refs: list[SourceReference] = Field(default_factory=list)
    support_node_ids: list[str] = Field(default_factory=list)
    token_estimate: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_retrieval_embedding(self) -> Self:
        if (
            self.retrieval_embedding is not None
            and self.kind != NodeKind.ATOMIC_FACT
        ):
            raise ValueError(
                "Only atomic nodes may have retrieval embeddings."
            )
        return self


class MemoryEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    source_id: str
    target_id: str
    family: EdgeFamily
    relation: RelationType
    directed: bool = True
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    traversal_weight: float = Field(default=1.0, ge=0.0, le=1.0)
    creation_method: CreationMethod
    evidence_node_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DraftNode(BaseModel):
    """One intermediate node definition before canonical IDs are assigned."""

    model_config = ConfigDict(extra="forbid")

    temporary_id: str
    kind: NodeKind
    content: str = Field(min_length=1)
    routing_text: str = Field(min_length=1)
    source_spans: list[SourceSpan] = Field(default_factory=list)
    proposition: PropositionPayload | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DraftLogicExpression(BaseModel):
    """Logical expression whose leaves reference semantic draft temporary IDs."""

    model_config = ConfigDict(extra="forbid")

    semantic_temporary_id: str | None = None
    operator: LogicalOperator | None = None
    operands: list[Self] = Field(default_factory=list)
    threshold: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_expression(self) -> Self:
        is_leaf = self.semantic_temporary_id is not None
        is_operator = self.operator is not None
        if is_leaf == is_operator:
            raise ValueError(
                "Draft logical expression must be exactly one of a semantic "
                "reference or an operator expression."
            )
        if is_leaf:
            if not self.semantic_temporary_id:
                raise ValueError("Draft logical semantic reference cannot be empty.")
            if self.operands:
                raise ValueError("Draft logical semantic references cannot have operands.")
            if self.threshold is not None:
                raise ValueError("Draft logical semantic references cannot have thresholds.")
            return self
        if self.operator == LogicalOperator.NOT:
            if len(self.operands) != 1 or self.threshold is not None:
                raise ValueError("Draft NOT requires one operand and no threshold.")
            return self
        if self.operator in {LogicalOperator.AND, LogicalOperator.OR}:
            if len(self.operands) < 2 or self.threshold is not None:
                raise ValueError("Draft AND/OR require at least two operands and no threshold.")
            return self
        if self.operator in {
            LogicalOperator.AT_LEAST,
            LogicalOperator.AT_MOST,
            LogicalOperator.EXACTLY,
        }:
            if not self.operands or self.threshold is None:
                raise ValueError(
                    "Draft cardinality operators require operands and a threshold."
                )
            return self
        raise ValueError(f"Unsupported draft logical operator: {self.operator}")


class DraftLogicAssertion(BaseModel):
    """Standalone Boolean/cardinality expression bound to draft semantic IDs.

    ``root`` must be an operator expression, never a bare semantic reference.
    Standalone semantic meaning is already represented by ``DraftNode``.
    """

    model_config = ConfigDict(extra="forbid")

    root: DraftLogicExpression
    parent_temporary_id: str
    evidence_spans: list[SourceSpan] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_expression_root(self) -> Self:
        if self.root.semantic_temporary_id is not None or self.root.operator is None:
            raise ValueError(
                "Draft logic assertions must root at a Boolean/cardinality "
                "operator expression, not a bare semantic node reference."
            )
        return self


class DraftLogicRule(BaseModel):
    """Conditional rule whose condition/effect are arbitrary draft logical terms."""

    model_config = ConfigDict(extra="forbid")

    condition: DraftLogicExpression
    effect: DraftLogicExpression
    parent_temporary_id: str
    evidence_spans: list[SourceSpan] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DraftLogicBinding(BaseModel):
    """Deprecated V1 compatibility shape."""

    model_config = ConfigDict(extra="forbid")

    expression: DraftLogicExpression
    effect_temporary_id: str | None = None
    parent_temporary_id: str
    evidence_spans: list[SourceSpan] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DraftHierarchyEdge(BaseModel):
    """A decomposition edge. A null parent means the deterministic raw root."""

    model_config = ConfigDict(extra="forbid")

    parent_temporary_id: str | None = None
    child_temporary_id: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    traversal_weight: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class LocalRelationType(str, Enum):
    """Legacy local relation vocabulary.

    The new lateral-relation pipeline uses RelationType as its canonical
    vocabulary. This enum is retained temporarily for compatibility with any
    existing callers and can be removed once those callers are audited.
    """

    COREFERS_WITH = "corefers_with"
    SAME_ENTITY = "same_entity"
    SAME_EVENT = "same_event"
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    QUALIFIES = "qualifies"
    SERVES_GOAL = "serves_goal"
    REQUIRES = "requires"
    ENABLES = "enables"
    CAUSES = "causes"
    BEFORE = "before"
    AFTER = "after"
    CONDITION_FOR = "condition_for"
    INCLUDED_IN = "included_in"
    EXCLUDED_FROM = "excluded_from"


class DraftRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_temporary_id: str
    target_temporary_id: str
    relation: RelationType
    directed: bool = True

    origin: RelationOrigin
    evidence_spans: list[SourceSpan] = Field(default_factory=list)

    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    traversal_weight: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class UnresolvedReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    source_span: SourceSpan | None = None
    reason: str


class DecompositionDraft(BaseModel):
    """A graph-native draft assembled before canonical graph materialization."""

    model_config = ConfigDict(extra="forbid")

    nodes: list[DraftNode] = Field(default_factory=list)
    hierarchy: list[DraftHierarchyEdge] = Field(default_factory=list)
    relations: list[DraftRelation] = Field(default_factory=list)
    logic_assertions: list[DraftLogicAssertion] = Field(default_factory=list)
    logic_rules: list[DraftLogicRule] = Field(default_factory=list)
    # Compatibility only; V3 builders leave this empty.
    logic_bindings: list[DraftLogicBinding] = Field(default_factory=list)
    unresolved_references: list[UnresolvedReference] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ValidationSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


class ValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    severity: ValidationSeverity
    temporary_id: str | None = None


class BuildValidation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted: bool
    issues: list[ValidationIssue] = Field(default_factory=list)


class LogicExpression(BaseModel):
    """Final logical expression whose leaves reference canonical semantic nodes."""

    model_config = ConfigDict(extra="forbid")

    semantic_node_id: str | None = None
    operator: LogicalOperator | None = None
    operands: list[Self] = Field(default_factory=list)
    threshold: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_expression(self) -> Self:
        is_leaf = self.semantic_node_id is not None
        is_operator = self.operator is not None
        if is_leaf == is_operator:
            raise ValueError(
                "Logic expression must be exactly one of a semantic reference "
                "or an operator expression."
            )
        if is_leaf:
            if not self.semantic_node_id:
                raise ValueError("Semantic node reference cannot be empty.")
            if self.operands:
                raise ValueError("Semantic node references cannot have operands.")
            if self.threshold is not None:
                raise ValueError("Semantic node references cannot have thresholds.")
            return self
        if self.operator == LogicalOperator.NOT:
            if len(self.operands) != 1 or self.threshold is not None:
                raise ValueError("NOT requires one operand and no threshold.")
            return self
        if self.operator in {LogicalOperator.AND, LogicalOperator.OR}:
            if len(self.operands) < 2 or self.threshold is not None:
                raise ValueError("AND/OR require at least two operands and no threshold.")
            return self
        if self.operator in {
            LogicalOperator.AT_LEAST,
            LogicalOperator.AT_MOST,
            LogicalOperator.EXACTLY,
        }:
            if not self.operands or self.threshold is None:
                raise ValueError("Cardinality operators require operands and a threshold.")
            return self
        raise ValueError(f"Unsupported logical operator: {self.operator}")


class LogicAssertion(BaseModel):
    """Materialized standalone Boolean/cardinality expression for one parent.

    A logic assertion adds grouping that the semantic graph alone cannot encode;
    it must never duplicate a single standalone semantic node.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    root: LogicExpression
    parent_node_id: str
    source_refs: list[SourceReference] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_expression_root(self) -> Self:
        if self.root.semantic_node_id is not None or self.root.operator is None:
            raise ValueError(
                "Logic assertions must root at a Boolean/cardinality operator "
                "expression, not a bare semantic node reference."
            )
        return self


class LogicRule(BaseModel):
    """Materialized conditional relation between two arbitrary logical terms."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    condition: LogicExpression
    effect: LogicExpression
    parent_node_id: str
    source_refs: list[SourceReference] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class LogicBinding(BaseModel):
    """Deprecated V1 compatibility shape."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    expression: LogicExpression
    effect_node_id: str | None = None
    parent_node_id: str
    source_refs: list[SourceReference] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class LogicLayer(BaseModel):
    """Boolean/cardinality layer referencing, but not duplicating, semantic nodes."""

    model_config = ConfigDict(extra="forbid")

    assertions: list[LogicAssertion] = Field(default_factory=list)
    rules: list[LogicRule] = Field(default_factory=list)
    # Compatibility only; V3 builders leave this empty.
    bindings: list[LogicBinding] = Field(default_factory=list)


class GraphBuildResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root_id: str
    nodes: list[MemoryNode]
    edges: list[MemoryEdge]
    logic_layer: LogicLayer = Field(default_factory=LogicLayer)
    unresolved_references: list[UnresolvedReference] = Field(default_factory=list)
    validation: BuildValidation

    def node_by_id(self) -> dict[str, MemoryNode]:
        return {node.id: node for node in self.nodes}