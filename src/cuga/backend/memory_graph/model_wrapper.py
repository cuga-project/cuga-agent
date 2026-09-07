from __future__ import annotations

import json
import os
import re
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from loguru import logger
from pydantic import BaseModel, Field, ValidationError

from cuga.backend.llm.models import LLMManager
from cuga.config import settings

from .schemas import (
    GraphBuildRequest,
    LocalDecompositionDecision,
    LocalLogicAssertion,
    LocalLogicDecision,
    LocalLogicNode,
    LocalLogicOperand,
    LocalLogicRule,
    LocalLogicSlot,
    LocalNormalizedRelation,
    LogicPropositionCandidate,
    LogicalOperator,
    LogicSlotBindingRequest,
    LogicSlotBindingResponse,
    RelationBuildRequest,
    RelationBuildResponse,
    RelationDecision,
    RelationDirection,
    RelationType,
)

from .decomposition_guidance_spacy import extract_decomposition_guidance_spacy
from .logging_utils import memory_graph_trace_enabled


from .logic_slot_binding_deberta import (
    MATCHER_MODEL_NAME as _LOGIC_SLOT_MATCHER_NAME,
    call_logic_slot_binding_model as _call_logic_slot_binding_backend,
)


class DecompositionModelNotConfiguredError(RuntimeError):
    """Kept for compatibility with the original stub."""


class PromptDecompositionModelError(RuntimeError):
    """Raised when the prompt-decomposition model cannot produce a valid decision."""


class ContextualChunkingModelError(RuntimeError):
    """Raised when source contextualization cannot produce a safe chunk plan."""


class RelationModelError(RuntimeError):
    """Raised when the relation model cannot produce a valid relation decision."""


class LogicalStructureModelError(RuntimeError):
    """Raised when propositional logic extraction cannot be recovered."""


class LogicSlotBindingModelError(RuntimeError):
    """Raised when runtime logic-slot binding cannot be recovered."""


def _capture_logic_slot_binding_dataset(
    request: LogicSlotBindingRequest,
    response: LogicSlotBindingResponse,
) -> None:
    """Optionally record candidate-pair labels to a dedicated JSONL dataset.

    Pair-level records are intentionally NOT emitted to the normal Loguru stream:
    they are extremely verbose and made ordinary verifier logs unusable. Set
    ``CUGA_LOGIC_MATCH_DATASET`` to a file path when dataset capture is needed.
    ``label_value`` is the matched slot polarity; ``label_match`` is the
    semantic-identity decision.
    """
    output_path = os.getenv("CUGA_LOGIC_MATCH_DATASET", "").strip()
    if not output_path:
        return

    by_slot: dict[str, list[Any]] = {}
    for binding in response.bindings:
        by_slot.setdefault(binding.slot_id, []).append(binding)

    rows: list[dict[str, Any]] = []
    for candidate in request.candidates:
        matched = by_slot.get(candidate.slot_id, [])
        values = sorted({item.value for item in matched})
        label_class = (
            "no_match"
            if not values
            else "positive"
            if values == [True]
            else "negative"
            if values == [False]
            else "ambiguous"
        )
        row = {
            "node_id": request.node_id,
            "node_content": request.node_content,
            "node_routing_text": request.node_routing_text,
            "node_context_paths": request.node_context_paths,
            "slot_id": candidate.slot_id,
            "slot_source_text": candidate.source_text,
            "slot_context_paths": candidate.context_paths,
            "slot_bound_node_ids": candidate.bound_node_ids,
            "label_class": label_class,
            "label_match": bool(values),
            "label_value": values[0] if len(values) == 1 else None,
            "matcher": _LOGIC_SLOT_MATCHER_NAME,
            "matcher_confidence": (
                max((item.confidence for item in matched), default=None)
            ),
            # Retained for compatibility with the earlier extraction scripts.
            "teacher_confidence": (
                max((item.confidence for item in matched), default=None)
            ),
        }
        rows.append(row)

    path = Path(output_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


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
                raise ValueError(
                    "A passing decomposition audit must not return a correction."
                )
            return

        if self.corrected_decision is None:
            raise ValueError(
                "A corrected decomposition audit must return corrected_decision."
            )
        if self.corrected_decision.kind != "composite":
            raise ValueError(
                "A composite decomposition audit may only return a corrected "
                "composite decision."
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
                raise ValueError(
                    "Assertion clause requires root and forbids condition/effect."
                )
        elif self.kind == "rule":
            if self.root is not None or self.condition is None or self.effect is None:
                raise ValueError(
                    "Rule clause requires condition/effect and forbids root."
                )


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
                raise ValueError(
                    f"{label} references unknown expression_id={ref.expression_id}."
                )

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
                raise ValueError(
                    "Logical normalization expression graph contains a cycle."
                )
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


@dataclass(frozen=True)
class DecompositionSourceChunk:
    """One exact, contiguous source slice for semantic decomposition.

    ``text`` is always exactly ``source[start:end]``. Structural chunking never
    paraphrases or drops source text; it only chooses deterministic boundaries.
    """

    text: str
    start: int
    end: int


_DEFAULT_SOURCE_CHUNK_MAX_CHARS = 2500
_DEFAULT_SOURCE_CHUNK_TARGET_CHARS = 2000
_MIN_HARD_SPLIT_FRACTION = 0.60

_MARKDOWN_HEADING_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+\S")
_FENCE_RE = re.compile(r"^[ \t]{0,3}(```+|~~~+)")


def split_source_for_decomposition(
    text: str,
    *,
    max_chars: int = _DEFAULT_SOURCE_CHUNK_MAX_CHARS,
    target_chars: int = _DEFAULT_SOURCE_CHUNK_TARGET_CHARS,
) -> list[DecompositionSourceChunk]:
    """Deterministically partition a long source into exact semantic-work chunks.

    This is structural segmentation only; it performs no semantic summarization
    or rewriting.

    Boundary preference:
    1. Markdown heading sections.
    2. Paragraph / blank-line blocks.
    3. Line boundaries, useful for lists and tables.
    4. Sentence-like punctuation boundaries.
    5. Whitespace-aware hard cuts as a final fallback.

    The returned chunks form a lossless, ordered partition of ``text``.
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if target_chars <= 0:
        raise ValueError("target_chars must be positive")
    if target_chars > max_chars:
        raise ValueError("target_chars cannot exceed max_chars")
    if not text:
        return []

    if len(text) <= max_chars:
        return [DecompositionSourceChunk(text=text, start=0, end=len(text))]

    chunks: list[DecompositionSourceChunk] = []
    for section_start, section_end in _markdown_section_spans(text):
        if section_end - section_start <= max_chars:
            chunks.append(
                DecompositionSourceChunk(
                    text=text[section_start:section_end],
                    start=section_start,
                    end=section_end,
                )
            )
            continue

        for chunk_start, chunk_end in _split_large_span(
            text,
            start=section_start,
            end=section_end,
            max_chars=max_chars,
            target_chars=target_chars,
        ):
            chunks.append(
                DecompositionSourceChunk(
                    text=text[chunk_start:chunk_end],
                    start=chunk_start,
                    end=chunk_end,
                )
            )

    chunks = _merge_small_adjacent_chunks(
        text,
        chunks,
        max_chars=max_chars,
        target_chars=target_chars,
    )

    _validate_source_chunk_partition(text, chunks, max_chars=max_chars)

    logger.debug(
        "Deterministic source chunking complete: chars={} chunks={} "
        "max_chars={} target_chars={} sizes={}",
        len(text),
        len(chunks),
        max_chars,
        target_chars,
        [chunk.end - chunk.start for chunk in chunks],
    )
    return chunks


def _markdown_section_spans(text: str) -> list[tuple[int, int]]:
    """Split at Markdown headings while ignoring heading-like text in fences."""
    lines = text.splitlines(keepends=True)
    if not lines:
        return [(0, len(text))]

    heading_starts: list[int] = []
    offset = 0
    fence_marker: str | None = None

    for line in lines:
        stripped_line = line.rstrip("\r\n")
        fence_match = _FENCE_RE.match(stripped_line)

        if fence_match is not None:
            marker_char = fence_match.group(1)[0]
            if fence_marker is None:
                fence_marker = marker_char
            elif marker_char == fence_marker:
                fence_marker = None
        elif fence_marker is None and _MARKDOWN_HEADING_RE.match(stripped_line):
            heading_starts.append(offset)

        offset += len(line)

    starts = [0]
    starts.extend(start for start in heading_starts if start != 0)
    starts = sorted(set(starts))

    spans: list[tuple[int, int]] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(text)
        if start < end:
            spans.append((start, end))

    return spans or [(0, len(text))]


def _split_large_span(
    text: str,
    *,
    start: int,
    end: int,
    max_chars: int,
    target_chars: int,
) -> list[tuple[int, int]]:
    """Split one oversized structural section without changing source text."""
    paragraph_spans = _paragraph_spans(text, start=start, end=end)
    atomic_spans: list[tuple[int, int]] = []

    for paragraph_start, paragraph_end in paragraph_spans:
        if paragraph_end - paragraph_start <= max_chars:
            atomic_spans.append((paragraph_start, paragraph_end))
        else:
            atomic_spans.extend(
                _split_oversized_block(
                    text,
                    start=paragraph_start,
                    end=paragraph_end,
                    max_chars=max_chars,
                    target_chars=target_chars,
                )
            )

    return _pack_contiguous_spans(
        atomic_spans,
        max_chars=max_chars,
        target_chars=target_chars,
    )


def _paragraph_spans(
    text: str,
    *,
    start: int,
    end: int,
) -> list[tuple[int, int]]:
    """Return exact paragraph-like spans, retaining blank-line separators."""
    segment = text[start:end]
    boundaries = [start]
    for match in re.finditer(r"\r?\n[ \t]*\r?\n+", segment):
        boundaries.append(start + match.end())
    boundaries.append(end)
    boundaries = sorted(set(boundaries))

    spans = [
        (boundaries[index], boundaries[index + 1])
        for index in range(len(boundaries) - 1)
        if boundaries[index] < boundaries[index + 1]
    ]
    return spans or [(start, end)]


def _split_oversized_block(
    text: str,
    *,
    start: int,
    end: int,
    max_chars: int,
    target_chars: int,
) -> list[tuple[int, int]]:
    """Prefer line boundaries, then sentences, then whitespace-aware hard cuts."""
    line_spans = _line_spans(text, start=start, end=end)

    if len(line_spans) > 1:
        expanded: list[tuple[int, int]] = []
        for line_start, line_end in line_spans:
            if line_end - line_start <= max_chars:
                expanded.append((line_start, line_end))
            else:
                expanded.extend(
                    _sentence_or_hard_spans(
                        text,
                        start=line_start,
                        end=line_end,
                        max_chars=max_chars,
                        target_chars=target_chars,
                    )
                )
        return _pack_contiguous_spans(
            expanded,
            max_chars=max_chars,
            target_chars=target_chars,
        )

    return _sentence_or_hard_spans(
        text,
        start=start,
        end=end,
        max_chars=max_chars,
        target_chars=target_chars,
    )


def _line_spans(
    text: str,
    *,
    start: int,
    end: int,
) -> list[tuple[int, int]]:
    """Return exact line spans with newline characters retained."""
    segment = text[start:end]
    spans: list[tuple[int, int]] = []
    cursor = start

    for line in segment.splitlines(keepends=True):
        line_end = cursor + len(line)
        spans.append((cursor, line_end))
        cursor = line_end

    if cursor < end:
        spans.append((cursor, end))
    return spans or [(start, end)]


def _sentence_or_hard_spans(
    text: str,
    *,
    start: int,
    end: int,
    max_chars: int,
    target_chars: int,
) -> list[tuple[int, int]]:
    """Split an oversized single-line block on sentence-like boundaries."""
    segment = text[start:end]
    boundaries = [start]

    for match in re.finditer(r"""[.!?](?:["')\]]+)?[ \t]+""", segment):
        boundaries.append(start + match.end())

    boundaries.append(end)
    boundaries = sorted(set(boundaries))
    candidate_spans = [
        (boundaries[index], boundaries[index + 1])
        for index in range(len(boundaries) - 1)
        if boundaries[index] < boundaries[index + 1]
    ]

    if len(candidate_spans) > 1:
        expanded: list[tuple[int, int]] = []
        for candidate_start, candidate_end in candidate_spans:
            if candidate_end - candidate_start <= max_chars:
                expanded.append((candidate_start, candidate_end))
            else:
                expanded.extend(
                    _hard_split_span(
                        text,
                        start=candidate_start,
                        end=candidate_end,
                        max_chars=max_chars,
                    )
                )
        return _pack_contiguous_spans(
            expanded,
            max_chars=max_chars,
            target_chars=target_chars,
        )

    return _hard_split_span(
        text,
        start=start,
        end=end,
        max_chars=max_chars,
    )


def _hard_split_span(
    text: str,
    *,
    start: int,
    end: int,
    max_chars: int,
) -> list[tuple[int, int]]:
    """Final fallback: exact whitespace-aware cuts bounded by ``max_chars``."""
    spans: list[tuple[int, int]] = []
    cursor = start
    min_cut_distance = max(1, int(max_chars * _MIN_HARD_SPLIT_FRACTION))

    while end - cursor > max_chars:
        hard_limit = cursor + max_chars
        search_floor = cursor + min_cut_distance
        cut = hard_limit

        whitespace_matches = list(
            re.finditer(r"\s+", text[search_floor:hard_limit])
        )
        if whitespace_matches:
            cut = search_floor + whitespace_matches[-1].end()
        if cut <= cursor:
            cut = hard_limit

        spans.append((cursor, cut))
        cursor = cut

    if cursor < end:
        spans.append((cursor, end))
    return spans


def _pack_contiguous_spans(
    spans: list[tuple[int, int]],
    *,
    max_chars: int,
    target_chars: int,
) -> list[tuple[int, int]]:
    """Greedily pack adjacent exact spans without crossing ``max_chars``."""
    if not spans:
        return []

    packed: list[tuple[int, int]] = []
    current_start, current_end = spans[0]

    for next_start, next_end in spans[1:]:
        if next_start != current_end:
            raise ValueError(
                "Cannot pack non-contiguous decomposition source spans"
            )

        combined_length = next_end - current_start
        current_length = current_end - current_start

        if combined_length <= max_chars and current_length < target_chars:
            current_end = next_end
        else:
            packed.append((current_start, current_end))
            current_start, current_end = next_start, next_end

    packed.append((current_start, current_end))
    return packed


def _merge_small_adjacent_chunks(
    text: str,
    chunks: list[DecompositionSourceChunk],
    *,
    max_chars: int,
    target_chars: int,
) -> list[DecompositionSourceChunk]:
    """Merge tiny neighboring structural chunks when safely bounded."""
    if len(chunks) <= 1:
        return chunks

    merged: list[DecompositionSourceChunk] = []
    current = chunks[0]

    for next_chunk in chunks[1:]:
        if current.end != next_chunk.start:
            raise ValueError("Source chunks must be contiguous before merging")

        combined_length = next_chunk.end - current.start
        current_length = current.end - current.start
        next_length = next_chunk.end - next_chunk.start

        should_merge = (
            combined_length <= max_chars
            and (
                current_length < target_chars // 2
                or next_length < target_chars // 2
            )
        )

        if should_merge:
            current = DecompositionSourceChunk(
                text=text[current.start:next_chunk.end],
                start=current.start,
                end=next_chunk.end,
            )
        else:
            merged.append(current)
            current = next_chunk

    merged.append(current)
    return merged


def _validate_source_chunk_partition(
    text: str,
    chunks: list[DecompositionSourceChunk],
    *,
    max_chars: int,
) -> None:
    """Assert that chunking is an exact, gap-free partition of the source."""
    if not chunks:
        raise ValueError("Non-empty source produced no decomposition chunks")

    cursor = 0
    reconstructed: list[str] = []

    for index, chunk in enumerate(chunks):
        if chunk.start != cursor:
            raise ValueError(
                f"Source chunk gap/overlap before chunk[{index}]: "
                f"expected start={cursor}, actual={chunk.start}"
            )
        if chunk.end <= chunk.start:
            raise ValueError(f"Source chunk[{index}] must have positive length")
        if chunk.text != text[chunk.start:chunk.end]:
            raise ValueError(
                f"Source chunk[{index}] text does not match its source span"
            )
        if chunk.end - chunk.start > max_chars:
            raise ValueError(
                f"Source chunk[{index}] exceeds max_chars={max_chars}"
            )

        reconstructed.append(chunk.text)
        cursor = chunk.end

    if cursor != len(text):
        raise ValueError(
            "Source chunk partition does not reach the end of the source"
        )
    if "".join(reconstructed) != text:
        raise ValueError(
            "Source chunk partition does not reconstruct the source exactly"
        )


_CONTEXT_BLOCK_MAX_CHARS = 700
_CONTEXTUAL_CHUNK_MAX_CHARS = 2500
_CONTEXTUALIZED_TEXT_MAX_CHARS = 3000
_MAX_SEMANTIC_SEGMENTATION_DEPTH = 8
_MAX_SEMANTIC_SEGMENTATION_RETRIES = 1
_MAX_CONTEXTUALIZATION_RETRIES = 1

_SEMANTIC_SEGMENTATION_SYSTEM_PROMPT = """
You perform COARSE SEMANTIC SEGMENTATION before semantic decomposition.

You receive:
- the complete source as ordered deterministic SOURCE BLOCKS;
- an ACTIVE_BLOCK_RANGE that must be partitioned in this call.

The full source is visible only so you can understand references, inherited scope,
and relationships that affect where it is semantically safe to split the ACTIVE
range. You are NOT performing semantic decomposition and you are NOT rewriting
the text.

Your task is to partition exactly the ACTIVE range into meaningful contiguous
semantic chunks.

IMPORTANT RESPONSIBILITY BOUNDARY
---------------------------------
- You decide semantic boundaries.
- Python validates exact coverage/order and recursively asks you to segment any
  resulting chunk that is still larger than the configured leaf threshold.
- Python will NOT arbitrarily split an oversized semantic chunk.
- Therefore, preserve meaningful context when choosing boundaries.
- If a large semantic unit must be divided, choose a boundary where each side
  can later be made self-contained by a light contextual rewrite.
- Do not force unrelated material together merely to approach a target size.

OUTPUT CONTRACT
---------------
Each returned chunk is defined only by:
- start_block_index
- end_block_index

The returned chunks must:
- cover every block in ACTIVE_BLOCK_RANGE exactly once;
- be ordered;
- be contiguous;
- be non-overlapping and gap-free;
- stay entirely inside ACTIVE_BLOCK_RANGE.

If ACTIVE_BLOCK_RANGE is larger than the configured leaf threshold, return at
least TWO strictly smaller chunks. Do not return the entire active range unchanged.

You do NOT need to make every returned child smaller than the leaf threshold in
one call. If a child is still too large, Python will recursively ask you to
segment that child semantically.

Do not output contextualized text, propositions, graph nodes, graph relations, or
summaries.

Return only the structured SemanticSegmentationDecision.
""".strip()

_FINAL_CONTEXTUALIZATION_SYSTEM_PROMPT = """
You perform a LIGHT, SOURCE-GROUNDED contextualization pass over finalized
semantic chunks.

You receive:
- the complete source as exact SOURCE BLOCKS;
- FINAL_LEAF_CHUNKS chosen by recursive semantic segmentation.

Every finalized leaf chunk is already small enough for the later decomposition
model. Your job is to make each leaf locally understandable WITHOUT forwarding
the whole source into decomposition.

For every FINAL_LEAF_CHUNK:
1. identify only the external source blocks genuinely needed to resolve references
   or inherited scope;
2. produce a minimally rewritten contextualized_text that is self-contained.

The later decomposition model receives ONLY contextualized_text for that leaf.
It does NOT receive the whole source or the external context blocks.

AUTHORITATIVE VS DERIVED TEXT
-----------------------------
SOURCE BLOCKS are authoritative.
contextualized_text is derived interpretation only.

Use external context only for things such as:
- pronouns and anaphora;
- phrases such as "this rule", "the above", "that decision", or "these steps";
- omitted subjects/objects that are unambiguous from source context;
- inherited scope from headings or nearby text;
- cross-chunk references that would otherwise be unclear.

Prefer replacing a reference with its referent rather than copying large amounts
of supporting text.

BAD:
    Copy several previous paragraphs into the contextualized chunk.

GOOD:
    Replace "this exception" with the specific exception/rule it refers to.

LOSSLESS LIGHT-TOUCH REQUIREMENT
--------------------------------
Preserve:
- polarity and modality;
- conjunction vs disjunction;
- conditions and exceptions;
- quantifiers, thresholds, counts, and ordering;
- temporal scope and attribution;
- restrictive words such as only, unless, before, after, first, correctly,
  exactly, any, all, none, and equivalent phrasing.

Do not add facts, rules, permissions, prohibitions, implications, causal claims,
or prerequisites absent from the source.

If a reference is genuinely ambiguous, preserve the ambiguity rather than invent
one interpretation.

CONTEXT BLOCKS
--------------
context_block_indices may reference source blocks outside the primary leaf when
they are necessary to interpret it.

- Do not list blocks already inside the primary leaf.
- Use the smallest useful context set.
- External context is not copied wholesale into contextualized_text.
- Context block identities are preserved as metadata for later graph/linking use.

OUTPUT CONTRACT
---------------
Return exactly one FinalChunkContextPlan for every FINAL_LEAF_CHUNK.
chunk_index must match the supplied final leaf index.
Do not omit, duplicate, or reorder chunk indices.

Keep contextualized_text concise. Its purpose is reference/scope resolution, not
summarization of the whole source.

Return only the structured FinalChunkContextDecision.
""".strip()

_CONTEXTUAL_CHUNKING_AUDIT_SYSTEM_PROMPT = """
You audit finalized source-backed contextualized leaf chunks for semantic
faithfulness.

You receive:
- the complete ordered SOURCE BLOCKS;
- the finalized primary leaf chunks;
- exact external context blocks selected for each leaf;
- each derived contextualized_text.

Mark complete=true only if all of the following hold:

1. Every contextualized_text faithfully represents its own primary source leaf.
2. External context is used only to resolve references, inherited scope, ellipsis,
   or otherwise necessary interpretation.
3. No contextualized leaf invents facts, rules, conditions, permissions,
   prohibitions, exceptions, causal claims, prerequisites, or implications.
4. Important modality, polarity, conjunction/disjunction, ordering, thresholds,
   quantifiers, attribution, uncertainty, and restrictive qualifiers are preserved.
5. References such as "this", "that", "it", "they", "the above", or "these rules"
   are resolved only when source context supports the resolution.
6. A leaf that would otherwise be misleading or materially ambiguous is made
   self-contained through a faithful light rewrite.
7. Contextualization does not reproduce unrelated independent semantics from
   context blocks.
8. The contextualized text is concise enough to serve as a local decomposition
   input rather than a restatement of the full source.

The exact source leaves and context blocks are authoritative.
The contextualized text is only a derived aid for later decomposition.

Return:
- complete: true/false
- issues: a list of localized issue objects. Every issue object MUST contain:
  - chunk_index: the exact zero-based chunk_index from FINAL_CONTEXTUALIZED_LEAVES
  - issue: a concise description of the semantic distortion, unresolved dependency,
    wrong referent, lost scope, unsupported addition, or excessive context copying
- reason: concise overall assessment

LOCALIZATION REQUIREMENT
------------------------
When complete=false, identify every defective leaf by its exact supplied
chunk_index. Do not report an unindexed/global issue when the defect belongs to a
leaf. Do not include good leaves in issues. When complete=true, issues must be
empty.
""".strip()


_LOGIC_NORMALIZATION_SYSTEM_PROMPT = """
Normalize source-explicit RELATIONAL and PROPOSITIONAL structure in exactly ONE
source statement.

This is a narrow language-normalization task. Do not build persistent graph edges,
do not create semantic nodes, and do not decide whether an AST is required. Python
performs deterministic routing after your response.

You receive:
- SOURCE: the exact statement being analyzed;
- AVAILABLE_PROPOSITIONS: semantic atomic descendants already produced for this
  statement.

Return LogicNormalizationDecision with two independent kinds of structure:
1. ``relations`` for source-explicit BINARY semantic relations whose two endpoints
   are exact AVAILABLE_PROPOSITIONS;
2. ``slots`` / ``expressions`` / ``clauses`` only for Boolean/cardinality structure
   that must first be normalized canonically.

If SOURCE has no source-explicit binary relation or Boolean/cardinality structure,
return empty relations, slots, expressions, and clauses. Ordinary standalone facts,
requirements, prohibitions, and procedures do not need an entry merely because they
are propositions.

BINARY SEMANTIC RELATIONS
-------------------------
Use ``relations`` when SOURCE explicitly relates two semantic propositions with one
binary relation that can live directly in the graph. Important examples include:
- PRECEDES for before/after/first/then ordering;
- REQUIRES for an explicit prerequisite/dependency relation;
- ENABLES for an explicitly stated enabling/establishment relation;
- CAUSES for explicit causal language;
- QUALIFIES for an explicit qualification/exception relation;
- SUPPORTS or SUPERSEDES when explicitly stated.

Examples:
- "Before A, do B" -> B PRECEDES A.
- "After A, do B" -> A PRECEDES B.
- "B requires A" -> B REQUIRES A.
- "A enables B" -> A ENABLES B.

Use proposition indices only for exact endpoint identity. Do not point a relation at
a broader rule node merely because it contains one endpoint. Do not invent a binary
relation when the source is ambiguous. ``DECOMPOSES_INTO`` is forbidden.

Do NOT directly emit IMPLIES for Boolean condition/effect language. Normalize such
language through clauses below so Python can determine whether it reduces to one or
more binary IMPLIES edges or requires compound logic.

LANGUAGE NORMALIZATION
----------------------
Interpret equivalent natural-language connectives, not only literal keywords.
Conjunction may be expressed by "and", "both", "together with", "along with",
"as well as", or "in addition to". Disjunction may be expressed by "or",
"either", "one of", "alternatively", or equivalent phrasing. Conditions may use
"if", "when", "provided that", "assuming", "only if", "unless", or equivalent
constructions. Temporal order may use "before", "after", "first", "then",
"prior to", "following", or equivalent phrasing. Normalize meaning, not surface
words.

SLOTS AND BINDING
-----------------
Create a slot only for a proposition that participates in Boolean/cardinality
structure. Bind proposition_index only when one AVAILABLE_PROPOSITION independently
expresses the same complete proposition. A broader conditional/rule node is not
identical to one of its internal operands. Leave proposition_index=null when there
is no exact semantic proposition. Unresolved slots are valid.

Use proposition_value=false only when the available proposition explicitly asserts
the negation of the slot. Never bind by inference, arithmetic, date reasoning,
world knowledge, or implication.

TEMPORARY CANONICAL EXPRESSIONS
-------------------------------
Use direct signed slot operands for literals. Use expression nodes only to expose
the source's actual grouping:
- AND
- OR
- NOT for scoped negation that cannot be represented as a signed literal
- AT_LEAST / AT_MOST / EXACTLY for Boolean cardinality only

Do NOT optimize or distribute expressions yourself. Python owns that decision.
Examples:
- "If A then B" -> rule with condition=A, effect=B.
- "A only if B" -> rule with condition=A, effect=B.
- "A if B" -> rule with condition=B, effect=A.
- "If A then B and C" -> rule with effect=AND(B,C).
- "If A or B then C" -> rule with condition=OR(A,B).
- "If A and B then C" -> rule with condition=AND(A,B).
- "If A then B or C" -> rule with effect=OR(B,C).

Python will later convert reducible literal-level rules into graph IMPLIES edges,
for example:
- A -> (B AND C) becomes A->B and A->C;
- (A OR B) -> C becomes A->C and B->C;
while irreducible forms such as (A AND B)->C or A->(B OR C) remain in the logic
layer.

CLAUSES
-------
Use kind=rule for explicit Boolean condition -> effect structure. Use kind=assertion
only when truth-functional grouping itself must be preserved, such as an asserted
OR, cardinality constraint, or scoped compound negation. Do not create an assertion
for ordinary standalone A, NOT A, or A AND B when the semantic graph already carries
those independent assertions.

A statement may contain more than one explicit relation or logical clause.
evidence_text must be a source-supported excerpt.

Return only LogicNormalizationDecision.
""".strip()


_LOGICAL_STRUCTURE_AUDIT_SYSTEM_PROMPT = """
Audit one proposed COMPOUND propositional-logic remainder against SOURCE and
AVAILABLE_PROPOSITIONS.

This is a compound-logic audit, not semantic coverage and not a checklist of
available propositions. SOURCE-explicit simple binary relations are supplied
separately as NORMALIZED_SIMPLE_RELATIONS and are owned by the semantic relation
graph, not by the logic layer.

Representation rule
-------------------
Simple A -> B and every other Boolean rule that Python can distribute losslessly
to binary IMPLIES edges belongs in NORMALIZED_SIMPLE_RELATIONS and should NOT be
duplicated in PROPOSED_COMPOUND_LOGIC. AST expressions are appropriate only for
genuine irreducible AND/OR/cardinality/nested Boolean structure.

Unresolved slots are valid
--------------------------
An unresolved slot is a first-class Boolean variable. It may appear anywhere a
resolved slot may appear: as a rule condition/effect or inside a compound
expression. ``proposition_index=null`` means only that no existing semantic node
is bound yet. It does NOT invalidate the surrounding logical structure.

AVAILABLE_PROPOSITIONS boundary
-------------------------------
Bind proposition_index only for exact proposition identity. Containment, overlap,
or participation in a larger rule is not identity.

Examples:
- available: "If C, perform A"; slot: "perform A" => DO NOT bind.
- available: "When time is needed, use get_current_time()"; slot: "use
  get_current_time()" => DO NOT bind unless a separate proposition independently
  states the action.
- available: "Y requires P"; slots "Y" and "P" => neither is automatically bound
  to the broader requirement node.
- available: "At least 30 days have passed"; matching slot => bind.

Completeness criteria
---------------------
Mark complete=true when:
- every irreducible SOURCE-explicit Boolean/cardinality structure is represented
  with correct scope, direction, and polarity;
- any reducible Boolean structure is already represented by
  NORMALIZED_SIMPLE_RELATIONS rather than duplicated as an AST;
- compound ASTs are used only where compound structure is actually required;
- every proposition_index binding is an exact semantic-identity binding;
- slots lacking an exact available proposition remain unresolved;
- rule-only terms are not incorrectly asserted true;
- truth-functional assertions whose grouping matters (for example OR/cardinality)
  are preserved; ordinary semantic facts are not duplicated as logic assertions;
- no arithmetic/date/world/deductive reasoning was used to fill missing slots.

IMPORTANT:
- A partial representation is NOT incomplete because slots are unresolved.
- Do not require graph-owned PRECEDES/REQUIRES/ENABLES/CAUSES/QUALIFIES relations
  to appear in the logic layer.
- Do not require a reducible IMPLIES relation to appear in the logic layer when it
  is present in NORMALIZED_SIMPLE_RELATIONS.
- Do not require every AVAILABLE_PROPOSITION to appear in logic.
- Ordinary non-logical statements do not need logic entries.
- Never set complete=false for an item you call optional, preferable, cleaner, or
  merely an alternative representation.

Return:
- complete
- missing_logic: only required source-explicit logical structure that is absent or
  materially misrepresented
- unsupported_logic: invented/mis-scoped structure, invalid exact bindings, or
  unnecessary AST structure that changes meaning
- reason: concise assessment
""".strip()

# The previous 120B logic-slot matcher prompt and implementation are preserved
# in logic_slot_binding_llm_legacy.py. The active backend is local DeBERTa.

_LOGIC_CUE_RE = re.compile(
    # Boolean implication / conditional / prerequisite language.
    r"\b(?:if|when|whenever|unless|provided|assuming|otherwise|else)\b"
    r"|\bonly\s+if\b|\bprovided\s+that\b|\bin\s+case\b"
    r"|\b(?:as\s+long\s+as|so\s+long\s+as|on\s+condition\s+that)\b"
    r"|\b(?:in\s+the\s+event\s+that|contingent\s+upon|contingent\s+on)\b"
    r"|\b(?:subject\s+to|requires?|requirement|depends?\s+on|conditional\s+on)\b"
    # Source-explicit temporal/procedural relations that should be normalized
    # onto graph edges rather than retained as connective fragments in nodes.
    r"|\b(?:before|after|first|then|previously|subsequently)\b"
    r"|\b(?:prior\s+to|followed\s+by|following)\b"
    r"|\b(?:enables?|enabled\s+by|causes?|caused\s+by)\b"
    r"|\b(?:leads?\s+to|results?\s+in|supersedes?|replaces?)\b"
    r"|\b(?:qualifies?|qualified\s+by|except(?:\s+when|\s+if)?)\b"
    # Disjunction is not reducible to independent asserted facts, so it deserves
    # inspection even without an implication cue.
    r"|\b(?:or|either|alternatively)\b"
    r"|\bfailing\s+that\b"
    r"|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+of\b"
    # Boolean cardinality. Numeric predicates such as 'at least 30 days' are
    # intentionally excluded unless they use the '<N> of <terms>' form.
    r"|\b(?:at\s+least|at\s+most|exactly)\s+"
    r"(?:one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+of\b",
    flags=re.IGNORECASE,
)

_MAX_LOGICAL_STRUCTURE_RETRIES = 1


_RELATION_SYSTEM_PROMPT = """
You identify direct semantic relations between atomic propositions in a memory graph.

You receive:
- exactly one ANCHOR proposition;
- a small list of CANDIDATE propositions that were preselected by another layer.

You are NOT searching the graph.
You are NOT creating graph nodes or edges.
You are NOT responsible for candidate retrieval.
Your only task is to decide which supplied candidates have a meaningful direct
relation to the anchor, what relation exists, and its direction.

Allowed relation types
----------------------
related_to:
    The propositions are directly semantically related, but none of the more
    specific relation types below applies.

equivalent_to:
    The propositions express materially the same fact, rule, state, or action.

corefers_with:
    The propositions contain expressions that refer to the same underlying
    referent.

same_entity:
    The propositions concern the same concrete entity.

same_event:
    The propositions concern the same event or action occurrence.

supports:
    One proposition provides evidence or justification for the other.

contradicts:
    The propositions cannot both hold under the same applicable conditions,
    attribution, and temporal scope.

qualifies:
    One proposition narrows, conditions, limits, or adds an exception/qualification
    to the other.

serves_goal:
    One proposition describes an action/state that serves the goal expressed by
    the other.

requires:
    One proposition depends on the other being satisfied or true.

enables:
    One proposition makes the other possible or helps establish a prerequisite
    for it.

precedes:
    One proposition must occur before the other in an explicitly stated
    temporal or procedural order.

causes:
    One proposition causally produces or leads to the other.

supersedes:
    One proposition replaces an older state, value, instruction, or fact
    represented by the other.

Direction
---------
For directional relations, return:
- anchor_to_candidate when the anchor bears the relation to the candidate;
- candidate_to_anchor when the candidate bears the relation to the anchor.

For symmetric relations, return symmetric.

Treat these relations as symmetric:
- related_to
- equivalent_to
- corefers_with
- same_entity
- same_event
- contradicts

All other allowed relations are directional.

Important rules
---------------
- ``anchor_context_paths`` and candidate ``context_paths`` are semantic ancestry,
  not extra facts. Use them to disambiguate inherited scope and referents.
- Identical leaf wording under different parents does NOT imply equivalent_to,
  corefers_with, or any other identity relation. Compare complete contextual
  meanings. If "this rule" points to different rules, keep the occurrences
  distinct.
- Different wording may still be equivalent when the contextual paths establish
  the same complete proposition.
- Omit candidates that have no meaningful direct relation to the anchor.
- Do not create a relation merely because two propositions share a broad topic.
- Prefer the most specific supported relation over related_to.
- Do not invent facts or relations not supported by the proposition meanings.
- Account for polarity, modality, conditions, temporal scope, attribution, and
  qualifiers.
- Two rules are NOT contradictory merely because one states a prerequisite and
  another describes the procedure used to establish that prerequisite.
- Distinguish a prerequisite from the procedure that establishes/checks it. If
  proposition A describes the permitted method for establishing/checking state P,
  and proposition B expresses or depends on P, prefer an ENABLES relation from A
  toward P/B when supported; do not reverse it into "A requires P" unless the
  proposition meanings explicitly state that prerequisite.
- Generic forms such as "to establish P, perform A" or "verify P by checking A"
  normally encode procedural direction from A toward P, not a requirement that P
  already hold before A can occur.
- Keep ordering distinct from prerequisites. "A happens before B" should use
  PRECEDES when the order is explicit; do not encode mere ordering as REQUIRES.
- Generic ordering forms:
  - "First A, then B" -> A PRECEDES B.
  - "Before B, perform A" -> A PRECEDES B.
  - "After A, perform B" -> A PRECEDES B.
- PRECEDES is directional. The source is the earlier action/state and the target
  is the later action/state.
- IMPLIES is reserved for the source-explicit normalization pass and must not be
  inferred by this broader relation-linking model.
- The hierarchical relation decomposes_into is forbidden here; it is owned by
  deterministic hierarchy construction.
- other_node_id must be the ID of one of the supplied candidates.
""".strip()


_DECOMPOSITION_SYSTEM_PROMPT = """
You decompose exactly ONE statement at a time.

A deterministic dependency-parser scaffold may be supplied with the request. It
is ADVISORY structural evidence, not authoritative semantics. Use its predicate
frames, participants, modality/negation, and connective cues to avoid dropping
source meaning. If the parser is wrong, preserve the source meaning rather than
forcing the scaffold.

You are NOT constructing a graph.
You are NOT responsible for canonical node IDs, parent IDs, hierarchy edges, graph
depth, graph mutation, candidate retrieval, global lateral-relation discovery, or
formal logical parsing.

A dedicated SECOND PASS runs after semantic decomposition and receives the original
source plus all semantic propositions produced by this pass. That second pass owns
source-explicit relations/connectives between operands, including temporal order,
prerequisites, simple condition -> effect structure, Boolean/cardinality grouping,
and IF/WHEN/UNLESS/ONLY-IF/OTHERWISE semantics. Python routes reducible binary
structure to graph relations and keeps only irreducible Boolean structure in the
logic layer. A later relation linker owns broader cross-node relation discovery.

For a composite statement, you MAY return sparse local relation hints when a
relation is completely unambiguous and explicitly stated by the immediate parent.
These hints are optional conveniences. They are NOT part of the semantic-completeness
contract of this pass because the second normalization pass receives the unchanged
SOURCE and can recover source-explicit relations there.

Your goal is LOSSLESS SEMANTIC PROPOSITION DECOMPOSITION, not summarization and not
logical reconstruction. The direct children of a composite statement must
collectively preserve every operationally meaningful OPERAND and every qualifier
that belongs inside an operand. Connective words whose only job is to relate two
separate operands should not be turned into standalone semantic children.

Your only task is to decide whether the supplied statement is:

1. atomic
   - It expresses one independently meaningful fact, rule, requirement,
     prohibition, permission, condition, procedure, prescribed method,
     user claim, observation, or intended action.
   - A statement is NOT atomic merely because it can be summarized in one sentence.
     If it contains multiple independently applicable clauses, conditions,
     procedures, exceptions, or ordered requirements, it is composite.
   - Return kind="atomic".
   - Return children=[].
   - An atomic statement MUST be a standalone, semantically meaningful proposition.
     It must not be a bare auxiliary/modal/connective fragment whose meaning depends
     on a missing lexical predicate or complement. Never emit/accept fragments such
     as "have to", "has to", "must", "should", "can", "need to", "only if",
     or a bare infinitival marker as an atomic proposition. Keep modality and
     auxiliaries attached to the lexical predicate and its required arguments.
   - Do not generate subjects/predicates/objects here. A dedicated post-tree
     extractor fills retrieval payloads only after final atomic leaves are known.

2. composite
   - Its meaning can be separated into more specific direct semantic components.
   - Return kind="composite".
   - Return the DIRECT child statements only.
   - Do not recursively decompose the children yourself.
   - Do not generate subjects/predicates/objects for the composite statement.
   - Do not return proposition/S/P/O fields for children. Retrieval payloads are
     generated only after the complete semantic hierarchy is built.

A composite child must be strictly narrower than its parent.
Never return the complete parent statement unchanged as one of its children.
Do not create vague heading-like children when the parent contains concrete rules.

OCCURRENCE IDENTITY AND REPEATED WORDING
----------------------------------------
Do not emit the same semantic child twice for the same source occurrence. However,
identical or near-identical wording can legitimately appear more than once when it
belongs to different source occurrences, referents, scopes, or parent rules. Those
occurrences must remain distinct.

When the immediate parent makes an anaphoric referent unambiguous, make the child
content self-contained enough to preserve that context. For example, prefer
"The tool-V requirement may be overridden when the user is a kid" over an isolated
"This statement may be overridden when the user is a kid" when "this statement"
clearly refers to the tool-V requirement. Preserve genuine ambiguity rather than
inventing a referent.

Never deduplicate children merely because their surface strings match. Source
occurrence and inherited parent meaning are part of proposition identity.

CHILD OUTPUT CONTRACT
---------------------
Every composite child MUST contain:
- content: the lossless semantic child statement;
- source_text: the best supporting excerpt from the immediate parent statement;
- semantic_role: the child's primary role from the supplied schema.

Prefer copying ``source_text`` verbatim as one contiguous excerpt from the parent.
Do not manufacture evidence. Minor formatting normalization, bullet-marker changes,
or punctuation differences in ``source_text`` are tolerated by the runtime when
the child meaning is still directly supported by the parent. The child ``content``
may paraphrase for clarity only when the meaning is fully preserved.

LOSSLESSNESS REQUIREMENT
------------------------
For a composite statement, every independently operative semantic clause in the
parent MUST be represented by at least one direct child. Do not select only the
"main" ideas. Do not summarize several clauses into a broader child if that loses
how, when, under what condition, by what procedure, in what order, or with what
qualification a rule applies.

Preserve in CHILD CONTENT whenever present:
- facts and assertions;
- requirements and prohibitions;
- permissions and optional actions;
- prerequisites and postconditions;
- conditions and conditional branches;
- procedures, prescribed methods, and means of accomplishing/checking something;
- statements that explain how a prerequisite, state, condition, or result is
  established or verified;
- exceptions, overrides, and fallback rules;
- temporal scope that belongs inside one proposition, such as "before 5 PM" or
  "until the account closes";
- thresholds, counts, quantifiers, and selection rules;
- polarity, modality, attribution, uncertainty, and scope;
- restrictive or satisfaction-changing qualifiers.

The structured PropositionPayload does NOT need to duplicate these nuances. It is
only a coarse subject/predicate/object lexical index; exact semantic fidelity is
owned by child content and the later relation/logic passes.

SEMANTIC OPERANDS MUST SURVIVE; FORMAL CONNECTORS MAY BE DEFERRED
----------------------------------------------------------------
Preserve every independently meaningful operand and every qualifier that belongs
inside an operand. The later normalization pass receives the exact original SOURCE,
so relational/Boolean connectors BETWEEN returned children do not need to be
redundantly copied into child content.

Examples:
- "If A, then B" may decompose to children A and B. The later logic pass owns A -> B.
- "Either A or B" may decompose to children A and B. The later logic pass owns OR.
- "Before A, do B" should decompose to clean children A and B. The later
  normalization pass owns B PRECEDES A; do not create a child "Before A".
- "B requires A" should decompose to clean children B and A. The later
  normalization pass owns B REQUIRES A; do not keep "requires A" attached to B
  merely to preserve the relation word.
- "At least 30 days have passed" is one semantic proposition: "at least 30 days"
  is internal to that proposition and must NOT be weakened to "30 days".
- "Only authorized users may act" must preserve "only authorized users" because
  that restricts the operand itself.

RELATIONAL CONNECTIVES VS OPERAND-INTERNAL QUALIFIERS
-----------------------------------------------------
When before/after/first/then/if/when/unless/requires/depends-on or equivalent
language CONNECTS two independently meaningful operands, return the clean operands
and leave the relation/connective to the later normalization pass.

Do NOT create connective-only children such as "Before", "After", "If", "Then",
"Unless", or "Requires". Do NOT recursively split a clean operand just to preserve
a connective that belongs between siblings.

Preserve temporal/restrictive wording when it is INTERNAL to one proposition and
cannot be represented as a relation between sibling operands. Examples include
"before 5 PM", "for at least 30 days", "already verified", and "only authorized
users".

PROCEDURES AND PREREQUISITES
----------------------------
A prerequisite and the procedure used to establish/check that prerequisite are
different semantic statements and must be preserved separately when both are
present.

Examples of generic forms that contain procedural meaning:
- "To establish P, perform A."
- "To verify P, check A."
- "P is determined by comparing A with B."
- "Before B, establish P by performing A."

Do NOT collapse these into only "B requires P" or only "P is required".
Preserve the procedure/method A as its own child when it is independently useful.
Do NOT collapse an establishment/checking procedure into a generic condition.
Preserve the procedure as semantic content when it is independently useful.

SOURCE GROUNDING
----------------
Every child must be directly supported by a specific phrase, sentence, or clause
in the supplied parent statement. A child may paraphrase for clarity, but it must
not add a rule, prerequisite, exception, or implication that is absent from the
source. Return that support explicitly in the child's ``source_text`` field. Prefer
a verbatim contiguous excerpt from the parent whenever possible. Before returning,
map every child back to supporting source wording and confirm that no operative
source clause is left unmapped. Exact punctuation or formatting identity is less
important than faithful semantic support.

For policy/rule text:
- preserve requirements, prohibitions, permissions, conditions, and procedures;
- preserve modality, operand-internal temporal scope, exceptions, and satisfaction
  criteria;
- preserve how prerequisites are established, not only that they are required;
- preserve what must be true for a condition to count as satisfied;
- preserve explicit prerequisite wording in semantic content; the later
  normalization pass owns condition/prerequisite relations between propositions.

For user messages:
- preserve requests, preferences, values supplied by the user, and claims;
- preserve uncertainty and qualifications in the user's wording;
- do not promote user claims into externally verified facts.

For tool results:
- preserve observations and reported outcomes;
- distinguish returned observations from conclusions that would require further
  reasoning.

For executable code or tool-use text:
- decompose the semantic action, not irrelevant Python syntax;
- preserve the tool/action name, known arguments, intended effect, and explicit
  preconditions;
- preserve dependencies between actions when one action supplies information
  needed to determine another.

LOCAL RELATION HINTS ARE OPTIONAL
---------------------------------
For composite statements, ``local_relations`` are OPTIONAL hints only. Return one
only when the immediate parent explicitly and unambiguously states that semantic
relation between two returned children.

Use zero-based child indices. ``source_child_index`` is the relation source and
``target_child_index`` is the relation target. ``evidence_text`` should preferably
be a verbatim contiguous excerpt from the parent that supports the relation.

Important boundaries:
- Do NOT use local_relations as a substitute for clean semantic operands. The
  dedicated normalization pass owns Boolean/simple-conditional structure and can
  also recover source-explicit temporal/prerequisite relations.
- Do NOT add PRECEDES solely because a word such as "first" appears. Emit PRECEDES
  only when the immediate source makes both ordered endpoints unambiguous.
- Do NOT add REQUIRES/QUALIFIES merely to encode a conditional whose wording is
  already preserved for the logic pass.
- Do NOT reverse an establishment procedure into a prerequisite.
- If uncertain whether the relation is explicit, omit it. The later relation
  linker can infer semantic relations after node construction.
- Set origin="source_explicit" for any hint you do emit.

The absence of local_relations MUST NOT cause you to add, remove, merge, or rewrite
semantic children. Proposition decomposition comes first.

FINAL SELF-CHECK BEFORE RETURNING
---------------------------------
If kind="composite", ask yourself:
1. Did every independently operative semantic operand/clause survive as a child?
   Relational/Boolean connectors between those operands may be deferred to the
   normalization pass.
2. Did every procedure or "how to establish/check X" clause survive?
3. Did I keep relation words BETWEEN operands out of standalone children while
   preserving temporal/restrictive wording that belongs INSIDE an operand?
4. Did every qualifier or threshold internal to a proposition survive?
5. Did every exception/override operand survive, even if its formal connection is
   deferred to the logic pass?
6. Is every child directly supported by source_text from the parent?
7. Did I avoid inventing child-to-child logical or temporal relations merely to
   make the decomposition look formally complete?
8. For every local relation I did return, is it explicit and unambiguous in the
   immediate parent text?

If a source meaning is missing, revise the children. Do NOT invent a local relation
as a substitute for missing semantic content.

routing_text must be a short retrieval-oriented description of the current
statement. It is not a substitute for content and must not contain important
semantics that are absent from content.
""".strip()


_CHUNK_SEMANTIC_AUDIT_SYSTEM_PROMPT = """
You audit ONE fully constructed semantic-decomposition chunk after recursive
construction is complete.

The decomposition itself was produced from deterministic dependency-parser
scaffolds plus an OSS-120B semantic decomposer. You are NOT repairing the chunk.
You return only PASS/FAIL diagnostics. If the chunk is incomplete, the graph build
will stop rather than enter a repair loop.

You receive:
- PRIMARY_SOURCE: the exact authoritative source slice for this chunk;
- CONTEXT_SOURCE_TEXTS: exact external source excerpts used only when needed to
  interpret the primary slice;
- CONTEXTUALIZED_INPUT: the derived self-contained text actually decomposed;
- CHUNK_STRUCTURE: every node, hierarchy edge, source-explicit relation, and local
  logic object produced while building this chunk, before cross-chunk slot binding.

Judge semantic fidelity of the completed subtree as a whole, not each recursive
LLM call in isolation.

Check for:
- missing_semantics: an operative source proposition/condition/procedure/exception
  is absent from the completed subtree;
- distorted_semantics: a represented proposition changes actor, modality,
  polarity, scope, qualifier, threshold, temporal restriction, or other
  satisfaction-changing meaning;
- unsupported_semantics: a node asserts meaning not supported by the authoritative
  primary/context sources;
- missing_relation: an explicit source relation/connective that should have been
  represented by the source-normalization layer is absent;
- incorrect_relation: a stored source relation has wrong endpoints or direction;
- logic_error: Boolean/conditional/cardinality structure materially changes the
  source meaning;
- atomicity: a final atomic leaf still contains multiple independently operative
  semantic propositions, or a composite node has no meaningful decomposition.

Do NOT fail merely because parser guidance was imperfect, because routing_text is
brief, because S/P/O payloads are not present yet, or because broad inferred
lateral relations have not been linked. S/P/O extraction and broad relation linking
happen later. Cross-chunk slot identity binding also happens later.

Return complete=true with issues=[] only when the completed chunk preserves the
source semantics safely. Otherwise return complete=false and precise issue objects.
""".strip()

_ATOMIC_CLASSIFICATION_AUDIT_SYSTEM_PROMPT = """
Audit only whether one proposed ATOMIC semantic statement is truly atomic.

The authoritative graph node content remains the exact SOURCE text. Decide only:

classification_valid
  Is SOURCE one semantically indivisible operand in its inherited SEMANTIC_ROLE,
  so kind=atomic is appropriate? A role-bound condition, exception,
  qualification, procedure, or intended action does not need to be a complete
  standalone sentence if it expresses one coherent operand. Return false when
  SOURCE contains multiple independently applicable clauses/procedures/branches
  that need separate semantic children.

Connective-only fragments such as "Before", "After", "If", "Then", "Unless",
or "Requires" are never valid atomic operands. However do not split a coherent
role-bound operand merely because its grammar depends on the parent context.

Do NOT evaluate or comment on PropositionPayload here. Retrieval payloads are
generated only after the complete semantic hierarchy has been built and only for
nodes that remain atomic leaves.

Do not require formal Boolean AST/implication structure here. The separate
logic-normalization pass owns formal structure.
""".strip()


_DECOMPOSITION_AUDIT_SYSTEM_PROMPT = """
You audit exactly one proposed COMPOSITE semantic decomposition for LOSSLESS
CHILD COVERAGE. You are also the ONLY semantic repair step.

You receive:
- the exact original SOURCE statement;
- one proposed LocalDecompositionDecision.

Return exactly one DecompositionAuditResult with one of two actions:

1. action="pass"
   Use this only when the proposed decomposition is semantically lossless and
   contains no unsupported/duplicate child meaning or unsafe local relation.
   corrected_decision must be null.

2. action="corrected"
   If anything is missing, weakened, duplicated, unsupported, or incorrectly
   scoped, return the COMPLETE corrected LocalDecompositionDecision in
   corrected_decision. Do the correction yourself in this same response.

CORRECTION RULES
----------------
- Make the minimum semantic edits needed to make the decomposition lossless.
- You MAY replace an existing child when a qualifier, modality, condition,
  actor, restriction, or scope was lost. Prefer replacement over adding a
  near-duplicate child.
- You MAY delete unsupported or duplicate children.
- You MAY add a genuinely missing child.
- Preserve already-correct children unchanged whenever possible.
- Keep kind="composite".
- Return the whole corrected decision, not a patch, issue list, or prose repair
  instruction.
- Do not create PropositionPayload/S-P-O fields. Those are generated later.
- Child content must be meaningful semantic propositions, not connective-only
  fragments such as "Before", "After", "If", "Then", "Unless",
  "Requires", "And", or "Or".
- source_text should remain grounded in the immediate SOURCE; minor formatting
  normalization is acceptable.
- routing_text should remain a concise retrieval-oriented rendering of the same
  current statement.

SEMANTIC COVERAGE
-----------------
Preserve every independently operative source meaning, especially:
- requirements, prohibitions, and permissions;
- actors/subjects whose identity changes the proposition;
- prerequisites/conditions and postconditions as semantic operands;
- procedures or prescribed methods for establishing/checking a state;
- exception/override/fallback operands and their scope;
- modality, polarity, attribution, uncertainty, quantifiers, thresholds, counts,
  durations, and restrictive qualifiers such as "only", "below", "already";
- temporal/restrictive wording that belongs INSIDE one proposition, such as
  "before 5 PM" or "until closed".

FORMAL-STRUCTURE RESPONSIBILITY BOUNDARY
----------------------------------------
A later normalization pass receives the unchanged SOURCE and owns relations and
formal connectives between preserved operands. Therefore do NOT reject or modify
an otherwise lossless decomposition merely because child text omits a connector
whose only role is to connect separate operands, including:
- AND / OR / NOT grouping;
- AT_LEAST / AT_MOST / EXACTLY cardinality;
- IF / WHEN / UNLESS / ONLY-IF / OTHERWISE condition -> effect structure;
- prerequisite direction such as REQUIRES or QUALIFIES;
- temporal/order edges such as PRECEDES.

Examples:
- SOURCE: "If A, then B". Clean children A and B are sufficient; later logic
  normalization reconstructs A -> B.
- SOURCE: "First A, then B". Clean children A and B are sufficient; later
  normalization reconstructs PRECEDES.
- SOURCE: "Y requires P". Clean children Y and P are sufficient; later
  normalization reconstructs REQUIRES.

But connective-like wording that is internal to one proposition MUST remain.
For example "only authorized users", "at least 30 days", "before 5 PM",
or a condition that semantically scopes the child itself must not be weakened.

DUPLICATES AND SCOPE
--------------------
Do not solve a missing qualifier by adding a second near-duplicate child. Edit
the existing child so the complete proposition carries its proper scope.
If two children repeat the same semantic proposition from the same source
occurrence, return a corrected decision that removes/merges the redundancy.
Repeated wording from distinct source occurrences may remain distinct when its
referent or inherited scope differs.

LOCAL RELATIONS
---------------
local_relations are optional hints. Their absence is not a failure. If present,
keep only relations explicitly and unambiguously supported by the immediate
SOURCE. Remove any invented relation in the corrected decision.

Do not return missing_semantics, unsupported_children, or instructions for a
second repair model. Either PASS the proposed decomposition or RETURN THE FULL
CORRECTED DECOMPOSITION.
""".strip()

def _get_model(*, reasoning_effort: Literal["low", "medium", "high"] = "low"):
    model = LLMManager().get_model(settings.agent.code.model)
    model_name = str(
        getattr(model, "model_name", "")
        or getattr(model, "model", "")
        or ""
    ).lower()

    if "gpt-oss" in model_name:
        return model.bind(reasoning_effort=reasoning_effort)
    return model



_DECOMPOSITION_MODEL_NAME = os.environ.get(
    "CUGA_DECOMPOSITION_MODEL",
    "Azure/gpt-5-nano-2025-08-07",
).strip() or "Azure/gpt-5-nano-2025-08-07"
_DECOMPOSITION_REASONING_EFFORT = os.environ.get(
    "CUGA_DECOMPOSITION_REASONING_EFFORT",
    "minimal",
).strip() or "minimal"
_DECOMPOSITION_VERBOSITY = os.environ.get(
    "CUGA_DECOMPOSITION_VERBOSITY",
    "low",
).strip() or "low"
_DECOMPOSITION_MAX_COMPLETION_TOKENS = int(
    os.environ.get("CUGA_DECOMPOSITION_MAX_COMPLETION_TOKENS", "8000")
)
_DECOMPOSITION_MODEL_CACHE: Any | None = None


def _get_decomposition_model() -> Any:
    """Clone CUGA's configured OpenAI-compatible client for decomposition only.

    The global/default model remains untouched (normally OSS-120B).  The clone
    reuses the exact same LiteLLM/OpenAI-compatible client, credentials and base
    URL, but overrides only the request model.  For GPT-5-family decomposition we
    suppress sampling parameters and bind minimal reasoning + low verbosity.
    """
    global _DECOMPOSITION_MODEL_CACHE
    if _DECOMPOSITION_MODEL_CACHE is not None:
        return _DECOMPOSITION_MODEL_CACHE

    base_model = LLMManager().get_model(settings.agent.code.model)
    model_name = _DECOMPOSITION_MODEL_NAME

    # ChatOpenAI clients are Pydantic models.  A shallow model_copy preserves the
    # already-configured HTTP client/auth/base URL while letting this one call path
    # use a different gateway model alias.  This avoids mutating MODEL_NAME or the
    # cached OSS-120B instance used by the rest of the graph pipeline.
    if hasattr(base_model, "model_copy"):
        update: dict[str, Any] = {}
        if hasattr(base_model, "model_name"):
            update["model_name"] = model_name
        elif hasattr(base_model, "model"):
            update["model"] = model_name

        # GPT-5 reasoning models should not inherit OSS/non-reasoning sampling
        # parameters.  Setting these model fields to None keeps them out of the
        # normal ChatOpenAI default payload.
        if "gpt-5" in model_name.casefold():
            for field_name in ("temperature", "top_p", "max_tokens"):
                if hasattr(base_model, field_name):
                    update[field_name] = None
            if hasattr(base_model, "max_completion_tokens"):
                update["max_completion_tokens"] = (
                    _DECOMPOSITION_MAX_COMPLETION_TOKENS
                )

        model = base_model.model_copy(update=update, deep=False)
    else:
        # Defensive fallback for a non-Pydantic chat model.  Runtime binding still
        # leaves the global model object unchanged.
        model = base_model.bind(model=model_name)

    if "gpt-5" in model_name.casefold():
        model = model.bind(
            reasoning_effort=_DECOMPOSITION_REASONING_EFFORT,
            verbosity=_DECOMPOSITION_VERBOSITY,
            max_completion_tokens=_DECOMPOSITION_MAX_COMPLETION_TOKENS,
        )

    logger.info(
        "Decomposition model configured: model={} reasoning_effort={} "
        "verbosity={} max_completion_tokens={} global_model_unchanged=true",
        model_name,
        _DECOMPOSITION_REASONING_EFFORT if "gpt-5" in model_name.casefold() else "n/a",
        _DECOMPOSITION_VERBOSITY if "gpt-5" in model_name.casefold() else "n/a",
        _DECOMPOSITION_MAX_COMPLETION_TOKENS,
    )
    _DECOMPOSITION_MODEL_CACHE = model
    return model


_ATOMIC_FRAGMENT_EXACT = {
    "have to",
    "has to",
    "had to",
    "need to",
    "needs to",
    "needed to",
    "must",
    "should",
    "can",
    "could",
    "may",
    "might",
    "shall",
    "would",
    "will",
    "only if",
    "if",
    "unless",
    "then",
    "before",
    "after",
    "and",
    "or",
}


def _atomic_fragment_risk(text: str) -> str | None:
    """Return a diagnostic reason for obviously non-standalone atomic fragments.

    This is logging-only.  It deliberately does not repair/reject the model result,
    so runtime experiments measure the decomposer we are actually testing.
    """
    normalized = re.sub(r"\s+", " ", text.strip().casefold()).strip(" .,:;!?\"'")
    if not normalized:
        return "empty atomic text"
    if normalized in _ATOMIC_FRAGMENT_EXACT:
        return "bare modal/auxiliary/connective fragment"
    words = normalized.split()
    if len(words) <= 4 and words[-1:] == ["to"]:
        return "short atomic fragment ends in infinitival marker 'to'"
    return None


def _log_decomposition_trace(
    *,
    request: GraphBuildRequest,
    guidance_text: str,
    decision: LocalDecompositionDecision,
    stage: str,
    structural_repairs: int,
) -> None:
    """Emit one machine-readable parent -> decomposition record to wlateral logs."""
    payload = {
        "source_id": request.source_id,
        "source_type": request.source_type.value,
        "depth": request.metadata.get("decomposition_depth", 0),
        "semantic_role": request.metadata.get("semantic_role"),
        "model": _DECOMPOSITION_MODEL_NAME,
        "reasoning_effort": _DECOMPOSITION_REASONING_EFFORT,
        "verbosity": _DECOMPOSITION_VERBOSITY,
        "stage": stage,
        "structural_repairs": structural_repairs,
        "input_statement": request.content,
        "dependency_guidance": guidance_text,
        "decision": decision.model_dump(mode="json", exclude_none=True),
    }
    logger.info(
        "DECOMPOSITION_TRACE {}",
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    )

    if decision.kind == "atomic":
        risk = _atomic_fragment_risk(request.content)
        if risk is not None:
            logger.warning(
                "ATOMIC_FRAGMENT_RISK {}",
                json.dumps(
                    {
                        "source_id": request.source_id,
                        "depth": request.metadata.get("decomposition_depth", 0),
                        "model": _DECOMPOSITION_MODEL_NAME,
                        "statement": request.content,
                        "reason": risk,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )


def _normalize_enum_token(value: Any) -> Any:
    """Normalize harmless enum formatting drift without guessing semantics."""
    if not isinstance(value, str):
        return value
    return re.sub(r"[\s\-]+", "_", value.strip().casefold())


def _normalize_local_decomposition(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize common LLM drift before strict schema validation.

    Semantic content is never invented here. The normalization is limited to
    harmless enum formatting plus dropping malformed *optional* local-relation
    hints that cannot be represented safely.
    """
    raw = dict(raw)

    # Retrieval payload generation is a dedicated post-tree pass. Ignore any
    # eager top-level payload emitted by a provider so semantic decomposition
    # remains independent of S/P/O extraction.
    raw.pop("proposition", None)

    kind = raw.get("kind")
    if isinstance(kind, str):
        raw["kind"] = kind.strip().casefold()

    children = raw.get("children")
    if isinstance(children, list):
        normalized_children: list[Any] = []
        for child in children:
            if not isinstance(child, dict):
                normalized_children.append(child)
                continue
            normalized_child = dict(child)
            # S/P/O is not part of semantic decomposition. Tolerate providers that
            # still emit the legacy child field by discarding it before validation.
            normalized_child.pop("proposition", None)
            if "semantic_role" in normalized_child:
                normalized_child["semantic_role"] = _normalize_enum_token(
                    normalized_child["semantic_role"]
                )
            normalized_children.append(normalized_child)
        raw["children"] = normalized_children

    local_relations = raw.get("local_relations")
    if isinstance(local_relations, list):
        child_count = len(children) if isinstance(children, list) else 0
        cleaned_relations: list[dict[str, Any]] = []
        seen: set[tuple[int, int, str]] = set()

        for index, relation in enumerate(local_relations):
            if not isinstance(relation, dict):
                logger.warning(
                    "Ignoring malformed local relation hint at index={}: not an object",
                    index,
                )
                continue

            normalized_relation = dict(relation)

            for key in ("relation", "origin"):
                if key in normalized_relation:
                    normalized_relation[key] = _normalize_enum_token(
                        normalized_relation[key]
                    )

            source_index = normalized_relation.get("source_child_index")
            target_index = normalized_relation.get("target_child_index")

            # Numeric strings are harmless provider drift.
            if isinstance(source_index, str) and source_index.strip().isdigit():
                source_index = int(source_index.strip())
                normalized_relation["source_child_index"] = source_index
            if isinstance(target_index, str) and target_index.strip().isdigit():
                target_index = int(target_index.strip())
                normalized_relation["target_child_index"] = target_index

            if not isinstance(source_index, int) or not isinstance(target_index, int):
                logger.warning(
                    "Ignoring malformed local relation hint at index={}: "
                    "child indices are not integers",
                    index,
                )
                continue

            if source_index == target_index:
                logger.warning(
                    "Ignoring local relation self-edge at index={} child_index={}",
                    index,
                    source_index,
                )
                continue

            if (
                source_index < 0
                or target_index < 0
                or source_index >= child_count
                or target_index >= child_count
            ):
                logger.warning(
                    "Ignoring out-of-range local relation hint at index={} "
                    "source_child_index={} target_child_index={} child_count={}",
                    index,
                    source_index,
                    target_index,
                    child_count,
                )
                continue

            relation_name = normalized_relation.get("relation")
            if relation_name == RelationType.DECOMPOSES_INTO.value:
                logger.warning(
                    "Ignoring forbidden decomposes_into local relation at index={}",
                    index,
                )
                continue

            if not isinstance(relation_name, str):
                # Let the strict schema deal with unknown non-string values if
                # this hint otherwise appears structurally meaningful.
                cleaned_relations.append(normalized_relation)
                continue

            key = (source_index, target_index, relation_name)
            if key in seen:
                logger.warning(
                    "Ignoring duplicate local relation hint at index={} key={}",
                    index,
                    key,
                )
                continue
            seen.add(key)
            cleaned_relations.append(normalized_relation)

        raw["local_relations"] = cleaned_relations

    return raw


def _try_validate_local_decomposition(
    payload: Any,
    *,
    source_id: str,
    depth: int,
    label: str,
) -> LocalDecompositionDecision | None:
    """Validate one model payload without allowing raw Pydantic errors to escape."""
    if isinstance(payload, LocalDecompositionDecision):
        return payload

    normalized = (
        _normalize_local_decomposition(payload)
        if isinstance(payload, dict)
        else payload
    )

    try:
        return LocalDecompositionDecision.model_validate(normalized)
    except ValidationError as exc:
        logger.warning(
            "{} produced invalid LocalDecompositionDecision for source_id={} "
            "depth={}: {}",
            label,
            source_id,
            depth,
            exc,
        )
        return None


def _extract_json_object_from_text(text: str) -> dict[str, Any] | None:
    if not isinstance(text, str):
        return None

    stripped = text.strip()
    if not stripped:
        return None

    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    fence_match = re.search(
        r"```(?:json)?\s*(\{.*?\})\s*```",
        stripped,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if fence_match is not None:
        try:
            parsed = json.loads(fence_match.group(1))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    decoder = json.JSONDecoder()
    for index, char in enumerate(stripped):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    return None


def _extract_json_from_message(raw_message: Any) -> dict[str, Any] | None:
    if raw_message is None:
        return None

    content = getattr(raw_message, "content", None)

    if isinstance(content, dict):
        return content

    if isinstance(content, str):
        parsed = _extract_json_object_from_text(content)
        if parsed is not None:
            return parsed

    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                if isinstance(item.get("json"), dict):
                    return item["json"]
                for key in ("text", "content", "arguments", "input"):
                    value = item.get(key)
                    if isinstance(value, dict):
                        return value
                    if isinstance(value, str):
                        parsed = _extract_json_object_from_text(value)
                        if parsed is not None:
                            return parsed
            elif isinstance(item, str):
                parsed = _extract_json_object_from_text(item)
                if parsed is not None:
                    return parsed

    additional_kwargs = getattr(raw_message, "additional_kwargs", None) or {}

    function_call = additional_kwargs.get("function_call")
    if isinstance(function_call, dict):
        arguments = function_call.get("arguments")
        if isinstance(arguments, dict):
            return arguments
        if isinstance(arguments, str):
            parsed = _extract_json_object_from_text(arguments)
            if parsed is not None:
                return parsed

    reasoning_content = additional_kwargs.get("reasoning_content")
    if isinstance(reasoning_content, str):
        parsed = _extract_json_object_from_text(reasoning_content)
        if parsed is not None:
            return parsed

    return None


def _extract_structured_args(raw_message: Any) -> dict[str, Any] | None:
    tool_calls = getattr(raw_message, "tool_calls", None)
    if tool_calls:
        first_call = tool_calls[0]
        args = (
            first_call.get("args")
            if isinstance(first_call, dict)
            else getattr(first_call, "args", None)
        )
        if isinstance(args, dict):
            return args
        if isinstance(args, str):
            parsed = _extract_json_object_from_text(args)
            if parsed is not None:
                return parsed

    additional_kwargs = getattr(raw_message, "additional_kwargs", None) or {}
    raw_tool_calls = additional_kwargs.get("tool_calls", [])
    if raw_tool_calls:
        first_call = raw_tool_calls[0]
        if isinstance(first_call, dict):
            function = first_call.get("function", {})
            if isinstance(function, dict):
                arguments = function.get("arguments")
                if isinstance(arguments, dict):
                    return arguments
                if isinstance(arguments, str):
                    parsed = _extract_json_object_from_text(arguments)
                    if parsed is not None:
                        return parsed

    return _extract_json_from_message(raw_message)


def _plain_json_retry(
    *,
    model: Any,
    messages: list[BaseMessage],
    schema: type[BaseModel],
    label: str,
) -> dict[str, Any] | None:
    """Retry once without function calling and request schema-conformant JSON."""
    schema_json = json.dumps(
        schema.model_json_schema(),
        ensure_ascii=False,
    )

    retry_messages = [
        *messages,
        HumanMessage(
            content=(
                "The previous structured-output attempt did not produce a "
                "recoverable result. Return ONLY one JSON object that matches "
                "the following JSON schema exactly. Do not use markdown, code "
                "fences, commentary, or text outside the JSON object.\n\n"
                f"JSON_SCHEMA:\n{schema_json}"
            )
        ),
    ]

    logger.warning(
        "Structured output missing for {}. Retrying once as plain JSON.",
        label,
    )

    response = model.invoke(retry_messages)
    return _extract_json_from_message(response)


def _build_context_source_blocks(
    text: str,
    *,
    max_block_chars: int = _CONTEXT_BLOCK_MAX_CHARS,
) -> list[ContextSourceBlock]:
    """Create small exact blocks that the LLM can group semantically.

    Lines are preferred because they naturally expose headings, list items,
    tables, and prose boundaries without requiring domain-specific keywords.
    Blank lines are retained by attaching them to the preceding block. Oversized
    single lines are split on sentence-like boundaries, then whitespace.
    """
    if not text:
        return []
    if max_block_chars <= 0:
        raise ValueError("max_block_chars must be positive")

    raw_spans: list[tuple[int, int]] = []
    cursor = 0

    for line in text.splitlines(keepends=True):
        line_end = cursor + len(line)

        if line_end - cursor <= max_block_chars:
            raw_spans.append((cursor, line_end))
        else:
            raw_spans.extend(
                _context_split_oversized_span(
                    text,
                    start=cursor,
                    end=line_end,
                    max_chars=max_block_chars,
                )
            )
        cursor = line_end

    if cursor < len(text):
        tail_end = len(text)
        if tail_end - cursor <= max_block_chars:
            raw_spans.append((cursor, tail_end))
        else:
            raw_spans.extend(
                _context_split_oversized_span(
                    text,
                    start=cursor,
                    end=tail_end,
                    max_chars=max_block_chars,
                )
            )

    if not raw_spans:
        raw_spans = [(0, len(text))]

    # Attach whitespace-only blocks to the previous block when possible. This
    # preserves exact reconstruction without making blank lines semantic units.
    merged_spans: list[tuple[int, int]] = []
    for start, end in raw_spans:
        block_text = text[start:end]
        if not block_text.strip() and merged_spans:
            previous_start, _ = merged_spans[-1]
            merged_spans[-1] = (previous_start, end)
        else:
            merged_spans.append((start, end))

    # If the source begins with whitespace-only content, keep it attached to the
    # first following block.
    if len(merged_spans) >= 2 and not text[
        merged_spans[0][0]:merged_spans[0][1]
    ].strip():
        first_start, _ = merged_spans[0]
        _, second_end = merged_spans[1]
        merged_spans[1] = (first_start, second_end)
        merged_spans.pop(0)

    blocks = [
        ContextSourceBlock(
            index=index,
            text=text[start:end],
            start=start,
            end=end,
        )
        for index, (start, end) in enumerate(merged_spans)
    ]

    _validate_context_source_blocks(text, blocks)
    return blocks


def _context_split_oversized_span(
    text: str,
    *,
    start: int,
    end: int,
    max_chars: int,
) -> list[tuple[int, int]]:
    """Split one oversized source line without rewriting it."""
    segment = text[start:end]
    boundaries = [start]

    for match in re.finditer(
        r"""[.!?](?:["')\]]+)?(?:\s+|$)""",
        segment,
    ):
        boundary = start + match.end()
        if start < boundary < end:
            boundaries.append(boundary)

    boundaries.append(end)
    boundaries = sorted(set(boundaries))

    sentence_spans = [
        (boundaries[index], boundaries[index + 1])
        for index in range(len(boundaries) - 1)
        if boundaries[index] < boundaries[index + 1]
    ]

    packed: list[tuple[int, int]] = []
    current_start: int | None = None
    current_end: int | None = None

    for span_start, span_end in sentence_spans:
        if span_end - span_start > max_chars:
            hard_parts = _hard_split_span(
                text,
                start=span_start,
                end=span_end,
                max_chars=max_chars,
            )
        else:
            hard_parts = [(span_start, span_end)]

        for part_start, part_end in hard_parts:
            if current_start is None:
                current_start, current_end = part_start, part_end
                continue

            if (
                part_start == current_end
                and part_end - current_start <= max_chars
            ):
                current_end = part_end
            else:
                packed.append((current_start, current_end))
                current_start, current_end = part_start, part_end

    if current_start is not None and current_end is not None:
        packed.append((current_start, current_end))

    return packed or [(start, end)]


def _validate_context_source_blocks(
    text: str,
    blocks: list[ContextSourceBlock],
) -> None:
    """Verify exact, gap-free deterministic source blocking."""
    if not blocks:
        raise ContextualChunkingModelError(
            "Non-empty source produced no contextualization blocks"
        )

    cursor = 0
    reconstructed: list[str] = []

    for expected_index, block in enumerate(blocks):
        if block.index != expected_index:
            raise ContextualChunkingModelError(
                "Context block indices must be dense and ordered"
            )
        if block.start != cursor:
            raise ContextualChunkingModelError(
                "Context blocks contain a gap or overlap before "
                f"block_index={block.index}"
            )
        if block.end <= block.start:
            raise ContextualChunkingModelError(
                f"Context block {block.index} has non-positive length"
            )
        if block.text != text[block.start:block.end]:
            raise ContextualChunkingModelError(
                f"Context block {block.index} does not match its source span"
            )

        reconstructed.append(block.text)
        cursor = block.end

    if cursor != len(text) or "".join(reconstructed) != text:
        raise ContextualChunkingModelError(
            "Context blocks do not reconstruct the source exactly"
        )


def _render_context_source_blocks(
    blocks: list[ContextSourceBlock],
) -> str:
    return "\n\n".join(
        (
            f"BLOCK_INDEX: {block.index}\n"
            "BLOCK_BEGIN\n"
            f"{block.text}"
            "BLOCK_END"
        )
        for block in blocks
    )


def _semantic_segmentation_request_text(
    request: GraphBuildRequest,
    blocks: list[ContextSourceBlock],
    *,
    active_start_block: int,
    active_end_block: int,
    max_chunk_chars: int,
    recursion_depth: int,
) -> str:
    """Serialize one recursive semantic-segmentation problem."""
    active_start = blocks[active_start_block].start
    active_end = blocks[active_end_block].end
    active_chars = active_end - active_start

    return (
        f"SOURCE_TYPE: {request.source_type.value}\n"
        f"SOURCE_ID: {request.source_id}\n"
        f"RECURSION_DEPTH: {recursion_depth}\n"
        f"LEAF_THRESHOLD_CHARS: {max_chunk_chars}\n"
        f"ACTIVE_BLOCK_RANGE: {active_start_block}..{active_end_block}\n"
        f"ACTIVE_RANGE_CHARS: {active_chars}\n"
        f"TOTAL_BLOCK_COUNT: {len(blocks)}\n\n"
        "SOURCE_BLOCKS_BEGIN\n"
        f"{_render_context_source_blocks(blocks)}\n"
        "SOURCE_BLOCKS_END"
    )


def _invoke_semantic_segmentation_decision(
    *,
    model: Any,
    messages: list[BaseMessage],
    source_id: str,
    label: str,
) -> SemanticSegmentationDecision:
    """Invoke recursive semantic segmentation with structured-output recovery."""
    structured_model = model.with_structured_output(
        SemanticSegmentationDecision,
        method="function_calling",
        include_raw=True,
    )

    structured_exception: Exception | None = None

    try:
        result = structured_model.invoke(messages)
    except Exception as exc:
        structured_exception = exc
        logger.warning(
            "{} structured call failed for source_id={}: {}. "
            "Retrying once as plain JSON.",
            label,
            source_id,
            exc,
        )
        result = None

    if isinstance(result, dict):
        parsed = result.get("parsed")
        if parsed is not None:
            try:
                return (
                    parsed
                    if isinstance(parsed, SemanticSegmentationDecision)
                    else SemanticSegmentationDecision.model_validate(parsed)
                )
            except ValidationError as exc:
                logger.warning(
                    "{} parsed output failed validation for source_id={}: {}",
                    label,
                    source_id,
                    exc,
                )

        raw_message = result.get("raw")
        parsing_error = result.get("parsing_error")
        raw_args = _extract_structured_args(raw_message)

        if raw_args is not None:
            try:
                return SemanticSegmentationDecision.model_validate(raw_args)
            except ValidationError as exc:
                logger.warning(
                    "{} raw structured args failed validation for "
                    "source_id={}: {}",
                    label,
                    source_id,
                    exc,
                )

        logger.warning(
            "{} returned no valid structured decision for source_id={}: {}. "
            "Retrying once as plain JSON.",
            label,
            source_id,
            parsing_error,
        )

    raw_args = _plain_json_retry(
        model=model,
        messages=messages,
        schema=SemanticSegmentationDecision,
        label=label,
    )
    if raw_args is not None:
        try:
            return SemanticSegmentationDecision.model_validate(raw_args)
        except ValidationError as exc:
            logger.warning(
                "{} plain-JSON retry failed validation for source_id={}: {}",
                label,
                source_id,
                exc,
            )

    error = ContextualChunkingModelError(
        f"Could not recover semantic segmentation decision for "
        f"source_id={source_id}"
    )
    if structured_exception is not None:
        raise error from structured_exception
    raise error


def _validate_semantic_segmentation_decision(
    *,
    decision: SemanticSegmentationDecision,
    active_start_block: int,
    active_end_block: int,
    require_progress: bool,
) -> None:
    """Validate coverage/order only; Python never chooses a semantic split."""
    if not decision.chunks:
        raise ContextualChunkingModelError(
            "Semantic segmentation returned no chunks"
        )

    expected_start = active_start_block

    for chunk_index, plan in enumerate(decision.chunks):
        if plan.start_block_index != expected_start:
            raise ContextualChunkingModelError(
                "Semantic segmentation must be gap-free and ordered: "
                f"chunk[{chunk_index}] expected start_block_index="
                f"{expected_start}, got {plan.start_block_index}"
            )
        if plan.end_block_index < plan.start_block_index:
            raise ContextualChunkingModelError(
                f"Semantic chunk[{chunk_index}] has end before start"
            )
        if plan.start_block_index < active_start_block:
            raise ContextualChunkingModelError(
                f"Semantic chunk[{chunk_index}] starts outside active range"
            )
        if plan.end_block_index > active_end_block:
            raise ContextualChunkingModelError(
                f"Semantic chunk[{chunk_index}] ends outside active range"
            )

        expected_start = plan.end_block_index + 1

    if expected_start != active_end_block + 1:
        raise ContextualChunkingModelError(
            "Semantic segmentation does not cover the complete active block range"
        )

    if require_progress and len(decision.chunks) < 2:
        raise ContextualChunkingModelError(
            "Oversized semantic range was returned unchanged; recursive "
            "segmentation requires at least two narrower chunks"
        )


def _semantic_segmentation_repair_message(
    *,
    decision: SemanticSegmentationDecision,
    issue: str,
    active_start_block: int,
    active_end_block: int,
) -> HumanMessage:
    return HumanMessage(
        content=(
            "SEMANTIC_SEGMENTATION_REPAIR_REQUIRED\n"
            "Revise the partition of the SAME ACTIVE_BLOCK_RANGE. "
            "Do not rewrite or summarize the source.\n\n"
            f"ACTIVE_BLOCK_RANGE: {active_start_block}..{active_end_block}\n"
            f"ISSUE:\n{issue}\n\n"
            "PREVIOUS_DECISION_BEGIN\n"
            f"{json.dumps(decision.model_dump(mode='json'), ensure_ascii=False, indent=2)}\n"
            "PREVIOUS_DECISION_END\n\n"
            "Return a complete, ordered, contiguous, gap-free partition of only "
            "the active range. If the active range is oversized, return at least "
            "two strictly smaller semantic chunks. Choose boundaries by meaning, "
            "not by arbitrary character cutting."
        )
    )


def _segment_range_recursively(
    *,
    request: GraphBuildRequest,
    blocks: list[ContextSourceBlock],
    active_start_block: int,
    active_end_block: int,
    max_chunk_chars: int,
    recursion_depth: int,
    model: Any,
) -> list[SemanticLeafChunk]:
    """Recursively ask the LLM to split only oversized semantic ranges."""
    if recursion_depth > _MAX_SEMANTIC_SEGMENTATION_DEPTH:
        raise ContextualChunkingModelError(
            "Maximum semantic-segmentation recursion depth exceeded for "
            f"source_id={request.source_id} active_range="
            f"{active_start_block}..{active_end_block}"
        )

    source_start = blocks[active_start_block].start
    source_end = blocks[active_end_block].end
    source_chars = source_end - source_start

    if source_chars <= max_chunk_chars:
        return [
            SemanticLeafChunk(
                start_block_index=active_start_block,
                end_block_index=active_end_block,
                start=source_start,
                end=source_end,
            )
        ]

    base_messages: list[BaseMessage] = [
        SystemMessage(content=_SEMANTIC_SEGMENTATION_SYSTEM_PROMPT),
        HumanMessage(
            content=_semantic_segmentation_request_text(
                request,
                blocks,
                active_start_block=active_start_block,
                active_end_block=active_end_block,
                max_chunk_chars=max_chunk_chars,
                recursion_depth=recursion_depth,
            )
        ),
    ]

    generation_messages = list(base_messages)
    decision = _invoke_semantic_segmentation_decision(
        model=model,
        messages=generation_messages,
        source_id=request.source_id,
        label="semantic segmentation",
    )

    for attempt in range(_MAX_SEMANTIC_SEGMENTATION_RETRIES + 1):
        try:
            _validate_semantic_segmentation_decision(
                decision=decision,
                active_start_block=active_start_block,
                active_end_block=active_end_block,
                require_progress=True,
            )
            break
        except ContextualChunkingModelError as exc:
            logger.warning(
                "Semantic segmentation structural validation failed for "
                "source_id={} depth={} active_range={}..{} attempt={}/{}: {}",
                request.source_id,
                recursion_depth,
                active_start_block,
                active_end_block,
                attempt + 1,
                _MAX_SEMANTIC_SEGMENTATION_RETRIES + 1,
                exc,
            )

            if attempt >= _MAX_SEMANTIC_SEGMENTATION_RETRIES:
                raise

            generation_messages = [
                *base_messages,
                _semantic_segmentation_repair_message(
                    decision=decision,
                    issue=str(exc),
                    active_start_block=active_start_block,
                    active_end_block=active_end_block,
                ),
            ]
            decision = _invoke_semantic_segmentation_decision(
                model=model,
                messages=generation_messages,
                source_id=request.source_id,
                label="semantic segmentation repair",
            )

    logger.info(
        "Semantic segmentation accepted: source_id={} depth={} active_range={}..{} "
        "active_chars={} children={} child_ranges={}",
        request.source_id,
        recursion_depth,
        active_start_block,
        active_end_block,
        source_chars,
        len(decision.chunks),
        [
            (chunk.start_block_index, chunk.end_block_index)
            for chunk in decision.chunks
        ],
    )

    leaves: list[SemanticLeafChunk] = []
    for plan in decision.chunks:
        leaves.extend(
            _segment_range_recursively(
                request=request,
                blocks=blocks,
                active_start_block=plan.start_block_index,
                active_end_block=plan.end_block_index,
                max_chunk_chars=max_chunk_chars,
                recursion_depth=recursion_depth + 1,
                model=model,
            )
        )
    return leaves


def _validate_semantic_leaf_partition(
    *,
    source: str,
    blocks: list[ContextSourceBlock],
    leaves: list[SemanticLeafChunk],
    max_chunk_chars: int,
) -> None:
    """Verify final leaves are exact, gap-free, ordered, and bounded."""
    if not leaves:
        raise ContextualChunkingModelError(
            "Recursive semantic segmentation produced no leaf chunks"
        )

    expected_block = 0
    reconstructed: list[str] = []

    for leaf_index, leaf in enumerate(leaves):
        if leaf.start_block_index != expected_block:
            raise ContextualChunkingModelError(
                "Final semantic leaves contain a gap/overlap before "
                f"leaf[{leaf_index}]"
            )
        if leaf.end - leaf.start > max_chunk_chars:
            raise ContextualChunkingModelError(
                f"Final semantic leaf[{leaf_index}] exceeds threshold "
                f"{max_chunk_chars}: chars={leaf.end - leaf.start}"
            )

        expected_start = blocks[leaf.start_block_index].start
        expected_end = blocks[leaf.end_block_index].end
        if leaf.start != expected_start or leaf.end != expected_end:
            raise ContextualChunkingModelError(
                f"Final semantic leaf[{leaf_index}] source span does not match "
                "its block range"
            )

        reconstructed.append(source[leaf.start:leaf.end])
        expected_block = leaf.end_block_index + 1

    if expected_block != len(blocks):
        raise ContextualChunkingModelError(
            "Final semantic leaves do not cover every source block"
        )

    if "".join(reconstructed) != source:
        raise ContextualChunkingModelError(
            "Final semantic leaves do not reconstruct the source exactly"
        )


def _final_contextualization_request_text(
    request: GraphBuildRequest,
    blocks: list[ContextSourceBlock],
    leaves: list[SemanticLeafChunk],
    *,
    max_contextualized_chars: int,
) -> str:
    leaf_payload = [
        {
            "chunk_index": index,
            "start_block_index": leaf.start_block_index,
            "end_block_index": leaf.end_block_index,
            "source_span": {"start": leaf.start, "end": leaf.end},
            "source_text": request.content[leaf.start:leaf.end],
        }
        for index, leaf in enumerate(leaves)
    ]

    return (
        f"SOURCE_TYPE: {request.source_type.value}\n"
        f"SOURCE_ID: {request.source_id}\n"
        f"FINAL_LEAF_COUNT: {len(leaves)}\n"
        f"MAX_CONTEXTUALIZED_TEXT_CHARS: {max_contextualized_chars}\n\n"
        "SOURCE_BLOCKS_BEGIN\n"
        f"{_render_context_source_blocks(blocks)}\n"
        "SOURCE_BLOCKS_END\n\n"
        "FINAL_LEAF_CHUNKS_BEGIN\n"
        f"{json.dumps(leaf_payload, ensure_ascii=False, indent=2)}\n"
        "FINAL_LEAF_CHUNKS_END"
    )


def _invoke_final_contextualization_decision(
    *,
    model: Any,
    messages: list[BaseMessage],
    source_id: str,
    label: str,
) -> FinalChunkContextDecision:
    structured_model = model.with_structured_output(
        FinalChunkContextDecision,
        method="function_calling",
        include_raw=True,
    )

    structured_exception: Exception | None = None

    try:
        result = structured_model.invoke(messages)
    except Exception as exc:
        structured_exception = exc
        logger.warning(
            "{} structured call failed for source_id={}: {}. "
            "Retrying once as plain JSON.",
            label,
            source_id,
            exc,
        )
        result = None

    if isinstance(result, dict):
        parsed = result.get("parsed")
        if parsed is not None:
            try:
                return (
                    parsed
                    if isinstance(parsed, FinalChunkContextDecision)
                    else FinalChunkContextDecision.model_validate(parsed)
                )
            except ValidationError as exc:
                logger.warning(
                    "{} parsed output failed validation for source_id={}: {}",
                    label,
                    source_id,
                    exc,
                )

        raw_message = result.get("raw")
        parsing_error = result.get("parsing_error")
        raw_args = _extract_structured_args(raw_message)

        if raw_args is not None:
            try:
                return FinalChunkContextDecision.model_validate(raw_args)
            except ValidationError as exc:
                logger.warning(
                    "{} raw structured args failed validation for "
                    "source_id={}: {}",
                    label,
                    source_id,
                    exc,
                )

        logger.warning(
            "{} returned no valid structured decision for source_id={}: {}. "
            "Retrying once as plain JSON.",
            label,
            source_id,
            parsing_error,
        )

    raw_args = _plain_json_retry(
        model=model,
        messages=messages,
        schema=FinalChunkContextDecision,
        label=label,
    )
    if raw_args is not None:
        try:
            return FinalChunkContextDecision.model_validate(raw_args)
        except ValidationError as exc:
            logger.warning(
                "{} plain-JSON retry failed validation for source_id={}: {}",
                label,
                source_id,
                exc,
            )

    error = ContextualChunkingModelError(
        f"Could not recover final contextualization decision for "
        f"source_id={source_id}"
    )
    if structured_exception is not None:
        raise error from structured_exception
    raise error


def _materialize_final_contextual_chunks(
    *,
    request: GraphBuildRequest,
    blocks: list[ContextSourceBlock],
    leaves: list[SemanticLeafChunk],
    decision: FinalChunkContextDecision,
    max_contextualized_chars: int,
) -> list[ContextualSourceChunk]:
    """Resolve contextualization outputs to exact authoritative source spans."""
    if len(decision.chunks) != len(leaves):
        raise ContextualChunkingModelError(
            "Final contextualization returned the wrong number of chunks: "
            f"expected={len(leaves)} actual={len(decision.chunks)}"
        )

    by_index: dict[int, FinalChunkContextPlan] = {}
    for plan in decision.chunks:
        if plan.chunk_index in by_index:
            raise ContextualChunkingModelError(
                f"Final contextualization duplicated chunk_index={plan.chunk_index}"
            )
        by_index[plan.chunk_index] = plan

    expected_indices = set(range(len(leaves)))
    if set(by_index) != expected_indices:
        raise ContextualChunkingModelError(
            "Final contextualization chunk indices do not exactly match the "
            f"final leaves: expected={sorted(expected_indices)} "
            f"actual={sorted(by_index)}"
        )

    materialized: list[ContextualSourceChunk] = []

    for chunk_index, leaf in enumerate(leaves):
        plan = by_index[chunk_index]
        source_text = request.content[leaf.start:leaf.end]

        normalized_context_indices: list[int] = []
        seen_context_indices: set[int] = set()

        for raw_index in plan.context_block_indices:
            if raw_index < 0 or raw_index >= len(blocks):
                raise ContextualChunkingModelError(
                    f"Final contextualization chunk[{chunk_index}] references "
                    f"invalid context block index {raw_index}"
                )
            if leaf.start_block_index <= raw_index <= leaf.end_block_index:
                continue
            if raw_index in seen_context_indices:
                continue
            seen_context_indices.add(raw_index)
            normalized_context_indices.append(raw_index)

        normalized_context_indices.sort()
        context_blocks = [blocks[index] for index in normalized_context_indices]

        contextualized_text = plan.contextualized_text.strip()
        if not contextualized_text:
            raise ContextualChunkingModelError(
                f"Final contextualization chunk[{chunk_index}] has empty "
                "contextualized_text"
            )
        if len(contextualized_text) > max_contextualized_chars:
            raise ContextualChunkingModelError(
                f"Final contextualization chunk[{chunk_index}] exceeds "
                f"max_contextualized_chars={max_contextualized_chars}: "
                f"chars={len(contextualized_text)}"
            )

        materialized.append(
            ContextualSourceChunk(
                source_text=source_text,
                contextualized_text=contextualized_text,
                start=leaf.start,
                end=leaf.end,
                context_source_texts=tuple(
                    block.text for block in context_blocks
                ),
                context_spans=tuple(
                    (block.start, block.end) for block in context_blocks
                ),
            )
        )

    if "".join(chunk.source_text for chunk in materialized) != request.content:
        raise ContextualChunkingModelError(
            "Final contextualized chunks do not reconstruct the source exactly"
        )

    return materialized


def _contextual_chunking_audit_text(
    request: GraphBuildRequest,
    blocks: list[ContextSourceBlock],
    chunks: list[ContextualSourceChunk],
) -> str:
    block_payload = [
        {
            "index": block.index,
            "text": block.text,
            "start": block.start,
            "end": block.end,
        }
        for block in blocks
    ]
    chunk_payload = [
        {
            "chunk_index": index,
            "source_text": chunk.source_text,
            "source_span": {"start": chunk.start, "end": chunk.end},
            "context_source_texts": list(chunk.context_source_texts),
            "context_spans": [
                {"start": start, "end": end}
                for start, end in chunk.context_spans
            ],
            "contextualized_text": chunk.contextualized_text,
        }
        for index, chunk in enumerate(chunks)
    ]

    return (
        f"SOURCE_TYPE: {request.source_type.value}\n"
        f"SOURCE_ID: {request.source_id}\n\n"
        "SOURCE_BLOCKS_BEGIN\n"
        f"{json.dumps(block_payload, ensure_ascii=False, indent=2)}\n"
        "SOURCE_BLOCKS_END\n\n"
        "FINAL_CONTEXTUALIZED_LEAVES_BEGIN\n"
        f"{json.dumps(chunk_payload, ensure_ascii=False, indent=2)}\n"
        "FINAL_CONTEXTUALIZED_LEAVES_END"
    )


def _try_validate_contextual_chunking_assessment(
    payload: Any,
    *,
    source_id: str,
    chunk_count: int,
) -> ContextualChunkingAssessment | None:
    """Validate audit output and require exact per-leaf localization."""
    try:
        assessment = (
            payload
            if isinstance(payload, ContextualChunkingAssessment)
            else ContextualChunkingAssessment.model_validate(payload)
        )
    except ValidationError as exc:
        logger.warning(
            "Contextual chunk audit output failed validation for source_id={}: {}",
            source_id,
            exc,
        )
        return None

    invalid_indices = sorted(
        {
            issue.chunk_index
            for issue in assessment.issues
            if issue.chunk_index >= chunk_count
        }
    )
    if invalid_indices:
        logger.warning(
            "Contextual chunk audit referenced invalid chunk indices for "
            "source_id={}: invalid={} chunk_count={}",
            source_id,
            invalid_indices,
            chunk_count,
        )
        return None

    if assessment.complete and assessment.issues:
        logger.warning(
            "Contextual chunk audit returned complete=true with localized issues "
            "for source_id={}; retrying the same audit",
            source_id,
        )
        return None

    if not assessment.complete and not assessment.issues:
        logger.warning(
            "Contextual chunk audit returned complete=false without any localized "
            "chunk issues for source_id={}; retrying the same audit",
            source_id,
        )
        return None

    return assessment


def _assess_contextual_chunking(
    *,
    request: GraphBuildRequest,
    blocks: list[ContextSourceBlock],
    chunks: list[ContextualSourceChunk],
) -> ContextualChunkingAssessment:
    """Audit contextualized leaves and require exact bad-leaf indices."""
    messages: list[BaseMessage] = [
        SystemMessage(content=_CONTEXTUAL_CHUNKING_AUDIT_SYSTEM_PROMPT),
        HumanMessage(
            content=_contextual_chunking_audit_text(
                request,
                blocks,
                chunks,
            )
        ),
    ]

    model = _get_model(reasoning_effort="medium")

    # Protocol failure is not semantic failure. If the auditor omits chunk_index,
    # returns an impossible index, or otherwise violates the structured contract,
    # retry the SAME audit without changing any contextualized leaf.
    for protocol_attempt in range(2):
        structured_model = model.with_structured_output(
            ContextualChunkingAssessment,
            method="function_calling",
            include_raw=True,
        )

        try:
            result = structured_model.invoke(messages)
        except Exception as exc:
            logger.warning(
                "Contextual chunk audit structured call failed for source_id={} "
                "protocol_attempt={}/2: {}",
                request.source_id,
                protocol_attempt + 1,
                exc,
            )
            result = None

        if isinstance(result, dict):
            for payload in (
                result.get("parsed"),
                _extract_structured_args(result.get("raw")),
            ):
                if payload is None:
                    continue
                assessment = _try_validate_contextual_chunking_assessment(
                    payload,
                    source_id=request.source_id,
                    chunk_count=len(chunks),
                )
                if assessment is not None:
                    return assessment

        raw_args = _plain_json_retry(
            model=model,
            messages=messages,
            schema=ContextualChunkingAssessment,
            label="contextual chunk audit",
        )
        if raw_args is not None:
            assessment = _try_validate_contextual_chunking_assessment(
                raw_args,
                source_id=request.source_id,
                chunk_count=len(chunks),
            )
            if assessment is not None:
                return assessment

        logger.warning(
            "Contextual chunk auditor protocol failed for source_id={} "
            "protocol_attempt={}/2; retrying the same audit without changing "
            "the contextualization candidate",
            request.source_id,
            protocol_attempt + 1,
        )

    raise ContextualChunkingModelError(
        "Could not recover localized ContextualChunkingAssessment for "
        f"source_id={request.source_id}"
    )


def _final_contextualization_structural_repair_message(
    *,
    decision: FinalChunkContextDecision,
    issue: str,
) -> HumanMessage:
    """Fallback only for batch-level structural corruption before semantic audit."""
    return HumanMessage(
        content=(
            "FINAL_CONTEXTUALIZATION_STRUCTURAL_REPAIR_REQUIRED\n"
            "The batch output cannot be materialized structurally, so exact bad "
            "leaf indices are not yet trustworthy. Revise the contextualization "
            "of the SAME finalized leaf chunks. Do not change chunk boundaries.\n\n"
            f"STRUCTURAL_ISSUE:\n{issue}\n\n"
            "PREVIOUS_DECISION_BEGIN\n"
            f"{json.dumps(decision.model_dump(mode='json'), ensure_ascii=False, indent=2)}\n"
            "PREVIOUS_DECISION_END\n\n"
            "Return exactly one output per supplied chunk_index. Preserve source "
            "meaning, use only necessary external context, and keep each rewrite "
            "concise and self-contained."
        )
    )


def _final_contextualization_targeted_repair_text(
    *,
    request: GraphBuildRequest,
    blocks: list[ContextSourceBlock],
    leaves: list[SemanticLeafChunk],
    decision: FinalChunkContextDecision,
    assessment: ContextualChunkingAssessment,
    max_contextualized_chars: int,
) -> tuple[str, tuple[int, ...]]:
    """Serialize only the leaves named by the audit, plus their exact feedback."""
    by_index = {plan.chunk_index: plan for plan in decision.chunks}
    issues_by_index: dict[int, list[str]] = {}
    for issue in assessment.issues:
        issues_by_index.setdefault(issue.chunk_index, []).append(issue.issue)

    target_indices = tuple(sorted(issues_by_index))
    if not target_indices:
        raise ContextualChunkingModelError(
            "Targeted contextualization repair requires at least one bad chunk"
        )

    targets: list[dict[str, Any]] = []
    for chunk_index in target_indices:
        if chunk_index >= len(leaves):
            raise ContextualChunkingModelError(
                f"Targeted repair references invalid chunk_index={chunk_index}"
            )
        prior = by_index.get(chunk_index)
        if prior is None:
            raise ContextualChunkingModelError(
                "Targeted repair cannot find the previous contextualization for "
                f"chunk_index={chunk_index}"
            )
        leaf = leaves[chunk_index]
        targets.append(
            {
                "chunk_index": chunk_index,
                "start_block_index": leaf.start_block_index,
                "end_block_index": leaf.end_block_index,
                "source_span": {"start": leaf.start, "end": leaf.end},
                "source_text": request.content[leaf.start:leaf.end],
                "previous_context_block_indices": list(
                    prior.context_block_indices
                ),
                "previous_contextualized_text": prior.contextualized_text,
                "audit_feedback": issues_by_index[chunk_index],
            }
        )

    text = (
        f"SOURCE_TYPE: {request.source_type.value}\n"
        f"SOURCE_ID: {request.source_id}\n"
        f"TARGET_REPAIR_COUNT: {len(target_indices)}\n"
        f"MAX_CONTEXTUALIZED_TEXT_CHARS: {max_contextualized_chars}\n\n"
        "SOURCE_BLOCKS_BEGIN\n"
        f"{_render_context_source_blocks(blocks)}\n"
        "SOURCE_BLOCKS_END\n\n"
        "TARGET_LEAVES_BEGIN\n"
        f"{json.dumps(targets, ensure_ascii=False, indent=2)}\n"
        "TARGET_LEAVES_END\n\n"
        "Repair ONLY the supplied TARGET_LEAVES. For each target, use its exact "
        "source_text as authoritative, preserve everything already correct in "
        "the previous contextualization, and correct the listed audit_feedback. "
        "Do not return or rewrite any non-target chunk."
    )
    return text, target_indices


def _targeted_context_repair_contract_issue(
    repair: FinalChunkContextRepair,
    *,
    target_indices: tuple[int, ...],
) -> str | None:
    """Return a protocol error when repair indices are not exactly the targets."""
    actual = [plan.chunk_index for plan in repair.replacements]
    if len(actual) != len(set(actual)):
        return f"duplicate replacement indices: actual={actual}"
    expected_set = set(target_indices)
    actual_set = set(actual)
    if actual_set != expected_set:
        return (
            "replacement indices do not match audit targets: "
            f"expected={sorted(expected_set)} actual={sorted(actual_set)}"
        )
    return None


def _invoke_final_contextualization_targeted_repair(
    *,
    model: Any,
    request: GraphBuildRequest,
    blocks: list[ContextSourceBlock],
    leaves: list[SemanticLeafChunk],
    decision: FinalChunkContextDecision,
    assessment: ContextualChunkingAssessment,
    max_contextualized_chars: int,
) -> tuple[FinalChunkContextRepair, tuple[int, ...]]:
    """Regenerate only audit-identified bad leaves with their local feedback."""
    repair_text, target_indices = _final_contextualization_targeted_repair_text(
        request=request,
        blocks=blocks,
        leaves=leaves,
        decision=decision,
        assessment=assessment,
        max_contextualized_chars=max_contextualized_chars,
    )

    system_prompt = (
        _FINAL_CONTEXTUALIZATION_SYSTEM_PROMPT
        + "\n\nTARGETED REPAIR MODE\n"
        + "--------------------\n"
        + "This TARGETED REPAIR MODE overrides the normal full-batch output "
        + "contract above. You are repairing only the explicitly supplied "
        + "TARGET_LEAVES. Return FinalChunkContextRepair with exactly one "
        + "replacement for every target "
        + "chunk_index and no replacements for any other chunk. Keep each target's "
        + "chunk_index unchanged. The supplied audit_feedback is correction guidance, "
        + "while SOURCE BLOCKS and target source_text remain authoritative."
    )
    messages: list[BaseMessage] = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=repair_text),
    ]

    structured_model = model.with_structured_output(
        FinalChunkContextRepair,
        method="function_calling",
        include_raw=True,
    )
    try:
        result = structured_model.invoke(messages)
    except Exception as exc:
        logger.warning(
            "Targeted final contextualization repair structured call failed for "
            "source_id={} targets={}: {}. Retrying once as plain JSON.",
            request.source_id,
            list(target_indices),
            exc,
        )
        result = None

    if isinstance(result, dict):
        for payload in (
            result.get("parsed"),
            _extract_structured_args(result.get("raw")),
        ):
            if payload is None:
                continue
            try:
                repair = (
                    payload
                    if isinstance(payload, FinalChunkContextRepair)
                    else FinalChunkContextRepair.model_validate(payload)
                )
                contract_issue = _targeted_context_repair_contract_issue(
                    repair,
                    target_indices=target_indices,
                )
                if contract_issue is None:
                    return repair, target_indices
                logger.warning(
                    "Targeted final contextualization repair violated target "
                    "contract for source_id={} targets={}: {}",
                    request.source_id,
                    list(target_indices),
                    contract_issue,
                )
            except ValidationError as exc:
                logger.warning(
                    "Targeted final contextualization repair output failed "
                    "validation for source_id={} targets={}: {}",
                    request.source_id,
                    list(target_indices),
                    exc,
                )

    raw_args = _plain_json_retry(
        model=model,
        messages=messages,
        schema=FinalChunkContextRepair,
        label="targeted final chunk contextualization repair",
    )
    if raw_args is not None:
        try:
            repair = FinalChunkContextRepair.model_validate(raw_args)
            contract_issue = _targeted_context_repair_contract_issue(
                repair,
                target_indices=target_indices,
            )
            if contract_issue is None:
                return repair, target_indices
            logger.warning(
                "Targeted final contextualization plain-JSON repair violated "
                "target contract for source_id={} targets={}: {}",
                request.source_id,
                list(target_indices),
                contract_issue,
            )
        except ValidationError as exc:
            logger.warning(
                "Targeted final contextualization plain-JSON repair failed "
                "validation for source_id={} targets={}: {}",
                request.source_id,
                list(target_indices),
                exc,
            )

    raise ContextualChunkingModelError(
        "Could not recover targeted final contextualization repair for "
        f"source_id={request.source_id} targets={list(target_indices)}"
    )


def _apply_final_contextualization_replacements(
    *,
    decision: FinalChunkContextDecision,
    repair: FinalChunkContextRepair,
    target_indices: tuple[int, ...],
) -> FinalChunkContextDecision:
    """Splice repaired leaves into the prior decision without touching good leaves."""
    target_set = set(target_indices)
    replacements: dict[int, FinalChunkContextPlan] = {}
    for plan in repair.replacements:
        if plan.chunk_index not in target_set:
            raise ContextualChunkingModelError(
                "Targeted contextualization repair returned an unrequested "
                f"chunk_index={plan.chunk_index}; targets={sorted(target_set)}"
            )
        if plan.chunk_index in replacements:
            raise ContextualChunkingModelError(
                "Targeted contextualization repair duplicated "
                f"chunk_index={plan.chunk_index}"
            )
        replacements[plan.chunk_index] = plan

    if set(replacements) != target_set:
        raise ContextualChunkingModelError(
            "Targeted contextualization repair did not return exactly the bad "
            f"leaves: expected={sorted(target_set)} actual={sorted(replacements)}"
        )

    updated: list[FinalChunkContextPlan] = []
    changed_indices: list[int] = []
    for plan in decision.chunks:
        replacement = replacements.get(plan.chunk_index)
        if replacement is None:
            # Preserve every good leaf object exactly as it was.
            updated.append(plan)
            continue
        updated.append(replacement)
        changed_indices.append(plan.chunk_index)

    if set(changed_indices) != target_set:
        raise ContextualChunkingModelError(
            "Previous contextualization decision is missing one or more targeted "
            f"leaves: expected={sorted(target_set)} changed={sorted(changed_indices)}"
        )

    return FinalChunkContextDecision(chunks=updated)

def call_contextual_chunking_model(
    request: GraphBuildRequest,
    *,
    max_chunk_chars: int = _CONTEXTUAL_CHUNK_MAX_CHARS,
    max_contextualized_chars: int = _CONTEXTUALIZED_TEXT_MAX_CHARS,
) -> list[ContextualSourceChunk]:
    """Recursively segment a source semantically, then contextualize final leaves.

    Python never invents semantic cut points. It validates exact block coverage
    and recursively invokes semantic segmentation only for model-selected chunks
    that remain larger than ``max_chunk_chars``.
    """
    if max_chunk_chars <= 0:
        raise ValueError("max_chunk_chars must be positive")
    if max_contextualized_chars <= 0:
        raise ValueError("max_contextualized_chars must be positive")

    source = request.content
    if not source:
        return []

    if len(source) <= max_chunk_chars:
        return [
            ContextualSourceChunk(
                source_text=source,
                contextualized_text=source,
                start=0,
                end=len(source),
            )
        ]

    blocks = _build_context_source_blocks(source)
    segmentation_model = _get_model(reasoning_effort="low")

    leaves = _segment_range_recursively(
        request=request,
        blocks=blocks,
        active_start_block=0,
        active_end_block=len(blocks) - 1,
        max_chunk_chars=max_chunk_chars,
        recursion_depth=0,
        model=segmentation_model,
    )

    _validate_semantic_leaf_partition(
        source=source,
        blocks=blocks,
        leaves=leaves,
        max_chunk_chars=max_chunk_chars,
    )

    logger.info(
        "Recursive semantic segmentation complete: source_id={} chars={} "
        "blocks={} leaves={} threshold={} leaf_sizes={} leaf_block_ranges={}",
        request.source_id,
        len(source),
        len(blocks),
        len(leaves),
        max_chunk_chars,
        [leaf.end - leaf.start for leaf in leaves],
        [
            (leaf.start_block_index, leaf.end_block_index)
            for leaf in leaves
        ],
    )

    base_messages: list[BaseMessage] = [
        SystemMessage(content=_FINAL_CONTEXTUALIZATION_SYSTEM_PROMPT),
        HumanMessage(
            content=_final_contextualization_request_text(
                request,
                blocks,
                leaves,
                max_contextualized_chars=max_contextualized_chars,
            )
        ),
    ]

    contextualization_model = _get_model(reasoning_effort="low")

    decision = _invoke_final_contextualization_decision(
        model=contextualization_model,
        messages=list(base_messages),
        source_id=request.source_id,
        label="final chunk contextualization",
    )

    for attempt in range(_MAX_CONTEXTUALIZATION_RETRIES + 1):
        try:
            chunks = _materialize_final_contextual_chunks(
                request=request,
                blocks=blocks,
                leaves=leaves,
                decision=decision,
                max_contextualized_chars=max_contextualized_chars,
            )
        except ContextualChunkingModelError as exc:
            logger.warning(
                "Final contextualization structural validation failed for "
                "source_id={} attempt={}/{}: {}",
                request.source_id,
                attempt + 1,
                _MAX_CONTEXTUALIZATION_RETRIES + 1,
                exc,
            )

            if attempt >= _MAX_CONTEXTUALIZATION_RETRIES:
                raise

            # A batch-level structural failure can make the trustworthy target
            # indices unknowable (wrong count, duplicate/missing indices, etc.).
            # Only this protocol-recovery path is allowed to regenerate the batch.
            structural_repair_messages = [
                *base_messages,
                _final_contextualization_structural_repair_message(
                    decision=decision,
                    issue=str(exc),
                ),
            ]
            decision = _invoke_final_contextualization_decision(
                model=contextualization_model,
                messages=structural_repair_messages,
                source_id=request.source_id,
                label="final chunk contextualization structural repair",
            )
            continue

        assessment = _assess_contextual_chunking(
            request=request,
            blocks=blocks,
            chunks=chunks,
        )

        bad_indices = sorted({issue.chunk_index for issue in assessment.issues})
        logger.info(
            "Final contextualization audit: source_id={} attempt={}/{} "
            "complete={} chunks={} issues={} bad_indices={} reason={}",
            request.source_id,
            attempt + 1,
            _MAX_CONTEXTUALIZATION_RETRIES + 1,
            assessment.complete,
            len(chunks),
            len(assessment.issues),
            bad_indices,
            assessment.reason,
        )

        if assessment.complete:
            logger.info(
                "Contextual source preparation complete: source_id={} chars={} "
                "chunks={} primary_sizes={} contextualized_sizes={} "
                "context_counts={}",
                request.source_id,
                len(source),
                len(chunks),
                [chunk.end - chunk.start for chunk in chunks],
                [len(chunk.contextualized_text) for chunk in chunks],
                [len(chunk.context_source_texts) for chunk in chunks],
            )
            return chunks

        if attempt >= _MAX_CONTEXTUALIZATION_RETRIES:
            raise ContextualChunkingModelError(
                "Final contextualization remained semantically unsafe after "
                f"{_MAX_CONTEXTUALIZATION_RETRIES} repair attempt(s) for "
                f"source_id={request.source_id}. Issues="
                f"{[item.model_dump(mode='json') for item in assessment.issues]}; "
                f"Reason={assessment.reason}"
            )

        repair, target_indices = _invoke_final_contextualization_targeted_repair(
            model=contextualization_model,
            request=request,
            blocks=blocks,
            leaves=leaves,
            decision=decision,
            assessment=assessment,
            max_contextualized_chars=max_contextualized_chars,
        )
        decision = _apply_final_contextualization_replacements(
            decision=decision,
            repair=repair,
            target_indices=target_indices,
        )
        logger.warning(
            "Applied monotonic targeted contextualization repair for "
            "source_id={}: repaired_indices={} preserved_indices={}",
            request.source_id,
            list(target_indices),
            [
                index
                for index in range(len(leaves))
                if index not in set(target_indices)
            ],
        )

    raise ContextualChunkingModelError(
        f"Unexpected contextual source preparation state for "
        f"source_id={request.source_id}"
    )


_MAX_DECOMPOSITION_STRUCTURAL_RETRIES = 2


def _invoke_local_decomposition_decision(
    *,
    model: Any,
    messages: list[BaseMessage],
    source_id: str,
    depth: int,
    label: str,
) -> LocalDecompositionDecision:
    """Invoke decomposition with tolerant recovery and strict final validation.

    A malformed structured response is treated as a recoverable model-output
    failure. Raw ``ValidationError`` exceptions must not escape this boundary.
    """
    structured_model = model.with_structured_output(
        LocalDecompositionDecision,
        method="function_calling",
        include_raw=True,
    )

    structured_exception: Exception | None = None

    try:
        result = structured_model.invoke(messages)
    except Exception as exc:
        structured_exception = exc
        logger.warning(
            "{} structured call failed for source_id={} depth={}: {}. "
            "Retrying once as plain JSON.",
            label,
            source_id,
            depth,
            exc,
        )
        result = None

    if isinstance(result, dict):
        parsed = result.get("parsed")
        if parsed is not None:
            decision = _try_validate_local_decomposition(
                parsed,
                source_id=source_id,
                depth=depth,
                label=label,
            )
            if decision is not None:
                return decision

        raw_message = result.get("raw")
        parsing_error = result.get("parsing_error")
        raw_args = _extract_structured_args(raw_message)

        if raw_args is not None:
            decision = _try_validate_local_decomposition(
                raw_args,
                source_id=source_id,
                depth=depth,
                label=label,
            )
            if decision is not None:
                return decision

        logger.warning(
            "{} structured output could not be validated for source_id={} "
            "depth={}: {}. Retrying once as plain JSON.",
            label,
            source_id,
            depth,
            parsing_error,
        )

    raw_args = _plain_json_retry(
        model=model,
        messages=messages,
        schema=LocalDecompositionDecision,
        label=label,
    )

    if raw_args is not None:
        decision = _try_validate_local_decomposition(
            raw_args,
            source_id=source_id,
            depth=depth,
            label=f"{label} plain-JSON retry",
        )
        if decision is not None:
            return decision

    error = PromptDecompositionModelError(
        f"Could not recover valid LocalDecompositionDecision for "
        f"source_id={source_id} depth={depth}"
    )
    if structured_exception is not None:
        raise error from structured_exception
    raise error


def _decomposition_grounding_issues(
    *,
    request: GraphBuildRequest,
    decision: LocalDecompositionDecision,
) -> list[str]:
    """Return deterministic provenance imprecision diagnostics."""
    if decision.kind != "composite":
        return []

    issues: list[str] = []
    parent = request.content

    for index, child in enumerate(decision.children):
        excerpt = child.source_text.strip()
        if not excerpt:
            issues.append(f"child[{index}] has empty source_text")
        elif excerpt not in parent:
            issues.append(
                f"child[{index}] source_text is not a verbatim substring of parent: "
                f"{excerpt!r}"
            )

    for index, relation in enumerate(decision.local_relations):
        excerpt = relation.evidence_text.strip()
        if not excerpt:
            issues.append(f"local_relations[{index}] has empty evidence_text")
        elif excerpt not in parent:
            issues.append(
                f"local_relations[{index}] evidence_text is not a verbatim "
                f"substring of parent: {excerpt!r}"
            )

    return issues


def _coverage_request_text(
    request: GraphBuildRequest,
    decision: LocalDecompositionDecision,
) -> str:
    """Serialize the source and proposed composite decision for coverage audit."""
    decision_payload = decision.model_dump(
        mode="json",
        exclude_none=True,
    )
    # Direct-child payloads are provider drift. Retrieval payload generation is a
    # dedicated post-tree pass, so child payloads are excluded from semantic audit.
    for child in decision_payload.get("children", []):
        if isinstance(child, dict):
            child.pop("proposition", None)
    return (
        f"SOURCE_TYPE: {request.source_type.value}\n"
        f"SOURCE_ID: {request.source_id}\n\n"
        "SOURCE_BEGIN\n"
        f"{request.content}\n"
        "SOURCE_END\n\n"
        "PROPOSED_DECISION_BEGIN\n"
        f"{json.dumps(decision_payload, ensure_ascii=False, indent=2)}\n"
        "PROPOSED_DECISION_END"
    )


def _try_validate_decomposition_audit(
    payload: Any,
    *,
    source_id: str,
) -> DecompositionAuditResult | None:
    if isinstance(payload, DecompositionAuditResult):
        return payload
    try:
        return DecompositionAuditResult.model_validate(payload)
    except ValidationError as exc:
        logger.warning(
            "Decomposition audit output failed validation for source_id={}: {}",
            source_id,
            exc,
        )
        return None


def _audit_and_correct_decomposition(
    *,
    request: GraphBuildRequest,
    decision: LocalDecompositionDecision,
) -> DecompositionAuditResult:
    """Audit once; return PASS or the complete corrected composite decision.

    Provider/protocol recovery may retry malformed structured output, but there
    is no separate semantic repair model and no semantic re-audit of a returned
    correction.
    """
    messages: list[BaseMessage] = [
        SystemMessage(content=_DECOMPOSITION_AUDIT_SYSTEM_PROMPT),
        HumanMessage(content=_coverage_request_text(request, decision)),
    ]
    model = _get_model(reasoning_effort="medium")

    for protocol_attempt in range(2):
        structured_model = model.with_structured_output(
            DecompositionAuditResult,
            method="function_calling",
            include_raw=True,
        )
        try:
            result = structured_model.invoke(messages)
        except Exception as exc:
            logger.warning(
                "Decomposition audit structured call failed for source_id={} "
                "protocol_attempt={}/2: {}",
                request.source_id,
                protocol_attempt + 1,
                exc,
            )
            result = None

        if isinstance(result, dict):
            for payload in (
                result.get("parsed"),
                _extract_structured_args(result.get("raw")),
            ):
                if payload is None:
                    continue
                audit = _try_validate_decomposition_audit(
                    payload,
                    source_id=request.source_id,
                )
                if audit is not None:
                    return audit

        raw_args = _plain_json_retry(
            model=model,
            messages=messages,
            schema=DecompositionAuditResult,
            label="decomposition audit/correction",
        )
        if raw_args is not None:
            audit = _try_validate_decomposition_audit(
                raw_args,
                source_id=request.source_id,
            )
            if audit is not None:
                return audit

        logger.warning(
            "Decomposition auditor protocol failed for source_id={} "
            "protocol_attempt={}/2; retrying the same audit without changing "
            "the decomposition candidate",
            request.source_id,
            protocol_attempt + 1,
        )

    raise PromptDecompositionModelError(
        "Could not recover DecompositionAuditResult for "
        f"source_id={request.source_id}"
    )


def _atomic_classification_request_text(
    request: GraphBuildRequest,
) -> str:
    semantic_role = request.metadata.get("semantic_role")
    return (
        f"SOURCE_TYPE: {request.source_type.value}\n"
        f"SOURCE_ID: {request.source_id}\n"
        f"SEMANTIC_ROLE: {semantic_role or 'unspecified'}\n\n"
        "SOURCE_BEGIN\n"
        f"{request.content}\n"
        "SOURCE_END"
    )


def _try_validate_atomic_classification_assessment(
    payload: Any,
    *,
    source_id: str,
) -> AtomicClassificationAssessment | None:
    if isinstance(payload, AtomicClassificationAssessment):
        return payload
    try:
        return AtomicClassificationAssessment.model_validate(payload)
    except ValidationError as exc:
        logger.warning(
            "Atomic classification audit output failed validation for source_id={}: {}",
            source_id,
            exc,
        )
        return None


def _assess_atomic_classification(
    *,
    request: GraphBuildRequest,
) -> AtomicClassificationAssessment:
    """Audit only semantic atomicity during recursive tree construction."""
    messages: list[BaseMessage] = [
        SystemMessage(content=_ATOMIC_CLASSIFICATION_AUDIT_SYSTEM_PROMPT),
        HumanMessage(content=_atomic_classification_request_text(request)),
    ]
    model = _get_model(reasoning_effort="medium")

    for protocol_attempt in range(2):
        structured_model = model.with_structured_output(
            AtomicClassificationAssessment,
            method="function_calling",
            include_raw=True,
        )
        try:
            result = structured_model.invoke(messages)
        except Exception as exc:
            logger.warning(
                "Atomic classification audit structured call failed for source_id={} "
                "protocol_attempt={}/2: {}",
                request.source_id,
                protocol_attempt + 1,
                exc,
            )
            result = None

        if isinstance(result, dict):
            candidates = [
                result.get("parsed"),
                _extract_structured_args(result.get("raw")),
            ]
            for payload in candidates:
                if payload is None:
                    continue
                assessment = _try_validate_atomic_classification_assessment(
                    payload,
                    source_id=request.source_id,
                )
                if assessment is not None:
                    return assessment

        raw_args = _plain_json_retry(
            model=model,
            messages=messages,
            schema=AtomicClassificationAssessment,
            label="atomic classification audit",
        )
        if raw_args is not None:
            assessment = _try_validate_atomic_classification_assessment(
                raw_args,
                source_id=request.source_id,
            )
            if assessment is not None:
                return assessment

        logger.warning(
            "Atomic classification auditor protocol failed for source_id={} "
            "protocol_attempt={}/2; retrying the same audit",
            request.source_id,
            protocol_attempt + 1,
        )

    raise PromptDecompositionModelError(
        "Could not recover AtomicClassificationAssessment for "
        f"source_id={request.source_id}"
    )


def _normalized_semantic_child_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


_CONNECTIVE_ONLY_SEMANTIC_CHILDREN = frozenset(
    {
        "before",
        "after",
        "first",
        "then",
        "if",
        "when",
        "unless",
        "otherwise",
        "requires",
        "require",
        "and",
        "or",
    }
)


def _atomic_classification_feedback_message(
    assessment: AtomicClassificationAssessment,
) -> HumanMessage:
    return HumanMessage(
        content=(
            "ATOMIC_CLASSIFICATION_AUDIT_FAILED\n"
            "The source was classified atomic, but it is not one valid indivisible "
            "semantic operand in its inherited role. Re-decompose the SAME source "
            "only when it contains multiple meaningful operands. Do not manufacture "
            "standalone connective children such as Before/After/If/Then/Unless. "
            "If relational wording merely connects operands, return the clean "
            "operands and leave the relation to the later normalization pass. This "
            "is a semantic-structure repair, not a payload or AST task.\n\n"
            "AUDIT_REASON:\n" + assessment.reason
        )
    )


def _logic_request_text(
    request: GraphBuildRequest,
    propositions: list[LogicPropositionCandidate],
) -> str:
    payload = [item.model_dump(mode="json") for item in propositions]
    return (
        f"SOURCE_TYPE: {request.source_type.value}\n"
        f"SOURCE_ID: {request.source_id}\n\n"
        "SOURCE_BEGIN\n"
        f"{request.content}\n"
        "SOURCE_END\n\n"
        "AVAILABLE_PROPOSITIONS_BEGIN\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
        "AVAILABLE_PROPOSITIONS_END"
    )


def _validate_local_logic_decision(
    *,
    decision: LocalLogicDecision,
    proposition_count: int,
) -> None:
    for slot in decision.slots:
        if (
            slot.proposition_index is not None
            and slot.proposition_index >= proposition_count
        ):
            raise LogicalStructureModelError(
                f"Logical slot {slot.slot_id} references proposition_index="
                f"{slot.proposition_index} outside proposition_count="
                f"{proposition_count}"
            )


def _normalize_local_logic_payload(payload: Any) -> Any:
    """Remove harmless provider confidence metadata from nested logic output."""
    if isinstance(payload, list):
        return [_normalize_local_logic_payload(item) for item in payload]
    if not isinstance(payload, dict):
        return payload

    return {
        key: _normalize_local_logic_payload(value)
        for key, value in payload.items()
        if key != "confidence"
    }


def _try_validate_logic_normalization(
    payload: Any,
    *,
    source_id: str,
    label: str,
) -> LogicNormalizationDecision | None:
    if isinstance(payload, LogicNormalizationDecision):
        return payload

    normalized = _normalize_local_logic_payload(payload)
    try:
        return LogicNormalizationDecision.model_validate(normalized)
    except ValidationError as exc:
        logger.warning(
            "{} output failed LogicNormalizationDecision validation for "
            "source_id={}: {}",
            label,
            source_id,
            exc,
        )
        return None


def _invoke_logic_normalization(
    *,
    model: Any,
    messages: list[BaseMessage],
    source_id: str,
    label: str,
) -> LogicNormalizationDecision:
    """Recover one narrow natural-language -> canonical-logic normalization."""
    structured_model = model.with_structured_output(
        LogicNormalizationDecision,
        method="function_calling",
        include_raw=True,
    )
    structured_exception: Exception | None = None

    try:
        result = structured_model.invoke(messages)
    except Exception as exc:
        structured_exception = exc
        logger.warning(
            "{} structured call failed for source_id={}: {}. Retrying once as "
            "plain JSON.",
            label,
            source_id,
            exc,
        )
        result = None

    if isinstance(result, dict):
        parsed = result.get("parsed")
        if parsed is not None:
            decision = _try_validate_logic_normalization(
                parsed,
                source_id=source_id,
                label=f"{label} parsed",
            )
            if decision is not None:
                return decision

        raw_args = _extract_structured_args(result.get("raw"))
        if raw_args is not None:
            decision = _try_validate_logic_normalization(
                raw_args,
                source_id=source_id,
                label=f"{label} raw args",
            )
            if decision is not None:
                return decision

    raw_args = _plain_json_retry(
        model=model,
        messages=messages,
        schema=LogicNormalizationDecision,
        label=label,
    )
    if raw_args is not None:
        decision = _try_validate_logic_normalization(
            raw_args,
            source_id=source_id,
            label=f"{label} plain-JSON retry",
        )
        if decision is not None:
            return decision

    error = LogicalStructureModelError(
        f"Could not recover LogicNormalizationDecision for source_id={source_id}"
    )
    if structured_exception is not None:
        raise error from structured_exception
    raise error


def _compile_logic_normalization(
    normalized: LogicNormalizationDecision,
    *,
    proposition_count: int,
) -> CompiledStructureDecision:
    """Route normalized structure to graph relations vs sparse compound logic.

    Python owns the representation choice. Source-explicit binary semantic
    relations are kept as graph relations. Boolean rules are distributed only by
    truth-preserving equivalences:

    - (A OR B) -> C  == A -> C AND B -> C
    - A -> (B AND C) == A -> B AND A -> C
    - asserted (A AND B) == assert A AND assert B

    A distributed literal implication becomes a graph IMPLIES relation only when
    both signed literals are already represented by exact semantic propositions.
    Otherwise it stays in the logic layer as a fallback. Irreducible compound
    structure always stays in the logic layer.
    """
    expression_by_id = {
        item.expression_id: item
        for item in normalized.expressions
    }
    slot_by_id = {slot.slot_id: slot for slot in normalized.slots}

    normalized_relations: list[LocalNormalizedRelation] = []
    for relation in normalized.relations:
        if relation.source_proposition_index >= proposition_count:
            raise LogicalStructureModelError(
                "Normalized relation references source_proposition_index="
                f"{relation.source_proposition_index} outside proposition_count="
                f"{proposition_count}"
            )
        if relation.target_proposition_index >= proposition_count:
            raise LogicalStructureModelError(
                "Normalized relation references target_proposition_index="
                f"{relation.target_proposition_index} outside proposition_count="
                f"{proposition_count}"
            )
        normalized_relations.append(relation)

    def expression_for(ref: LocalLogicOperand) -> LocalLogicNode | None:
        if ref.expression_id is None:
            return None
        return expression_by_id.get(ref.expression_id)

    def collapse_signed_literal(ref: LocalLogicOperand) -> LocalLogicOperand:
        """Collapse NOT(literal) and double-negation to a signed slot operand."""
        if ref.slot_id is not None:
            return ref

        node = expression_for(ref)
        if (
            node is None
            or node.operator != LogicalOperator.NOT
            or len(node.operands) != 1
        ):
            return ref

        inner = collapse_signed_literal(node.operands[0])
        if inner.slot_id is None:
            return ref
        return LocalLogicOperand(
            slot_id=inner.slot_id,
            value=not inner.value,
        )

    def split_top_level(
        ref: LocalLogicOperand,
        *,
        operator: LogicalOperator,
    ) -> list[LocalLogicOperand]:
        ref = collapse_signed_literal(ref)
        if ref.slot_id is not None:
            return [ref]
        node = expression_for(ref)
        if node is None or node.operator != operator:
            return [ref]

        result: list[LocalLogicOperand] = []
        for operand in node.operands:
            result.extend(split_top_level(operand, operator=operator))
        return result

    def bound_semantic_index(ref: LocalLogicOperand) -> int | None:
        """Return the semantic proposition that exactly expresses this literal."""
        ref = collapse_signed_literal(ref)
        if ref.slot_id is None:
            return None
        slot = slot_by_id.get(ref.slot_id)
        if slot is None or slot.proposition_index is None:
            return None
        if slot.proposition_index >= proposition_count:
            raise LogicalStructureModelError(
                f"Logical slot {slot.slot_id} references proposition_index="
                f"{slot.proposition_index} outside proposition_count="
                f"{proposition_count}"
            )
        # A bound semantic node represents the literal only when its asserted
        # polarity matches the operand's signed truth value.
        if bool(slot.proposition_value) != bool(ref.value):
            return None
        return slot.proposition_index

    compiled_assertions: list[LocalLogicAssertion] = []
    compiled_rules: list[LocalLogicRule] = []

    for clause in normalized.clauses:
        if clause.kind == "assertion":
            assert clause.root is not None
            for root in split_top_level(
                clause.root,
                operator=LogicalOperator.AND,
            ):
                root = collapse_signed_literal(root)
                # An independently represented semantic literal is already an
                # asserted fact in the semantic graph; do not duplicate it in
                # the logic layer. Compound OR/cardinality/scoped negation is not
                # suppressible and remains below.
                if bound_semantic_index(root) is not None:
                    continue
                compiled_assertions.append(
                    LocalLogicAssertion(
                        root=root,
                        evidence_text=clause.evidence_text,
                    )
                )
            continue

        assert clause.condition is not None
        assert clause.effect is not None
        conditions = split_top_level(
            clause.condition,
            operator=LogicalOperator.OR,
        )
        effects = split_top_level(
            clause.effect,
            operator=LogicalOperator.AND,
        )
        for condition in conditions:
            condition = collapse_signed_literal(condition)
            for effect in effects:
                effect = collapse_signed_literal(effect)
                source_index = bound_semantic_index(condition)
                target_index = bound_semantic_index(effect)
                if (
                    source_index is not None
                    and target_index is not None
                    and source_index != target_index
                ):
                    normalized_relations.append(
                        LocalNormalizedRelation(
                            source_proposition_index=source_index,
                            target_proposition_index=target_index,
                            relation=RelationType.IMPLIES,
                            evidence_text=clause.evidence_text,
                        )
                    )
                    continue

                compiled_rules.append(
                    LocalLogicRule(
                        condition=condition,
                        effect=effect,
                        evidence_text=clause.evidence_text,
                    )
                )

    referenced_expression_ids: set[int] = set()
    referenced_slot_ids: set[int] = set()

    def collect(ref: LocalLogicOperand) -> None:
        if ref.slot_id is not None:
            referenced_slot_ids.add(ref.slot_id)
            return
        if (
            ref.expression_id is None
            or ref.expression_id in referenced_expression_ids
        ):
            return
        node = expression_by_id.get(ref.expression_id)
        if node is None:
            raise LogicalStructureModelError(
                f"Compiled logic references unknown expression_id={ref.expression_id}"
            )
        referenced_expression_ids.add(ref.expression_id)
        for operand in node.operands:
            collect(operand)

    for assertion in compiled_assertions:
        collect(assertion.root)
    for rule in compiled_rules:
        collect(rule.condition)
        collect(rule.effect)

    slots = [
        slot
        for slot in normalized.slots
        if slot.slot_id in referenced_slot_ids
    ]
    expressions = [
        expression
        for expression in normalized.expressions
        if expression.expression_id in referenced_expression_ids
    ]

    logic = LocalLogicDecision(
        slots=slots,
        expressions=expressions,
        assertions=compiled_assertions,
        rules=compiled_rules,
    )

    deduped_relations: list[LocalNormalizedRelation] = []
    relation_by_key: dict[
        tuple[int, int, RelationType],
        LocalNormalizedRelation,
    ] = {}
    for relation in normalized_relations:
        key = (
            relation.source_proposition_index,
            relation.target_proposition_index,
            relation.relation,
        )
        prior = relation_by_key.get(key)
        if prior is None:
            relation_by_key[key] = relation
            deduped_relations.append(relation)
            continue
        if relation.confidence > prior.confidence:
            replacement = relation
            relation_by_key[key] = replacement
            deduped_relations[deduped_relations.index(prior)] = replacement

    return CompiledStructureDecision(
        logic=logic,
        relations=tuple(deduped_relations),
    )


def _logic_audit_text(
    request: GraphBuildRequest,
    propositions: list[LogicPropositionCandidate],
    relations: tuple[LocalNormalizedRelation, ...],
    logic: LocalLogicDecision,
) -> str:
    return (
        f"{_logic_request_text(request, propositions)}\n\n"
        "NORMALIZED_SIMPLE_RELATIONS_BEGIN\n"
        f"{json.dumps([item.model_dump(mode='json') for item in relations], ensure_ascii=False, indent=2)}\n"
        "NORMALIZED_SIMPLE_RELATIONS_END\n\n"
        "PROPOSED_COMPOUND_LOGIC_BEGIN\n"
        f"{json.dumps(logic.model_dump(mode='json'), ensure_ascii=False, indent=2)}\n"
        "PROPOSED_COMPOUND_LOGIC_END"
    )


def _try_validate_logical_structure_assessment(
    payload: Any,
    *,
    source_id: str,
) -> LogicalStructureAssessment | None:
    if isinstance(payload, LogicalStructureAssessment):
        return payload
    try:
        return LogicalStructureAssessment.model_validate(payload)
    except ValidationError as exc:
        logger.warning(
            "Logic structure audit output failed validation for source_id={}: {}",
            source_id,
            exc,
        )
        return None


def _assess_logical_structure(
    *,
    request: GraphBuildRequest,
    propositions: list[LogicPropositionCandidate],
    relations: tuple[LocalNormalizedRelation, ...],
    logic: LocalLogicDecision,
) -> LogicalStructureAssessment:
    """Audit only genuinely compound persisted logic."""
    messages: list[BaseMessage] = [
        SystemMessage(content=_LOGICAL_STRUCTURE_AUDIT_SYSTEM_PROMPT),
        HumanMessage(
            content=_logic_audit_text(
                request,
                propositions,
                relations,
                logic,
            )
        ),
    ]
    model = _get_model(reasoning_effort="medium")

    for protocol_attempt in range(2):
        structured_model = model.with_structured_output(
            LogicalStructureAssessment,
            method="function_calling",
            include_raw=True,
        )
        try:
            result = structured_model.invoke(messages)
        except Exception as exc:
            logger.warning(
                "Logic structure audit structured call failed for source_id={} "
                "protocol_attempt={}/2: {}",
                request.source_id,
                protocol_attempt + 1,
                exc,
            )
            result = None

        if isinstance(result, dict):
            parsed = result.get("parsed")
            if parsed is not None:
                assessment = _try_validate_logical_structure_assessment(
                    parsed,
                    source_id=request.source_id,
                )
                if assessment is not None:
                    return assessment

            raw_args = _extract_structured_args(result.get("raw"))
            if raw_args is not None:
                assessment = _try_validate_logical_structure_assessment(
                    raw_args,
                    source_id=request.source_id,
                )
                if assessment is not None:
                    return assessment

        raw_args = _plain_json_retry(
            model=model,
            messages=messages,
            schema=LogicalStructureAssessment,
            label="compound logic structure audit",
        )
        if raw_args is not None:
            assessment = _try_validate_logical_structure_assessment(
                raw_args,
                source_id=request.source_id,
            )
            if assessment is not None:
                return assessment

        logger.warning(
            "Compound logic auditor protocol failed for source_id={} "
            "protocol_attempt={}/2; retrying the same audit without changing "
            "the logic candidate",
            request.source_id,
            protocol_attempt + 1,
        )

    raise LogicalStructureModelError(
        f"Could not recover LogicalStructureAssessment for source_id={request.source_id}"
    )


def _logic_normalization_repair_message(
    *,
    assessment: LogicalStructureAssessment,
) -> HumanMessage:
    missing = assessment.missing_logic or ["none reported"]
    unsupported = assessment.unsupported_logic or ["none reported"]
    return HumanMessage(
        content=(
            "COMPOUND_LOGIC_AUDIT_FAILED\n"
            "Re-normalize the same source-explicit relational/logical operators "
            "and operands. Preserve correct binary semantic relations, but do not "
            "choose a persisted AST/simple representation for Boolean clauses; "
            "Python will route them deterministically. Do not create semantic "
            "nodes or infer facts.\n\n"
            "MISSING_LOGIC:\n- " + "\n- ".join(missing)
            + "\n\nUNSUPPORTED_LOGIC:\n- " + "\n- ".join(unsupported)
            + "\n\nAUDIT_REASON:\n" + assessment.reason
        )
    )


def call_logic_structure_model(
    request: GraphBuildRequest,
    propositions: list[LogicPropositionCandidate],
) -> CompiledStructureDecision:
    """Selectively normalize source relations/logic, then route deterministically."""
    empty = CompiledStructureDecision(logic=LocalLogicDecision())
    if (
        _LOGIC_CUE_RE.search(request.content) is None
        and not bool(request.metadata.get("logic_semantic_signal", False))
    ):
        return empty

    base_messages: list[BaseMessage] = [
        SystemMessage(content=_LOGIC_NORMALIZATION_SYSTEM_PROMPT),
        HumanMessage(content=_logic_request_text(request, propositions)),
    ]
    model = _get_model(reasoning_effort="low")
    generation_messages = list(base_messages)

    best_compiled = empty
    for attempt in range(_MAX_LOGICAL_STRUCTURE_RETRIES + 1):
        try:
            normalized = _invoke_logic_normalization(
                model=model,
                messages=generation_messages,
                source_id=request.source_id,
                label="relation/logic normalization",
            )
            compiled = _compile_logic_normalization(
                normalized,
                proposition_count=len(propositions),
            )
            _validate_local_logic_decision(
                decision=compiled.logic,
                proposition_count=len(propositions),
            )
            best_compiled = compiled
        except (LogicalStructureModelError, ValidationError, ValueError) as exc:
            if bool(request.metadata.get("logic_fail_soft", False)):
                logger.warning(
                    "Relation/logic normalization failed softly for source_id={}: {}",
                    request.source_id,
                    exc,
                )
                return best_compiled
            raise

        if memory_graph_trace_enabled():
            logger.info(
                "Structure normalization compiled: source_id={} attempt={}/{} "
                "relations={} logic_slots={} assertions={} rules={} expressions={}",
                request.source_id,
                attempt + 1,
                _MAX_LOGICAL_STRUCTURE_RETRIES + 1,
                len(compiled.relations),
                len(compiled.logic.slots),
                len(compiled.logic.assertions),
                len(compiled.logic.rules),
                len(compiled.logic.expressions),
            )

        # Graph-owned binary relations and literal-level fallback logic are
        # accepted after deterministic validation. Only surviving compound ASTs
        # incur the extra semantic logic audit.
        if not compiled.logic.expressions:
            return compiled

        try:
            assessment = _assess_logical_structure(
                request=request,
                propositions=propositions,
                relations=compiled.relations,
                logic=compiled.logic,
            )
        except LogicalStructureModelError as exc:
            if bool(request.metadata.get("logic_fail_soft", False)):
                logger.warning(
                    "Compound logic audit failed softly; preserving compiled "
                    "structure for source_id={}: {}",
                    request.source_id,
                    exc,
                )
                return compiled
            raise

        if memory_graph_trace_enabled():
            logger.info(
                "Compound logic structure audit: source_id={} attempt={}/{} complete={} "
                "relations={} slots={} assertions={} rules={} expressions={} missing={} "
                "unsupported={} reason={}",
                request.source_id,
                attempt + 1,
                _MAX_LOGICAL_STRUCTURE_RETRIES + 1,
                assessment.complete,
                len(compiled.relations),
                len(compiled.logic.slots),
                len(compiled.logic.assertions),
                len(compiled.logic.rules),
                len(compiled.logic.expressions),
                len(assessment.missing_logic),
                len(assessment.unsupported_logic),
                assessment.reason,
            )
        if assessment.complete:
            return compiled

        if attempt >= _MAX_LOGICAL_STRUCTURE_RETRIES:
            if bool(request.metadata.get("logic_fail_soft", False)):
                logger.warning(
                    "Compound logic remained incomplete after repair; preserving "
                    "best compiled structure: source_id={} missing={} unsupported={} "
                    "reason={}",
                    request.source_id,
                    assessment.missing_logic,
                    assessment.unsupported_logic,
                    assessment.reason,
                )
                return compiled
            raise LogicalStructureModelError(
                "Compound logical structure remained incomplete after repair for "
                f"source_id={request.source_id}. Missing={assessment.missing_logic}; "
                f"Unsupported={assessment.unsupported_logic}; Reason={assessment.reason}"
            )

        generation_messages = [
            *base_messages,
            _logic_normalization_repair_message(assessment=assessment),
        ]

    return best_compiled


def call_logic_slot_binding_model(
    request: LogicSlotBindingRequest,
) -> LogicSlotBindingResponse:
    """Match one semantic node to logic slots using the active local backend.

    The active backend is ``cross-encoder/nli-deberta-v3-small``. The prior 120B
    implementation is preserved verbatim in ``logic_slot_binding_llm_legacy.py``
    and can be restored with a one-line backend-import change near the top of
    this module.
    """
    if not request.candidates:
        return LogicSlotBindingResponse()

    try:
        response = _call_logic_slot_binding_backend(request)
    except Exception as exc:
        raise LogicSlotBindingModelError(
            "Local logic-slot matcher failed for "
            f"node_id={request.node_id}: {exc}"
        ) from exc

    allowed = {candidate.slot_id for candidate in request.candidates}
    filtered = [binding for binding in response.bindings if binding.slot_id in allowed]
    if len(filtered) != len(response.bindings):
        logger.warning(
            "Logic-slot matcher returned unknown slot IDs for node_id={}; dropping them",
            request.node_id,
        )

    filtered_response = LogicSlotBindingResponse(bindings=filtered)
    _capture_logic_slot_binding_dataset(request, filtered_response)
    return filtered_response



def _try_validate_chunk_semantic_audit(
    payload: Any,
    *,
    source_id: str,
    chunk_index: int,
) -> ChunkSemanticAuditResult | None:
    if isinstance(payload, ChunkSemanticAuditResult):
        return payload
    try:
        return ChunkSemanticAuditResult.model_validate(payload)
    except ValidationError as exc:
        logger.warning(
            "Chunk semantic audit output failed validation for source_id={} chunk={}: {}",
            source_id,
            chunk_index,
            exc,
        )
        return None


def audit_completed_chunk_model(
    *,
    request: GraphBuildRequest,
    chunk_index: int,
    primary_source_text: str,
    context_source_texts: list[str],
    contextualized_input: str,
    chunk_structure: dict[str, Any],
) -> ChunkSemanticAuditResult:
    """Run one semantic audit after the complete chunk subtree has been built.

    This is deliberately one-shot. A malformed provider response gets one plain
    JSON protocol retry; a semantic FAIL is returned directly and is never
    automatically repaired or re-audited.
    """
    body = (
        f"SOURCE_TYPE: {request.source_type.value}\n"
        f"SOURCE_ID: {request.source_id}\n"
        f"CHUNK_INDEX: {chunk_index}\n\n"
        "PRIMARY_SOURCE_BEGIN\n"
        f"{primary_source_text}\n"
        "PRIMARY_SOURCE_END\n\n"
        "CONTEXT_SOURCE_TEXTS_BEGIN\n"
        f"{json.dumps(context_source_texts, ensure_ascii=False, indent=2)}\n"
        "CONTEXT_SOURCE_TEXTS_END\n\n"
        "CONTEXTUALIZED_INPUT_BEGIN\n"
        f"{contextualized_input}\n"
        "CONTEXTUALIZED_INPUT_END\n\n"
        "CHUNK_STRUCTURE_BEGIN\n"
        f"{json.dumps(chunk_structure, ensure_ascii=False, indent=2)}\n"
        "CHUNK_STRUCTURE_END"
    )
    messages: list[BaseMessage] = [
        SystemMessage(content=_CHUNK_SEMANTIC_AUDIT_SYSTEM_PROMPT),
        HumanMessage(content=body),
    ]
    model = _get_model(reasoning_effort="medium")
    structured_model = model.with_structured_output(
        ChunkSemanticAuditResult,
        method="function_calling",
        include_raw=True,
    )
    try:
        result = structured_model.invoke(messages)
    except Exception as exc:
        logger.warning(
            "Chunk semantic audit structured call failed for source_id={} chunk={}: {}",
            request.source_id,
            chunk_index,
            exc,
        )
        result = None

    if isinstance(result, dict):
        for payload in (
            result.get("parsed"),
            _extract_structured_args(result.get("raw")),
        ):
            if payload is None:
                continue
            audit = _try_validate_chunk_semantic_audit(
                payload,
                source_id=request.source_id,
                chunk_index=chunk_index,
            )
            if audit is not None:
                return audit

    raw_args = _plain_json_retry(
        model=model,
        messages=messages,
        schema=ChunkSemanticAuditResult,
        label="completed chunk semantic audit",
    )
    if raw_args is not None:
        audit = _try_validate_chunk_semantic_audit(
            raw_args,
            source_id=request.source_id,
            chunk_index=chunk_index,
        )
        if audit is not None:
            return audit

    raise PromptDecompositionModelError(
        "Could not recover ChunkSemanticAuditResult for "
        f"source_id={request.source_id} chunk={chunk_index}"
    )


def call_prompt_decomposition_model(
    request: GraphBuildRequest,
) -> LocalDecompositionDecision:
    """Compatibility entrypoint for the active Stanza + DeDisCo pipeline.

    The previous parser-guided GPT-5-nano implementation is preserved in
    ``model_wrapper_parser_guided_gpt5_nano_legacy.py``.  Keep this wrapper so
    external callers that historically imported decomposition from
    ``model_wrapper`` automatically use the new local pipeline.
    """
    from .decomposition_stanza_dedisco import (
        call_prompt_decomposition_model as call_stanza_dedisco_decomposition,
    )

    return call_stanza_dedisco_decomposition(request)


_SYMMETRIC_RELATIONS = {
    RelationType.RELATED_TO,
    RelationType.EQUIVALENT_TO,
    RelationType.COREFERS_WITH,
    RelationType.SAME_ENTITY,
    RelationType.SAME_EVENT,
    RelationType.CONTRADICTS,
}


def _relation_request_text(request: RelationBuildRequest) -> str:
    """Serialize one local relation-classification neighborhood."""
    anchor = {
        "node_id": request.anchor_node_id,
        "content": request.anchor_content,
        "routing_text": request.anchor_routing_text,
        "context_paths": request.anchor_context_paths,
        "proposition": (
            request.anchor_proposition.model_dump(
                mode="json",
                exclude_none=True,
            )
            if request.anchor_proposition is not None
            else None
        ),
    }

    candidates = [
        {
            "node_id": candidate.node_id,
            "content": candidate.content,
            "routing_text": candidate.routing_text,
            "context_paths": candidate.context_paths,
            "proposition": (
                candidate.proposition.model_dump(
                    mode="json",
                    exclude_none=True,
                )
                if candidate.proposition is not None
                else None
            ),
        }
        for candidate in request.candidates
    ]

    return json.dumps(
        {
            "anchor": anchor,
            "candidates": candidates,
        },
        ensure_ascii=False,
        indent=2,
    )


def _normalize_relation_response(
    response: RelationBuildResponse,
    request: RelationBuildRequest,
) -> RelationBuildResponse:
    """Apply deterministic constraints to model-produced relation decisions."""
    allowed_ids = {
        candidate.node_id
        for candidate in request.candidates
    }

    normalized: list[RelationDecision] = []
    seen: set[tuple[str, RelationType, RelationDirection]] = set()

    for decision in response.relations:
        if decision.other_node_id not in allowed_ids:
            logger.warning(
                "Ignoring relation to unknown candidate node_id={} "
                "for anchor_node_id={}",
                decision.other_node_id,
                request.anchor_node_id,
            )
            continue

        if decision.relation in {
            RelationType.DECOMPOSES_INTO,
            RelationType.IMPLIES,
        }:
            logger.warning(
                "Ignoring relation={} reserved from broad relation inference "
                "for anchor_node_id={} candidate_node_id={}",
                decision.relation.value,
                request.anchor_node_id,
                decision.other_node_id,
            )
            continue

        direction = decision.direction

        if decision.relation in _SYMMETRIC_RELATIONS:
            direction = RelationDirection.SYMMETRIC
        elif direction == RelationDirection.SYMMETRIC:
            logger.warning(
                "Ignoring directional relation={} returned as symmetric "
                "for anchor_node_id={} candidate_node_id={}",
                decision.relation.value,
                request.anchor_node_id,
                decision.other_node_id,
            )
            continue

        key = (
            decision.other_node_id,
            decision.relation,
            direction,
        )
        if key in seen:
            continue
        seen.add(key)

        normalized.append(
            decision.model_copy(
                update={"direction": direction}
            )
        )

    return RelationBuildResponse(relations=normalized)


def call_relation_model(
    request: RelationBuildRequest,
) -> RelationBuildResponse:
    """Classify lateral relations from one anchor atom to supplied candidates.

    Candidate retrieval and graph mutation are intentionally owned by other
    layers. This function only performs local semantic relation classification.
    """
    if not request.candidates:
        return RelationBuildResponse(relations=[])

    messages: list[BaseMessage] = [
        SystemMessage(content=_RELATION_SYSTEM_PROMPT),
        HumanMessage(content=_relation_request_text(request)),
    ]

    model = _get_model(reasoning_effort="low")
    structured_model = model.with_structured_output(
        RelationBuildResponse,
        method="function_calling",
        include_raw=True,
    )

    try:
        result = structured_model.invoke(messages)
    except Exception as exc:
        logger.warning(
            "Relation structured call failed for anchor_node_id={}: {}. "
            "Retrying once as plain JSON.",
            request.anchor_node_id,
            exc,
        )
        raw_args = _plain_json_retry(
            model=model,
            messages=messages,
            schema=RelationBuildResponse,
            label="relation classification",
        )
        if raw_args is None:
            raise RelationModelError(
                "Could not recover relation classification for "
                f"anchor_node_id={request.anchor_node_id}"
            ) from exc

        response = RelationBuildResponse.model_validate(raw_args)
        return _normalize_relation_response(response, request)

    parsed = result.get("parsed")
    if parsed is not None:
        response = (
            parsed
            if isinstance(parsed, RelationBuildResponse)
            else RelationBuildResponse.model_validate(parsed)
        )
        return _normalize_relation_response(response, request)

    raw_message = result.get("raw")
    parsing_error = result.get("parsing_error")
    raw_args = _extract_structured_args(raw_message)

    if raw_args is None:
        logger.warning(
            "Relation model returned no recoverable structured output for "
            "anchor_node_id={}: {}. Retrying once as plain JSON.",
            request.anchor_node_id,
            parsing_error,
        )
        raw_args = _plain_json_retry(
            model=model,
            messages=messages,
            schema=RelationBuildResponse,
            label="relation classification",
        )

    if raw_args is None:
        raise RelationModelError(
            "Could not recover RelationBuildResponse JSON for "
            f"anchor_node_id={request.anchor_node_id}. "
            f"Original parsing error: {parsing_error}"
        )

    response = RelationBuildResponse.model_validate(raw_args)
    return _normalize_relation_response(response, request)