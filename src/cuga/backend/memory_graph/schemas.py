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
    IMPLIES = "implies"
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
    """Lightweight lexical retrieval keys for one atomic proposition.

    The authoritative semantics remain in ``MemoryNode.content``. This payload is
    intentionally limited to coarse subject-predicate-object lexical anchors used
    to estimate relatedness during candidate retrieval. Each field is a list so a
    short atomic statement may preserve multiple reasonable lexical perspectives
    without forcing one canonical grammatical parse.

    The ``before`` validator is also the migration/provider-normalization boundary:
    legacy singular ``subject``/``predicate``/``object`` values are promoted to
    one-element lists, common verb/action aliases are mapped to ``predicates``, and
    unrelated legacy metadata is discarded rather than persisted.
    """

    model_config = ConfigDict(extra="forbid")

    subjects: list[str] = Field(default_factory=list)
    predicates: list[str] = Field(default_factory=list)
    objects: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_payload(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value

        raw = dict(value)

        def _as_lexical_list(*keys: str) -> list[str]:
            values: list[Any] = []
            for key in keys:
                candidate = raw.get(key)
                if candidate is None:
                    continue
                if isinstance(candidate, (list, tuple, set)):
                    values.extend(candidate)
                else:
                    values.append(candidate)

            cleaned: list[str] = []
            seen: set[str] = set()
            for item in values:
                if item is None:
                    continue
                raw_text = str(item)
                # Exact one-space is a reserved zero-signal placeholder for
                # retrieval metadata that is intentionally unavailable.
                if raw_text == " ":
                    if "__retrieval_blank__" not in seen:
                        seen.add("__retrieval_blank__")
                        cleaned.append(" ")
                    continue
                text = raw_text.strip()
                if not text:
                    continue
                dedupe_key = text.casefold()
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                cleaned.append(text)
            return cleaned

        # Persist only lexical S/P/O lists. All other source semantics remain in
        # authoritative node content and relation/logic structures. Singular keys
        # keep old cached graphs and provider drift forward-compatible.
        return {
            "subjects": _as_lexical_list("subjects", "subject"),
            "predicates": _as_lexical_list(
                "predicates",
                "predicate",
                "verbs",
                "verb",
                "actions",
                "action",
            ),
            "objects": _as_lexical_list("objects", "object"),
        }



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
    """One atomic node that may have a lateral relation to an anchor node.

    ``context_paths`` preserves the candidate occurrence's semantic ancestry.
    Identical leaf wording can denote different propositions when its referent or
    scope is inherited from different parents, so relation classification must be
    able to inspect the surrounding hierarchy rather than compare flat strings.
    """

    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    routing_text: str = Field(min_length=1)
    proposition: PropositionPayload | None = None
    context_paths: list[list[str]] = Field(default_factory=list)


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
    anchor_context_paths: list[list[str]] = Field(default_factory=list)
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
    metadata: dict[str, Any] = Field(default_factory=dict)


class CompositeSemanticAdditionRepair(BaseModel):
    """Add-only semantic repair for an already-valid composite decomposition.

    The repair model may only contribute new direct children. Existing children,
    routing text, local relations, and graph structure are preserved by Python.
    """

    model_config = ConfigDict(extra="forbid")

    additions: list[LocalChildStatement] = Field(min_length=1)


class LogicPropositionCandidate(BaseModel):
    """One already-existing semantic proposition available to logic extraction."""

    model_config = ConfigDict(extra="forbid")

    proposition_index: int = Field(ge=0)
    temporary_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    routing_text: str = Field(min_length=1)
    semantic_role: str | None = None


class LocalNormalizedRelation(BaseModel):
    """One source-explicit binary relation between semantic propositions.

    This is an LLM-facing/local normalization type, not a persistent graph edge.
    The endpoints are indices into the AVAILABLE_PROPOSITIONS supplied to the
    normalization model. Python later resolves them to semantic node IDs and
    materializes a ``DraftRelation``. Irreducible Boolean structure remains in
    ``LocalLogicDecision`` instead.
    """

    model_config = ConfigDict(extra="forbid")

    source_proposition_index: int = Field(ge=0)
    target_proposition_index: int = Field(ge=0)
    relation: RelationType
    evidence_text: str = Field(min_length=1)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_relation(self) -> Self:
        if self.source_proposition_index == self.target_proposition_index:
            raise ValueError("Normalized relation cannot relate a proposition to itself.")
        if self.relation == RelationType.DECOMPOSES_INTO:
            raise ValueError(
                "DECOMPOSES_INTO is hierarchical and cannot be a normalized lateral relation."
            )
        return self


class LocalLogicSlot(BaseModel):
    """One logical proposition slot discovered in source text.

    A slot is NOT a semantic graph node. ``proposition_index`` is populated only
    when the slot is already represented by one of the supplied semantic
    propositions. Otherwise the slot remains unresolved and may be bound to a
    verified semantic node later at runtime.
    """

    model_config = ConfigDict(extra="forbid")

    slot_id: int = Field(ge=0)
    source_text: str = Field(min_length=1)
    proposition_index: int | None = Field(default=None, ge=0)
    proposition_value: bool = True
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class LocalLogicOperand(BaseModel):
    """One non-recursive logical term reference returned by the model.

    Direct slot references are signed literals. ``value=False`` means the
    logical negation of that slot and lets simple relations such as ``A -> NOT B``
    remain literal-to-literal constraints instead of requiring a one-node AST.
    Expression references are always positive; negate a compound expression with
    an explicit NOT expression node.
    """

    model_config = ConfigDict(extra="forbid")

    slot_id: int | None = Field(default=None, ge=0)
    expression_id: int | None = Field(default=None, ge=0)
    value: bool = True

    @model_validator(mode="after")
    def validate_operand(self) -> Self:
        if (self.slot_id is None) == (self.expression_id is None):
            raise ValueError(
                "Logical operand must reference exactly one slot_id or "
                "expression_id."
            )
        if self.expression_id is not None and self.value is not True:
            raise ValueError(
                "Only direct slot operands may use value=false; negate compound "
                "expressions with an explicit NOT node."
            )
        return self


class LocalLogicNode(BaseModel):
    """One Boolean/cardinality operator in the flat LLM-facing AST."""

    model_config = ConfigDict(extra="forbid")

    expression_id: int = Field(ge=0)
    operator: LogicalOperator
    operands: list[LocalLogicOperand] = Field(default_factory=list)
    threshold: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_node(self) -> Self:
        if self.operator == LogicalOperator.NOT:
            if len(self.operands) != 1 or self.threshold is not None:
                raise ValueError("NOT requires exactly one operand and no threshold.")
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
            if self.threshold > len(self.operands):
                raise ValueError("Cardinality threshold cannot exceed operand count.")
            return self
        raise ValueError(f"Unsupported logical operator: {self.operator}")


class LocalLogicAssertion(BaseModel):
    """A source-explicit logical term asserted to hold."""

    model_config = ConfigDict(extra="forbid")

    root: LocalLogicOperand
    evidence_text: str = Field(min_length=1)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class LocalLogicRule(BaseModel):
    """A source-explicit condition -> effect rule."""

    model_config = ConfigDict(extra="forbid")

    condition: LocalLogicOperand
    effect: LocalLogicOperand
    evidence_text: str = Field(min_length=1)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class LocalLogicDecision(BaseModel):
    """Partial propositional representation over persistent slots.

    Direct slot operands represent simple signed literals. ``expressions`` are
    reserved for genuinely compound Boolean/cardinality terms. Slots may be bound
    to an existing semantic proposition or intentionally left unresolved. The
    decision never creates semantic graph nodes.
    """

    model_config = ConfigDict(extra="forbid")

    slots: list[LocalLogicSlot] = Field(default_factory=list)
    expressions: list[LocalLogicNode] = Field(default_factory=list)
    assertions: list[LocalLogicAssertion] = Field(default_factory=list)
    rules: list[LocalLogicRule] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_expression_graph(self) -> Self:
        slot_ids = {slot.slot_id for slot in self.slots}
        if len(slot_ids) != len(self.slots):
            raise ValueError("Logical slot_id values must be unique.")

        by_id = {node.expression_id: node for node in self.expressions}
        if len(by_id) != len(self.expressions):
            raise ValueError("Logical expression_id values must be unique.")

        def validate_ref(ref: LocalLogicOperand, *, label: str) -> None:
            if ref.slot_id is not None and ref.slot_id not in slot_ids:
                raise ValueError(f"{label} references unknown slot_id={ref.slot_id}.")
            if ref.expression_id is not None and ref.expression_id not in by_id:
                raise ValueError(
                    f"{label} references unknown expression_id={ref.expression_id}."
                )

        for node in self.expressions:
            for operand in node.operands:
                validate_ref(
                    operand,
                    label=f"Logical expression {node.expression_id}",
                )
        for index, assertion in enumerate(self.assertions):
            validate_ref(assertion.root, label=f"Logical assertion[{index}]")
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


class LogicSlotCandidate(BaseModel):
    """One persistent logic slot considered for binding to a semantic occurrence.

    ``context_paths`` contains the semantic ancestry of the slot's originating
    occurrence and/or already-bound occurrences. It is matching-time evidence;
    it does not collapse or replace the underlying semantic nodes.
    """

    model_config = ConfigDict(extra="forbid")

    slot_id: str = Field(min_length=1)
    source_text: str = Field(min_length=1)
    bound_node_ids: list[str] = Field(default_factory=list)
    context_paths: list[list[str]] = Field(default_factory=list)


class LogicSlotBindingRequest(BaseModel):
    """Constrained contextual semantic-equivalence request for slot augmentation."""

    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1)
    node_content: str = Field(min_length=1)
    node_routing_text: str = Field(min_length=1)
    node_context_paths: list[list[str]] = Field(default_factory=list)
    candidates: list[LogicSlotCandidate] = Field(default_factory=list)


class LogicSlotBindingDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot_id: str = Field(min_length=1)
    value: bool = True
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class LogicSlotBindingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bindings: list[LogicSlotBindingDecision] = Field(default_factory=list)


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
    metadata: dict[str, Any] = Field(default_factory=dict)

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
    children: list[LocalChildStatement] = Field(default_factory=list)
    local_relations: list[LocalRelationHint] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        if self.kind == "atomic":
            if self.children:
                raise ValueError(
                    "Atomic decomposition cannot contain children."
                )
            if self.local_relations:
                raise ValueError(
                    "Atomic decomposition cannot contain local relations."
                )
            return self

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
    logic_asserted: bool = True
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
    logic_asserted: bool = True
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DraftLogicSlotBinding(BaseModel):
    """One construction-time semantic binding for a logic slot."""

    model_config = ConfigDict(extra="forbid")

    semantic_temporary_id: str = Field(min_length=1)
    value: bool = True


class DraftLogicSlot(BaseModel):
    """Construction-time logic slot with optional semantic-node bindings."""

    model_config = ConfigDict(extra="forbid")

    temporary_id: str = Field(min_length=1)
    source_text: str = Field(min_length=1)
    bindings: list[DraftLogicSlotBinding] = Field(default_factory=list)
    evidence_spans: list[SourceSpan] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_bindings(self) -> Self:
        ids = [binding.semantic_temporary_id for binding in self.bindings]
        if len(ids) != len(set(ids)):
            raise ValueError("Draft logic-slot bindings must reference unique semantic nodes.")
        return self

    @property
    def bound_semantic_temporary_ids(self) -> list[str]:
        return [binding.semantic_temporary_id for binding in self.bindings]


class DraftLogicExpression(BaseModel):
    """Internal logical term whose leaves reference signed draft logic slots.

    This is only an intermediate construction type. Literal-only rules are
    materialized as lightweight ``LogicRelation`` objects; an AST is persisted
    only when a genuinely compound Boolean/cardinality expression is required.
    """

    model_config = ConfigDict(extra="forbid")

    slot_temporary_id: str | None = None
    slot_value: bool = True
    operator: LogicalOperator | None = None
    operands: list[Self] = Field(default_factory=list)
    threshold: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_expression(self) -> Self:
        is_leaf = self.slot_temporary_id is not None
        is_operator = self.operator is not None
        if is_leaf == is_operator:
            raise ValueError(
                "Draft logical expression must be exactly one of a slot reference "
                "or an operator expression."
            )
        if is_leaf:
            if not self.slot_temporary_id:
                raise ValueError("Draft logical slot reference cannot be empty.")
            if self.operands or self.threshold is not None:
                raise ValueError("Draft logical slot references cannot have operands/thresholds.")
            return self
        if self.slot_value is not True:
            raise ValueError("Operator expressions cannot carry slot_value=false.")
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
                raise ValueError("Draft cardinality operators require operands and a threshold.")
            if self.threshold > len(self.operands):
                raise ValueError("Draft cardinality threshold cannot exceed operand count.")
            return self
        raise ValueError(f"Unsupported draft logical operator: {self.operator}")


class DraftLogicAssertion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root: DraftLogicExpression
    parent_temporary_id: str
    evidence_spans: list[SourceSpan] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DraftLogicRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition: DraftLogicExpression
    effect: DraftLogicExpression
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


class DecompositionDraft(BaseModel):
    """A graph-native draft assembled before canonical graph materialization."""

    model_config = ConfigDict(extra="forbid")

    nodes: list[DraftNode] = Field(default_factory=list)
    hierarchy: list[DraftHierarchyEdge] = Field(default_factory=list)
    relations: list[DraftRelation] = Field(default_factory=list)
    logic_slots: list[DraftLogicSlot] = Field(default_factory=list)
    logic_assertions: list[DraftLogicAssertion] = Field(default_factory=list)
    logic_rules: list[DraftLogicRule] = Field(default_factory=list)
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


class LogicSlotBinding(BaseModel):
    """One semantic-node binding to a Boolean logic slot.

    ``value`` is the truth value of the slot when the bound semantic node is an
    established assertion. This lets a verified negative proposition bind to the
    same underlying slot without inventing a separate SAT variable.
    """

    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1)
    value: bool = True


class LogicSlot(BaseModel):
    """Persistent logical proposition slot.

    Bindings may grow during runtime as verified semantic propositions surface.
    An empty binding list is a valid unresolved slot.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    source_text: str = Field(min_length=1)
    bindings: list[LogicSlotBinding] = Field(default_factory=list)
    source_refs: list[SourceReference] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_bindings(self) -> Self:
        ids = [binding.node_id for binding in self.bindings]
        if len(ids) != len(set(ids)):
            raise ValueError("Logic-slot bindings must reference unique semantic nodes.")
        return self

    @property
    def bound_node_ids(self) -> list[str]:
        return [binding.node_id for binding in self.bindings]


class LogicLiteral(BaseModel):
    """A signed reference to one persistent logic slot."""

    model_config = ConfigDict(extra="forbid")

    slot_id: str = Field(min_length=1)
    value: bool = True


class LogicRelationType(str, Enum):
    """Lightweight truth-functional relations that do not require an AST."""

    IMPLIES = "implies"


class LogicRelation(BaseModel):
    """A simple literal-to-literal logical constraint.

    Simple conditionals, requirements, prohibitions with a condition, and other
    direct implications live here. They compile directly to CNF without creating
    an expression tree.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    relation: LogicRelationType = LogicRelationType.IMPLIES
    antecedent: LogicLiteral
    consequent: LogicLiteral
    parent_node_id: str
    source_refs: list[SourceReference] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class LogicLiteralAssertion(BaseModel):
    """A source-explicit assertion of one signed logical literal."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    literal: LogicLiteral
    parent_node_id: str
    source_refs: list[SourceReference] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class LogicExpression(BaseModel):
    """Persistent AST used only for genuinely compound Boolean expressions."""

    model_config = ConfigDict(extra="forbid")

    slot_id: str | None = None
    operator: LogicalOperator | None = None
    operands: list[Self] = Field(default_factory=list)
    threshold: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_expression(self) -> Self:
        is_leaf = self.slot_id is not None
        is_operator = self.operator is not None
        if is_leaf == is_operator:
            raise ValueError(
                "Logic expression must be exactly one of a slot reference or an "
                "operator expression."
            )
        if is_leaf:
            if not self.slot_id:
                raise ValueError("Logic slot reference cannot be empty.")
            if self.operands or self.threshold is not None:
                raise ValueError("Logic slot references cannot have operands/thresholds.")
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
            if self.threshold > len(self.operands):
                raise ValueError("Cardinality threshold cannot exceed operand count.")
            return self
        raise ValueError(f"Unsupported logical operator: {self.operator}")


class LogicCompoundAssertion(BaseModel):
    """A source-explicit assertion that genuinely requires a compound AST."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    root: LogicExpression
    parent_node_id: str
    source_refs: list[SourceReference] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_compound_root(self) -> Self:
        if self.root.operator is None:
            raise ValueError(
                "LogicCompoundAssertion requires a compound expression; use "
                "LogicLiteralAssertion for one literal."
            )
        return self


class LogicCompoundRule(BaseModel):
    """A condition -> effect rule with at least one genuinely compound side."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    condition: LogicExpression
    effect: LogicExpression
    parent_node_id: str
    source_refs: list[SourceReference] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_compound_side(self) -> Self:
        if self.condition.operator is None and self.effect.operator is None:
            raise ValueError(
                "LogicCompoundRule requires a compound side; use LogicRelation "
                "for literal-to-literal implication."
            )
        return self


class LogicLayer(BaseModel):
    """Persistent propositional layer with simple relations plus optional ASTs.

    ``relations`` and ``literal_assertions`` are the normal representation for
    literal-level logic. ``compound_rules`` and ``compound_assertions`` are used
    only when AND/OR/cardinality/nested Boolean structure cannot be represented
    losslessly as a single signed-literal relation/assertion.
    """

    model_config = ConfigDict(extra="forbid")

    slots: list[LogicSlot] = Field(default_factory=list)
    literal_assertions: list[LogicLiteralAssertion] = Field(default_factory=list)
    relations: list[LogicRelation] = Field(default_factory=list)
    compound_assertions: list[LogicCompoundAssertion] = Field(default_factory=list)
    compound_rules: list[LogicCompoundRule] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_slot_references(self) -> Self:
        slot_ids = {slot.id for slot in self.slots}
        if len(slot_ids) != len(self.slots):
            raise ValueError("LogicLayer slot IDs must be unique.")

        logical_ids = [
            *(item.id for item in self.literal_assertions),
            *(item.id for item in self.relations),
            *(item.id for item in self.compound_assertions),
            *(item.id for item in self.compound_rules),
        ]
        if len(logical_ids) != len(set(logical_ids)):
            raise ValueError("LogicLayer logical constraint IDs must be unique.")

        def refs(expr: LogicExpression):
            if expr.slot_id is not None:
                yield expr.slot_id
                return
            for operand in expr.operands:
                yield from refs(operand)

        for assertion in self.literal_assertions:
            if assertion.literal.slot_id not in slot_ids:
                raise ValueError(
                    f"Logic literal assertion references unknown slot: {assertion.literal.slot_id}"
                )
        for relation in self.relations:
            unknown = {
                relation.antecedent.slot_id,
                relation.consequent.slot_id,
            } - slot_ids
            if unknown:
                raise ValueError(f"Logic relation references unknown slots: {sorted(unknown)}")
        for assertion in self.compound_assertions:
            unknown = set(refs(assertion.root)) - slot_ids
            if unknown:
                raise ValueError(
                    f"Logic compound assertion references unknown slots: {sorted(unknown)}"
                )
        for rule in self.compound_rules:
            unknown = (set(refs(rule.condition)) | set(refs(rule.effect))) - slot_ids
            if unknown:
                raise ValueError(
                    f"Logic compound rule references unknown slots: {sorted(unknown)}"
                )
        return self


class GraphBuildResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root_id: str
    nodes: list[MemoryNode]
    edges: list[MemoryEdge]
    logic_layer: LogicLayer = Field(default_factory=LogicLayer)
    validation: BuildValidation

    def node_by_id(self) -> dict[str, MemoryNode]:
        return {node.id: node for node in self.nodes}