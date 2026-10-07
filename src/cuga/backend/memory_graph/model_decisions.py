"""Structured decisions and source-backed chunk contracts for graph models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from .schemas import (
    LocalDecompositionDecision,
    LocalLogicDecision,
    LocalLogicNode,
    LocalLogicOperand,
    LocalLogicSlot,
    LocalNormalizedRelation,
)


class LogicalStructureAssessment(BaseModel):
    """Semantic audit for one proposed local logical-structure decision."""

    complete: bool
    missing_logic: list[str] = Field(default_factory=list)
    unsupported_logic: list[str] = Field(default_factory=list)
    reason: str = ""


class DecompositionAuditResult(BaseModel):
    """One-shot semantic audit result for a composite decomposition.

    The auditor either accepts the proposed decomposition unchanged or returns
    the complete corrected LocalDecompositionDecision itself. There is no
    separate semantic repair model and corrected output is not re-audited.
    """

    action: Literal["pass", "corrected"]
    corrected_decision: LocalDecompositionDecision | None = None
    reason: str = ""

    def model_post_init(self, __context: Any) -> None:
        if self.action == "pass":
            if self.corrected_decision is not None:
                raise ValueError("A passing decomposition audit must not return a correction.")
            return

        if self.corrected_decision is None:
            raise ValueError("A corrected decomposition audit must return corrected_decision.")
        if self.corrected_decision.kind != "composite":
            raise ValueError(
                "A composite decomposition audit may only return a corrected composite decision."
            )


class ChunkSemanticAuditIssue(BaseModel):
    """One semantic defect found after a complete contextual chunk subtree exists."""

    issue_type: Literal[
        "missing_semantics",
        "distorted_semantics",
        "unsupported_semantics",
        "missing_relation",
        "incorrect_relation",
        "logic_error",
        "atomicity",
    ]
    description: str = Field(min_length=1)
    node_temporary_ids: list[str] = Field(default_factory=list)
    logic_slot_ids: list[str] = Field(default_factory=list)


class ChunkSemanticAuditResult(BaseModel):
    """One-shot audit of the fully constructed subtree for one source chunk."""

    complete: bool
    issues: list[ChunkSemanticAuditIssue] = Field(default_factory=list)
    reason: str = ""

    def model_post_init(self, __context: Any) -> None:
        if self.complete and self.issues:
            raise ValueError("A complete chunk semantic audit cannot contain issues.")
        if not self.complete and not self.issues:
            raise ValueError("An incomplete chunk semantic audit must identify at least one issue.")


class AtomicClassificationAssessment(BaseModel):
    """Semantic audit for whether one local statement is truly atomic."""

    classification_valid: bool
    reason: str = ""


class LogicNormalizationClause(BaseModel):
    """One source-explicit logical clause in temporary canonical form."""

    kind: Literal["assertion", "rule"]
    root: LocalLogicOperand | None = None
    condition: LocalLogicOperand | None = None
    effect: LocalLogicOperand | None = None
    evidence_text: str = Field(min_length=1)

    def model_post_init(self, __context: Any) -> None:
        if self.kind == "assertion":
            if self.root is None or self.condition is not None or self.effect is not None:
                raise ValueError("Assertion clause requires root and forbids condition/effect.")
        elif self.kind == "rule":
            if self.root is not None or self.condition is None or self.effect is None:
                raise ValueError("Rule clause requires condition/effect and forbids root.")


class LogicNormalizationDecision(BaseModel):
    """Temporary canonical structure produced from one source statement.

    This is deliberately not the persisted logic representation. The LLM only
    normalizes natural-language operators into binary semantic relations plus
    Boolean slots/expressions/clauses. Python later decides which Boolean clauses
    can be distributed into simple IMPLIES graph edges and which genuinely require
    a compound AST.
    """

    relations: list[LocalNormalizedRelation] = Field(default_factory=list)
    slots: list[LocalLogicSlot] = Field(default_factory=list)
    expressions: list[LocalLogicNode] = Field(default_factory=list)
    clauses: list[LogicNormalizationClause] = Field(default_factory=list)

    def model_post_init(self, __context: Any) -> None:
        slot_ids = {slot.slot_id for slot in self.slots}
        if len(slot_ids) != len(self.slots):
            raise ValueError("Logical slot_id values must be unique.")

        expression_by_id = {item.expression_id: item for item in self.expressions}
        if len(expression_by_id) != len(self.expressions):
            raise ValueError("Logical expression_id values must be unique.")

        def validate_ref(ref: LocalLogicOperand, label: str) -> None:
            if ref.slot_id is not None and ref.slot_id not in slot_ids:
                raise ValueError(f"{label} references unknown slot_id={ref.slot_id}.")
            if ref.expression_id is not None and ref.expression_id not in expression_by_id:
                raise ValueError(f"{label} references unknown expression_id={ref.expression_id}.")

        for expression in self.expressions:
            for operand in expression.operands:
                validate_ref(operand, f"Expression {expression.expression_id}")
        for index, clause in enumerate(self.clauses):
            if clause.root is not None:
                validate_ref(clause.root, f"Clause[{index}] root")
            if clause.condition is not None:
                validate_ref(clause.condition, f"Clause[{index}] condition")
            if clause.effect is not None:
                validate_ref(clause.effect, f"Clause[{index}] effect")

        visiting: set[int] = set()
        visited: set[int] = set()

        def visit(expression_id: int) -> None:
            if expression_id in visited:
                return
            if expression_id in visiting:
                raise ValueError("Logical normalization expression graph contains a cycle.")
            visiting.add(expression_id)
            for operand in expression_by_id[expression_id].operands:
                if operand.expression_id is not None:
                    visit(operand.expression_id)
            visiting.remove(expression_id)
            visited.add(expression_id)

        for expression_id in expression_by_id:
            visit(expression_id)


@dataclass(frozen=True)
class CompiledStructureDecision:
    """Deterministic routing result from temporary normalized structure."""

    logic: LocalLogicDecision
    relations: tuple[LocalNormalizedRelation, ...] = ()


class SemanticSegmentPlan(BaseModel):
    """One contiguous semantic segment over deterministic source blocks."""

    start_block_index: int = Field(ge=0)
    end_block_index: int = Field(ge=0)


class SemanticSegmentationDecision(BaseModel):
    """One semantic partition of the currently active source-block range."""

    chunks: list[SemanticSegmentPlan] = Field(min_length=1)


class FinalChunkContextPlan(BaseModel):
    """Contextualization output for one finalized semantic leaf chunk."""

    chunk_index: int = Field(ge=0)
    context_block_indices: list[int] = Field(default_factory=list)
    contextualized_text: str = Field(min_length=1)


class FinalChunkContextDecision(BaseModel):
    """Contextualization outputs for every finalized semantic leaf chunk."""

    chunks: list[FinalChunkContextPlan] = Field(min_length=1)


class ContextualChunkIssue(BaseModel):
    """One localized semantic defect in a contextualized leaf chunk."""

    chunk_index: int = Field(ge=0)
    issue: str = Field(min_length=1)


class ContextualChunkingAssessment(BaseModel):
    """Semantic audit of finalized contextualized leaf chunks.

    Every negative audit is localized to exact ``chunk_index`` values so the
    repair pass can be monotonic: good leaves are preserved byte-for-byte and
    only defective leaves are regenerated.
    """

    complete: bool
    issues: list[ContextualChunkIssue] = Field(default_factory=list)
    reason: str = ""


class FinalChunkContextRepair(BaseModel):
    """Targeted replacements for only the contextualized leaves under repair."""

    replacements: list[FinalChunkContextPlan] = Field(min_length=1)


@dataclass(frozen=True)
class SemanticLeafChunk:
    """One exact semantic leaf selected by recursive LLM segmentation."""

    start_block_index: int
    end_block_index: int
    start: int
    end: int


@dataclass(frozen=True)
class ContextSourceBlock:
    """One exact source block presented to the contextual chunking model."""

    index: int
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class ContextualSourceChunk:
    """One source-backed chunk plus a minimal self-contained interpretation.

    ``source_text`` and ``context_source_texts`` are authoritative excerpts from
    the original source. ``contextualized_text`` is derived text used only as the
    semantic-decomposition input; it is never authoritative evidence.
    """

    source_text: str
    contextualized_text: str
    start: int
    end: int
    context_source_texts: tuple[str, ...] = ()
    context_spans: tuple[tuple[int, int], ...] = ()

    @property
    def text(self) -> str:
        """Compatibility alias for callers that consume decomposition text."""
        return self.contextualized_text
