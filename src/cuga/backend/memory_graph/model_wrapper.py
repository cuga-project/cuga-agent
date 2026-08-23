from __future__ import annotations

import json
import re
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
    LogicalOperator,
    RelationBuildRequest,
    RelationBuildResponse,
    RelationDecision,
    RelationDirection,
    RelationType,
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
    """Raised when local Boolean/cardinality extraction cannot be recovered."""


class LogicalStructureAssessment(BaseModel):
    """Semantic audit for one proposed local logical-structure decision."""

    complete: bool
    missing_logic: list[str] = Field(default_factory=list)
    unsupported_logic: list[str] = Field(default_factory=list)
    reason: str = ""


class DecompositionCoverageAssessment(BaseModel):
    """Semantic coverage audit for one local decomposition decision."""

    complete: bool
    missing_semantics: list[str] = Field(default_factory=list)
    unsupported_children: list[str] = Field(default_factory=list)
    reason: str = ""


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


class ContextualChunkingAssessment(BaseModel):
    """Semantic audit of finalized contextualized leaf chunks."""

    complete: bool
    issues: list[str] = Field(default_factory=list)
    reason: str = ""


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
- issues: concise descriptions of any semantic distortion, unresolved dependency,
  wrong referent, lost scope, unsupported addition, or excessive context copying
- reason: concise overall assessment
""".strip()


_LOGICAL_STRUCTURE_SYSTEM_PROMPT = """
You extract SOURCE-EXPLICIT Boolean/cardinality structure and conditional rule
structure over the DIRECT semantic children of exactly one composite statement.

You receive:
- the original composite SOURCE statement;
- the already-established direct CHILDREN with stable zero-based indices.

You do NOT create, rewrite, merge, delete, or reinterpret semantic children.
You do NOT construct the semantic memory graph.
You do NOT infer broad semantic relations.
Your task is only to preserve explicit Boolean/cardinality grouping and explicit
condition -> effect structure among the supplied children.

Allowed Boolean/cardinality operators
-------------------------------------
AND:
    All operands participate jointly in the expressed group.

OR:
    The operands are alternatives; at least one is sufficient unless the source
    explicitly states a stricter cardinality.

NOT:
    Negates exactly one nested term. Preserve grouping: "not (A and B)" is
    NOT(AND(A,B)), not AND(NOT(A),NOT(B)).

AT_LEAST / AT_MOST / EXACTLY:
    Explicit cardinality constraints. Put the stated non-negative integer in
    ``threshold``. Do not expand cardinality into a large SAT formula.

Flat expression representation
------------------------------
- Return a flat ``expressions`` list. Every expression has a unique integer
  ``expression_id``, one operator, and non-recursive operands.
- Each operand references exactly one supplied child through ``child_index`` OR
  one other logical expression through ``expression_id``.
- Nested grouping is created by expression_id references, not recursive JSON.
- For AND/OR, threshold must be null.
- NOT has exactly one operand and threshold=null.
- Cardinality operators have one or more operands and a threshold.

Assertions versus rules
-----------------------
Use ``assertions`` ONLY for standalone Boolean/cardinality OPERATOR EXPRESSIONS
whose grouping would otherwise be lost from the semantic graph. An assertion
references ``root_expression_id`` and can never directly reference a child.

Ordinary standalone semantic children are already represented by the semantic
memory graph. Do NOT duplicate a fact, definition, requirement, prohibition,
permission, procedure, exception, qualification, or other standalone child as a
logic assertion merely because it exists or is not used in a rule. A child may
legitimately appear nowhere in the logic layer.

Example:
    "A and B"
    expression 0 = AND(child A, child B)
    assertion.root_expression_id = 0

Example:
    "A. B. C."
    expressions=[]
    assertions=[]
    rules=[]

Example:
    "A and B. C."
    expression 0 = AND(child A, child B)
    assertion.root_expression_id = 0
    # No assertion is created for standalone child C.

Use ``rules`` when the source explicitly states that one logical term governs,
triggers, or is the condition for another logical term. A rule has:
- ``condition``: one child OR one expression;
- ``effect``: one child OR one expression.

Both sides are fully general logical terms.

Examples:
    "If A, do X"
        rule: condition=child A, effect=child X

    "If A and B, do X"
        expression 0 = AND(A,B)
        rule: condition=expression 0, effect=X

    "If A, do X and Y"
        expression 0 = AND(X,Y)
        rule: condition=A, effect=expression 0

    "If A or B, do X and Y"
        expression 0 = OR(A,B)
        expression 1 = AND(X,Y)
        rule: condition=expression 0, effect=expression 1

IMPORTANT: implication/conditional direction is NOT a Boolean operator. Do not
invent an IMPLIES node. Represent it with a rule whose condition and effect are
references to children or expressions.

Important distinctions
----------------------
- Do not encode temporal ordering here; PRECEDES belongs to the semantic relation
  layer.
- Do not encode causality, enablement, qualification, or ordinary semantic
  relations as Boolean operators.
- Preserve source grouping rather than algebraically normalizing it. For example,
  keep OR(A,B) -> AND(X,Y) rather than rewriting it into multiple equivalent
  implications.
- Do not interpret plain proximity as conjunction/disjunction.
- Do not infer exclusivity from ordinary "or". "A or B" means OR(A,B) unless the
  source explicitly says exactly one, only one, mutually exclusive, etc.
- "all of" naturally maps to AND; "any of" naturally maps to OR; "none of"
  can be represented as NOT(OR(...)) when that is what the source says.
- Preserve parentheses/scope/grouping exactly.

Evidence
--------
Every assertion and every rule must include ``evidence_text`` that quotes or
closely reproduces the immediate source wording expressing that logical
structure. Evidence for an assertion must support the Boolean/cardinality
grouping itself, not merely the existence of one semantic child.

Sparse output
-------------
If the source does not explicitly express Boolean/cardinality composition or an
explicit condition -> effect rule among these direct children, return:
    expressions=[]
    assertions=[]
    rules=[]

Return only the structured LocalLogicDecision.
""".strip()

_LOGICAL_STRUCTURE_AUDIT_SYSTEM_PROMPT = """
You audit a proposed source-local Boolean/cardinality and conditional-rule
extraction.

You receive:
- the original SOURCE composite statement;
- its already-established indexed direct CHILDREN;
- the proposed LocalLogicDecision.

Mark complete=true only if every explicit Boolean/cardinality grouping AND every
explicit condition -> effect structure among the supplied children is preserved
with correct scope and no unsupported logic is added.

Check especially:
- conjunction vs disjunction;
- nested grouping and parentheses/scope;
- negation scope;
- "all", "any", "none", "either", "neither", and equivalent constructions;
- at-least / at-most / exactly thresholds and the operands they govern;
- conditional direction for if/when/unless/provided/only-if style constructions;
- whether the condition and effect each reference the correct child or grouped
  expression;
- grouped consequents: "if C, do A and B" must be C -> AND(A,B), not AND(C,A,B);
- grouped antecedents: "if A and B, do X" must be AND(A,B) -> X;
- ordinary "or" must not be strengthened to XOR/exactly-one;
- temporal ordering, causality, enablement, qualification, and ordinary semantic
  relations must NOT be invented as Boolean structure.

RESPONSIBILITY BOUNDARY: semantic coverage is out of scope here. The decomposition
stage has already established and audited the semantic children. Do NOT require
every supplied child to appear in the logic layer. Do NOT report missing logic
merely because a standalone fact, definition, requirement, prohibition,
permission, procedure, exception, qualification, or other semantic child is not
referenced by an assertion or rule.

Use assertions only for standalone Boolean/cardinality OPERATOR EXPRESSIONS
without explicit condition -> effect direction. An assertion must reference an
expression_id; a bare child must never be asserted. Use rules for explicit
conditional direction. Either side of a rule may be a single child or a logical
expression.

Examples for audit scope:
- SOURCE "A. B. C." with empty logic is COMPLETE.
- SOURCE "A and B. C." needs ASSERT(AND(A,B)); standalone C needs nothing.
- SOURCE "If A, do B and C. D." needs A -> AND(B,C); standalone D needs nothing.

The proposed decision may be empty when no explicit Boolean/cardinality grouping
or conditional rule exists between the supplied children.

Return:
- complete: true/false
- missing_logic: concise descriptions of omitted logical/rule structure
- unsupported_logic: concise descriptions of invented or mis-scoped structure
- reason: concise overall assessment
""".strip()

_LOGIC_CUE_RE = re.compile(
    r"\b(?:and|or|either|neither|both|not|all|any|none)\b"
    r"|\b(?:if|when|whenever|unless|provided|assuming)\b"
    r"|\bonly\s+if\b|\bprovided\s+that\b|\bin\s+case\b"
    r"|\b(?:at\s+least|at\s+most|exactly)\b"
    r"|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+of\b",
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
- The hierarchical relation decomposes_into is forbidden here; it is owned by
  deterministic hierarchy construction.
- other_node_id must be the ID of one of the supplied candidates.
""".strip()


_DECOMPOSITION_SYSTEM_PROMPT = """
You decompose exactly ONE statement at a time.

You are NOT constructing a graph.
You are NOT responsible for canonical node IDs, parent IDs, hierarchy edges, graph
depth, graph mutation, candidate retrieval, or global lateral-relation discovery.
For a composite statement, you MAY return sparse local relation hints only when the
relation is explicit in the same parent statement.

Your goal is LOSSLESS semantic decomposition, not summarization.
The direct children of a composite statement must collectively preserve every
operationally or logically meaningful part of the parent. Hierarchy may compress
structure, but the atomic leaves must not compress away decision-relevant meaning.

Your only task is to decide whether the supplied statement is:

1. atomic
   - It expresses one independently meaningful fact, rule, requirement,
     prohibition, permission, condition, procedure, prescribed method,
     user claim, observation, or intended action.
   - A statement is NOT atomic merely because it can be summarized in one sentence.
     If it contains multiple independently applicable clauses, conditions,
     procedures, exceptions, or ordered requirements, it is composite.
   - Return kind="atomic".
   - Return a structured proposition.
   - Return children=[].

2. composite
   - Its meaning can be separated into more specific direct semantic components.
   - Return kind="composite".
   - Return the DIRECT child statements only.
   - Do not recursively decompose the children yourself.
   - Do not return a proposition for the composite statement.

A composite child must be strictly narrower than its parent.
Never return the complete parent statement unchanged as one of its children.
Do not create vague heading-like children when the parent contains concrete rules.

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

Preserve, whenever present:
- facts and assertions;
- requirements and prohibitions;
- permissions and optional actions;
- prerequisites and postconditions;
- conditions and conditional branches;
- procedures, prescribed methods, and means of accomplishing/checking something;
- statements that explain how a prerequisite, state, condition, or result is
  established or verified;
- exceptions, overrides, and fallback rules;
- temporal and ordering constraints such as before, after, first, then, until;
- thresholds, counts, quantifiers, and selection rules;
- polarity, modality, attribution, uncertainty, and scope;
- restrictive or satisfaction-changing qualifiers.

SEMANTIC OPERATORS MUST SURVIVE
-------------------------------
Words and constructions such as "only", "unless", "before", "after", "first",
"then", "correctly", "at least", "at most", "exactly", "any", "all", "none",
"except", "if", "when", "until", and equivalent phrasing are semantically
binding when they affect whether a rule is satisfied. Do not paraphrase them away.

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
Do NOT encode a procedure as proposition.condition unless the source actually
states it as a condition or prerequisite.

SOURCE GROUNDING
----------------
Every child must be directly supported by a specific phrase, sentence, or clause
in the supplied parent statement. A child may paraphrase for clarity, but it must
not add a rule, prerequisite, exception, or implication that is absent from the
source. Return that support explicitly in the child's ``source_text`` field. Prefer a
verbatim contiguous excerpt from the parent whenever possible. Before returning,
map every child back to supporting source wording and confirm that no operative
source clause is left unmapped. Exact punctuation or formatting identity is less
important than faithful semantic support.

For policy/rule text:
- preserve requirements, prohibitions, permissions, conditions, and procedures;
- preserve modality, ordering, exceptions, and satisfaction criteria;
- preserve how prerequisites are established, not only that they are required;
- preserve what must be true for a condition to count as satisfied;
- put an explicit prerequisite in proposition.condition only when the current
  atomic statement itself is conditional on that prerequisite.

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

LOCAL RELATION HINTS
--------------------
For composite statements, return ``local_relations`` only for relations that are
explicitly expressed by the immediate parent text between two returned children.
Do not perform broad/global relation discovery here. The later relation linker
owns inferred relations across the graph.

Use zero-based child indices. ``source_child_index`` is the relation source and
``target_child_index`` is the relation target. ``evidence_text`` should preferably be a verbatim contiguous excerpt from the
parent that supports the relation. Minor formatting normalization is acceptable
when the relation is still explicitly supported by the parent.

Examples of generic source-explicit patterns:
- "To establish P, perform A" -> A ENABLES P when both meanings are represented
  by returned children.
- "B requires P" -> B REQUIRES P.
- "Rule R applies only when C" -> C QUALIFIES R when represented as separate
  children.
- "First A, then B" -> A PRECEDES B.
- "Before B, perform A" -> A PRECEDES B.
- "After A, perform B" -> A PRECEDES B.

Keep ordering distinct from prerequisites:
- PRECEDES means one action/state must occur earlier than another.
- REQUIRES means one action/state depends on another condition/state being
  satisfied or true.
- Do not use REQUIRES merely to encode that A comes before B.

Do not reverse an establishment procedure into a prerequisite. If A is the
permitted method used to establish/check P, do not emit A REQUIRES P merely
because another action later requires P.

Set origin="source_explicit" for these hints. If a relation would require
additional inference rather than being expressed in the parent, omit it and let
the relation linker infer it later.

FINAL SELF-CHECK BEFORE RETURNING
---------------------------------
If kind="composite", ask yourself:
1. Did every MUST/MUST NOT/MAY/ONLY/UNLESS/IF/WHEN clause survive?
2. Did every procedure or "how to establish/check X" clause survive?
3. Did every ordering word such as before/after/first/then survive?
4. Did every qualifier or threshold that changes satisfaction survive?
5. Did every exception/override survive?
6. Is every child directly supported by source_text from the parent?
7. Did I preserve any source-explicit child-to-child REQUIRES, ENABLES,
   QUALIFIES, PRECEDES, or other direct relation that would otherwise be lost?
8. Is every local relation directly backed by evidence_text from the parent?

If any answer is no, add or revise direct children/local relations before returning.

routing_text must be a short retrieval-oriented description of the current
statement. It is not a substitute for content and must not contain important
semantics that are absent from content.

The proposition schema is strict.
The ONLY allowed proposition fields are:
- subject
- predicate
- object
- polarity
- modality
- quantifier
- temporal_scope
- condition
- attribution
- qualifiers

Every proposition field except qualifiers must be a string or null.
Structured lists or dictionaries belong inside qualifiers.
""".strip()

_DECOMPOSITION_COVERAGE_SYSTEM_PROMPT = """
You audit one proposed semantic decomposition for LOSSLESS coverage.

You receive:
- the original SOURCE statement;
- a proposed LocalDecompositionDecision.

You are NOT decomposing the source yourself and you are NOT building a graph.
Your task is to detect semantic loss or unsupported additions.

Mark complete=true only when the proposed decision preserves every independently
operative part of the source that can affect later reasoning or verification.

Check especially for omitted or weakened:
- requirements, prohibitions, permissions;
- prerequisites, conditions, and postconditions;
- procedures or prescribed methods, including how a prerequisite/state/result is
  established, checked, verified, or obtained;
- exceptions, overrides, and fallback rules;
- ordering constraints such as before/after/first/then/until; when ordering is
  represented between separate children, PRECEDES is the canonical relation;
- restrictive qualifiers and satisfaction criteria such as only, unless,
  correctly, thresholds, counts, and quantifiers.

BOOLEAN/CARDINALITY RESPONSIBILITY BOUNDARY
------------------------------------------
A dedicated logical-structure extractor runs immediately after a composite
decomposition and is responsible for preserving AND/OR/NOT grouping and explicit
AT_LEAST/AT_MOST/EXACTLY cardinality over the returned direct children.

Therefore, do NOT mark an otherwise complete decomposition incomplete solely
because Boolean/cardinality grouping among already-present child meanings is not
encoded in local_relations. Do still mark it incomplete if an operand meaning,
threshold value, polarity, condition, or other semantic content needed by that
logical extractor has been omitted or weakened from the children themselves.
- modality, polarity, attribution, uncertainty, temporal scope, and quantifiers.

Also detect unsupported children: a child is unsupported if it adds a rule,
condition, implication, prerequisite, exception, or factual claim that the source
does not state or clearly entail. Judge support from the SOURCE itself. The
child's source_text is a provenance aid, not an independent semantic assertion.

Do NOT mark the decomposition incomplete solely because source_text or
evidence_text differs from the source in bullet markers, whitespace, punctuation,
quote style, capitalization, or other minor formatting normalization. Exact
provenance precision is checked deterministically outside this semantic audit.

Audit local_relations too. A local relation is valid here only when the immediate
source explicitly expresses the relation between the referenced child meanings.
Missing an explicit procedure/prerequisite relation that materially affects later
reasoning counts as missing semantics.

Important distinctions:
- An ordering relation is not the same as a prerequisite. If the source states
  only that A occurs before B, PRECEDES is appropriate; do not require A or B
  merely because they are ordered.
- A prerequisite is not the same as the permitted procedure used to establish or
  check that prerequisite. If the source contains both, both must survive.
- "provides X" is not equivalent to "provides X correctly" when correctness is
  part of the satisfaction criterion.
- A large parent may be composite even if the proposed decision labels it atomic.
- A concise paraphrase is acceptable only if it preserves all operative meaning.

Return:
- complete: true/false
- missing_semantics: short descriptions of source meanings that are absent or
  materially weakened
- unsupported_children: short descriptions of proposed child meanings not
  supported by the source
- reason: concise overall assessment

Do not propose benchmark-specific fixes. Judge only semantic coverage of the
supplied source.
""".strip()

_ALLOWED_PROPOSITION_FIELDS = {
    "subject",
    "predicate",
    "object",
    "polarity",
    "modality",
    "quantifier",
    "temporal_scope",
    "condition",
    "attribution",
    "qualifiers",
}

_STRING_PROPOSITION_FIELDS = {
    "subject",
    "predicate",
    "object",
    "polarity",
    "modality",
    "quantifier",
    "temporal_scope",
    "condition",
    "attribution",
}


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


def _normalize_proposition_payload(
    proposition: dict[str, Any],
) -> dict[str, Any]:
    """Normalize common provider drift without weakening the strict schema."""
    proposition = dict(proposition)

    qualifiers = proposition.get("qualifiers")
    if not isinstance(qualifiers, dict):
        qualifiers = (
            {}
            if qualifiers is None
            else {"raw_qualifiers": qualifiers}
        )
    else:
        qualifiers = dict(qualifiers)

    action = proposition.pop("action", None)
    if action is not None:
        if not proposition.get("predicate"):
            proposition["predicate"] = str(action)
        else:
            qualifiers["action"] = action

    for key in list(proposition):
        if key not in _ALLOWED_PROPOSITION_FIELDS:
            qualifiers[key] = proposition.pop(key)

    for key in _STRING_PROPOSITION_FIELDS:
        value = proposition.get(key)
        if value is None or isinstance(value, str):
            continue

        qualifiers[f"raw_{key}"] = value
        if isinstance(value, list):
            proposition[key] = ", ".join(str(item) for item in value)
        elif isinstance(value, dict):
            proposition[key] = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
            )
        else:
            proposition[key] = str(value)

    proposition["qualifiers"] = qualifiers
    return proposition


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

    kind = raw.get("kind")
    if isinstance(kind, str):
        raw["kind"] = kind.strip().casefold()

    proposition = raw.get("proposition")
    if isinstance(proposition, dict):
        raw["proposition"] = _normalize_proposition_payload(proposition)

    children = raw.get("children")
    if isinstance(children, list):
        normalized_children: list[Any] = []
        for child in children:
            if not isinstance(child, dict):
                normalized_children.append(child)
                continue
            normalized_child = dict(child)
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


def _assess_contextual_chunking(
    *,
    request: GraphBuildRequest,
    blocks: list[ContextSourceBlock],
    chunks: list[ContextualSourceChunk],
) -> ContextualChunkingAssessment:
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
    structured_model = model.with_structured_output(
        ContextualChunkingAssessment,
        method="function_calling",
        include_raw=True,
    )

    try:
        result = structured_model.invoke(messages)
    except Exception as exc:
        logger.warning(
            "Contextual chunk audit structured call failed for source_id={}: {}. "
            "Retrying once as plain JSON.",
            request.source_id,
            exc,
        )
        raw_args = _plain_json_retry(
            model=model,
            messages=messages,
            schema=ContextualChunkingAssessment,
            label="contextual chunk audit",
        )
        if raw_args is None:
            raise ContextualChunkingModelError(
                "Could not recover contextual chunk audit for "
                f"source_id={request.source_id}"
            ) from exc
        return ContextualChunkingAssessment.model_validate(raw_args)

    parsed = result.get("parsed")
    if parsed is not None:
        return (
            parsed
            if isinstance(parsed, ContextualChunkingAssessment)
            else ContextualChunkingAssessment.model_validate(parsed)
        )

    raw_message = result.get("raw")
    parsing_error = result.get("parsing_error")
    raw_args = _extract_structured_args(raw_message)

    if raw_args is None:
        logger.warning(
            "Contextual chunk audit returned no recoverable structured output "
            "for source_id={}: {}. Retrying once as plain JSON.",
            request.source_id,
            parsing_error,
        )
        raw_args = _plain_json_retry(
            model=model,
            messages=messages,
            schema=ContextualChunkingAssessment,
            label="contextual chunk audit",
        )

    if raw_args is None:
        raise ContextualChunkingModelError(
            "Could not recover ContextualChunkingAssessment JSON for "
            f"source_id={request.source_id}"
        )

    return ContextualChunkingAssessment.model_validate(raw_args)


def _final_contextualization_repair_message(
    *,
    decision: FinalChunkContextDecision,
    issue: str,
) -> HumanMessage:
    return HumanMessage(
        content=(
            "FINAL_CONTEXTUALIZATION_REPAIR_REQUIRED\n"
            "Revise the contextualization of the SAME finalized leaf chunks. "
            "Do not change chunk boundaries.\n\n"
            f"ISSUE:\n{issue}\n\n"
            "PREVIOUS_DECISION_BEGIN\n"
            f"{json.dumps(decision.model_dump(mode='json'), ensure_ascii=False, indent=2)}\n"
            "PREVIOUS_DECISION_END\n\n"
            "Return exactly one output per supplied chunk_index. Preserve source "
            "meaning, use only necessary external context, and keep each rewrite "
            "concise and self-contained."
        )
    )


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
    generation_messages = list(base_messages)

    decision = _invoke_final_contextualization_decision(
        model=contextualization_model,
        messages=generation_messages,
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

            generation_messages = [
                *base_messages,
                _final_contextualization_repair_message(
                    decision=decision,
                    issue=str(exc),
                ),
            ]
            decision = _invoke_final_contextualization_decision(
                model=contextualization_model,
                messages=generation_messages,
                source_id=request.source_id,
                label="final chunk contextualization repair",
            )
            continue

        assessment = _assess_contextual_chunking(
            request=request,
            blocks=blocks,
            chunks=chunks,
        )

        logger.info(
            "Final contextualization audit: source_id={} attempt={}/{} "
            "complete={} chunks={} issues={} reason={}",
            request.source_id,
            attempt + 1,
            _MAX_CONTEXTUALIZATION_RETRIES + 1,
            assessment.complete,
            len(chunks),
            len(assessment.issues),
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
                f"source_id={request.source_id}. Issues={assessment.issues}; "
                f"Reason={assessment.reason}"
            )

        issue = (
            "; ".join(assessment.issues)
            if assessment.issues
            else assessment.reason
        )
        generation_messages = [
            *base_messages,
            _final_contextualization_repair_message(
                decision=decision,
                issue=issue,
            ),
        ]
        decision = _invoke_final_contextualization_decision(
            model=contextualization_model,
            messages=generation_messages,
            source_id=request.source_id,
            label="final chunk contextualization repair",
        )

    raise ContextualChunkingModelError(
        f"Unexpected contextual source preparation state for "
        f"source_id={request.source_id}"
    )


_MAX_DECOMPOSITION_COVERAGE_RETRIES = 2


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
    """Serialize the source and proposed decision for semantic coverage audit."""
    decision_payload = decision.model_dump(
        mode="json",
        exclude_none=True,
    )
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


def _assess_decomposition_coverage(
    *,
    request: GraphBuildRequest,
    decision: LocalDecompositionDecision,
) -> DecompositionCoverageAssessment:
    """Audit whether a local decomposition preserves all operative semantics."""
    messages: list[BaseMessage] = [
        SystemMessage(content=_DECOMPOSITION_COVERAGE_SYSTEM_PROMPT),
        HumanMessage(content=_coverage_request_text(request, decision)),
    ]

    # Coverage checking is deliberately given slightly more reasoning budget than
    # decomposition because its job is to notice subtle omissions/weakening rather
    # than to generate a concise decomposition.
    model = _get_model(reasoning_effort="medium")
    structured_model = model.with_structured_output(
        DecompositionCoverageAssessment,
        method="function_calling",
        include_raw=True,
    )

    try:
        result = structured_model.invoke(messages)
    except Exception as exc:
        logger.warning(
            "Decomposition coverage structured call failed for source_id={}: {}. "
            "Retrying once as plain JSON.",
            request.source_id,
            exc,
        )
        raw_args = _plain_json_retry(
            model=model,
            messages=messages,
            schema=DecompositionCoverageAssessment,
            label="decomposition coverage audit",
        )
        if raw_args is None:
            raise PromptDecompositionModelError(
                "Could not recover decomposition coverage audit for "
                f"source_id={request.source_id}"
            ) from exc
        return DecompositionCoverageAssessment.model_validate(raw_args)

    parsed = result.get("parsed")
    if parsed is not None:
        return (
            parsed
            if isinstance(parsed, DecompositionCoverageAssessment)
            else DecompositionCoverageAssessment.model_validate(parsed)
        )

    raw_message = result.get("raw")
    parsing_error = result.get("parsing_error")
    raw_args = _extract_structured_args(raw_message)

    if raw_args is None:
        logger.warning(
            "Decomposition coverage audit returned no recoverable structured "
            "output for source_id={}: {}. Retrying once as plain JSON.",
            request.source_id,
            parsing_error,
        )
        raw_args = _plain_json_retry(
            model=model,
            messages=messages,
            schema=DecompositionCoverageAssessment,
            label="decomposition coverage audit",
        )

    if raw_args is None:
        raise PromptDecompositionModelError(
            "Could not recover DecompositionCoverageAssessment JSON for "
            f"source_id={request.source_id}. "
            f"Original parsing error: {parsing_error}"
        )

    return DecompositionCoverageAssessment.model_validate(raw_args)


def _coverage_feedback_message(
    assessment: DecompositionCoverageAssessment,
) -> HumanMessage:
    """Turn a failed audit into focused, source-grounded repair feedback."""
    missing = assessment.missing_semantics or ["none reported"]
    unsupported = assessment.unsupported_children or ["none reported"]

    return HumanMessage(
        content=(
            "SEMANTIC_COVERAGE_AUDIT_FAILED\n"
            "The proposed decomposition was structurally valid but was not "
            "semantically lossless. Revise the decomposition of the SAME source "
            "statement. Do not answer the audit; return a new "
            "LocalDecompositionDecision.\n\n"
            "MISSING_OR_WEAKENED_SEMANTICS:\n- "
            + "\n- ".join(missing)
            + "\n\nUNSUPPORTED_CHILD_MEANINGS:\n- "
            + "\n- ".join(unsupported)
            + "\n\nAUDIT_REASON:\n"
            + assessment.reason
            + "\n\nRepair requirements:\n"
            "- preserve every omitted operative clause as a direct child or in "
            "the atomic proposition, as appropriate;\n"
            "- restore lost procedures, ordering, modality, conditions, "
            "exceptions, thresholds, and restrictive qualifiers;\n"
            "- remove or rewrite unsupported child meanings;\n"
            "- keep every child semantically grounded in the original source;\n"
            "- do not spend a repair attempt merely fixing punctuation, bullet "
            "markers, or whitespace in provenance excerpts;\n"
            "- do not merely make the previous children more verbose."
        )
    )

def _logic_request_text(
    request: GraphBuildRequest,
    decision: LocalDecompositionDecision,
) -> str:
    children = [
        {
            "child_index": index,
            "content": child.content,
            "semantic_role": child.semantic_role.value,
            "source_text": child.source_text,
        }
        for index, child in enumerate(decision.children)
    ]
    return (
        f"SOURCE_TYPE: {request.source_type.value}\n"
        f"SOURCE_ID: {request.source_id}\n\n"
        "SOURCE_BEGIN\n"
        f"{request.content}\n"
        "SOURCE_END\n\n"
        "DIRECT_CHILDREN_BEGIN\n"
        f"{json.dumps(children, ensure_ascii=False, indent=2)}\n"
        "DIRECT_CHILDREN_END"
    )


def _iter_local_logic_child_indices(
    decision: LocalLogicDecision,
):
    for node in decision.expressions:
        for operand in node.operands:
            if operand.child_index is not None:
                yield operand.child_index

    # Assertions can only reference expression IDs. Child references contained
    # inside those expressions are already yielded above.

    for rule in decision.rules:
        if rule.condition.child_index is not None:
            yield rule.condition.child_index
        if rule.effect.child_index is not None:
            yield rule.effect.child_index


def _validate_local_logic_decision(
    *,
    decision: LocalLogicDecision,
    child_count: int,
) -> None:
    expression_ids = {node.expression_id for node in decision.expressions}

    for assertion_index, assertion in enumerate(decision.assertions):
        if assertion.root_expression_id not in expression_ids:
            raise LogicalStructureModelError(
                f"Logical assertion[{assertion_index}] references unknown "
                f"root_expression_id={assertion.root_expression_id}"
            )

    for child_index in _iter_local_logic_child_indices(decision):
        if child_index < 0 or child_index >= child_count:
            raise LogicalStructureModelError(
                f"Logical structure references child_index={child_index} "
                f"outside child_count={child_count}"
            )


def _invoke_local_logic_decision(
    *,
    model: Any,
    messages: list[BaseMessage],
    source_id: str,
    label: str,
) -> LocalLogicDecision:
    structured_model = model.with_structured_output(
        LocalLogicDecision,
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
                    if isinstance(parsed, LocalLogicDecision)
                    else LocalLogicDecision.model_validate(parsed)
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
                return LocalLogicDecision.model_validate(raw_args)
            except ValidationError as exc:
                logger.warning(
                    "{} raw args failed validation for source_id={}: {}",
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
        schema=LocalLogicDecision,
        label=label,
    )
    if raw_args is not None:
        try:
            return LocalLogicDecision.model_validate(raw_args)
        except ValidationError as exc:
            logger.warning(
                "{} plain-JSON retry failed validation for source_id={}: {}",
                label,
                source_id,
                exc,
            )

    error = LogicalStructureModelError(
        f"Could not recover LocalLogicDecision for source_id={source_id}"
    )
    if structured_exception is not None:
        raise error from structured_exception
    raise error


def _logic_audit_text(
    request: GraphBuildRequest,
    decomposition: LocalDecompositionDecision,
    logic: LocalLogicDecision,
) -> str:
    return (
        f"{_logic_request_text(request, decomposition)}\n\n"
        "PROPOSED_LOGIC_BEGIN\n"
        f"{json.dumps(logic.model_dump(mode='json'), ensure_ascii=False, indent=2)}\n"
        "PROPOSED_LOGIC_END"
    )


def _assess_logical_structure(
    *,
    request: GraphBuildRequest,
    decomposition: LocalDecompositionDecision,
    logic: LocalLogicDecision,
) -> LogicalStructureAssessment:
    messages: list[BaseMessage] = [
        SystemMessage(content=_LOGICAL_STRUCTURE_AUDIT_SYSTEM_PROMPT),
        HumanMessage(content=_logic_audit_text(request, decomposition, logic)),
    ]
    model = _get_model(reasoning_effort="medium")
    structured_model = model.with_structured_output(
        LogicalStructureAssessment,
        method="function_calling",
        include_raw=True,
    )
    try:
        result = structured_model.invoke(messages)
    except Exception as exc:
        logger.warning(
            "Logical structure audit structured call failed for source_id={}: {}. "
            "Retrying once as plain JSON.",
            request.source_id,
            exc,
        )
        raw_args = _plain_json_retry(
            model=model,
            messages=messages,
            schema=LogicalStructureAssessment,
            label="logical structure audit",
        )
        if raw_args is None:
            raise LogicalStructureModelError(
                f"Could not recover logical structure audit for source_id="
                f"{request.source_id}"
            ) from exc
        return LogicalStructureAssessment.model_validate(raw_args)

    parsed = result.get("parsed")
    if parsed is not None:
        return (
            parsed
            if isinstance(parsed, LogicalStructureAssessment)
            else LogicalStructureAssessment.model_validate(parsed)
        )

    raw_args = _extract_structured_args(result.get("raw"))
    if raw_args is None:
        raw_args = _plain_json_retry(
            model=model,
            messages=messages,
            schema=LogicalStructureAssessment,
            label="logical structure audit",
        )
    if raw_args is None:
        raise LogicalStructureModelError(
            f"Could not recover LogicalStructureAssessment for source_id="
            f"{request.source_id}"
        )
    return LogicalStructureAssessment.model_validate(raw_args)


def _logic_repair_message(
    *,
    decision: LocalLogicDecision,
    assessment: LogicalStructureAssessment,
) -> HumanMessage:
    missing = assessment.missing_logic or ["none reported"]
    unsupported = assessment.unsupported_logic or ["none reported"]
    return HumanMessage(
        content=(
            "LOGICAL_STRUCTURE_AUDIT_FAILED\n"
            "Revise ONLY the Boolean/cardinality and condition/effect structure "
            "over the same supplied children. Do not change or invent semantic "
            "children. Do not add assertions for standalone semantic children; "
            "assertions must reference Boolean/cardinality expressions.\n\n"
            "MISSING_LOGIC:\n- " + "\n- ".join(missing)
            + "\n\nUNSUPPORTED_LOGIC:\n- " + "\n- ".join(unsupported)
            + "\n\nAUDIT_REASON:\n" + assessment.reason
            + "\n\nPREVIOUS_DECISION_BEGIN\n"
            + json.dumps(decision.model_dump(mode="json"), ensure_ascii=False, indent=2)
            + "\nPREVIOUS_DECISION_END"
        )
    )


def call_logical_structure_model(
    request: GraphBuildRequest,
    decomposition: LocalDecompositionDecision,
) -> LocalLogicDecision:
    """Extract explicit Boolean/cardinality and condition/effect structure.

    This is deliberately a second model call after semantic decomposition. The
    model may only arrange existing child indices; it cannot create semantic
    propositions. Empty output is valid when the parent has no explicit logical
    grouping or conditional rule that needs a separate representation.
    """
    if decomposition.kind != "composite" or len(decomposition.children) < 2:
        return LocalLogicDecision()

    # Avoid doubling LLM calls for composites with no lexical sign of Boolean,
    # cardinality, or conditional structure. This is a generic syntax-level gate.
    if _LOGIC_CUE_RE.search(request.content) is None:
        return LocalLogicDecision()

    base_messages: list[BaseMessage] = [
        SystemMessage(content=_LOGICAL_STRUCTURE_SYSTEM_PROMPT),
        HumanMessage(content=_logic_request_text(request, decomposition)),
    ]
    model = _get_model(reasoning_effort="low")
    generation_messages = list(base_messages)
    decision = _invoke_local_logic_decision(
        model=model,
        messages=generation_messages,
        source_id=request.source_id,
        label="logical structure extraction",
    )

    for attempt in range(_MAX_LOGICAL_STRUCTURE_RETRIES + 1):
        _validate_local_logic_decision(
            decision=decision,
            child_count=len(decomposition.children),
        )
        assessment = _assess_logical_structure(
            request=request,
            decomposition=decomposition,
            logic=decision,
        )
        logger.info(
            "Logical structure audit: source_id={} attempt={}/{} complete={} "
            "assertions={} rules={} expressions={} missing={} unsupported={} reason={}",
            request.source_id,
            attempt + 1,
            _MAX_LOGICAL_STRUCTURE_RETRIES + 1,
            assessment.complete,
            len(decision.assertions),
            len(decision.rules),
            len(decision.expressions),
            len(assessment.missing_logic),
            len(assessment.unsupported_logic),
            assessment.reason,
        )
        if assessment.complete:
            return decision
        if attempt >= _MAX_LOGICAL_STRUCTURE_RETRIES:
            raise LogicalStructureModelError(
                "Logical structure remained incomplete after "
                f"{_MAX_LOGICAL_STRUCTURE_RETRIES} repair attempt(s) for "
                f"source_id={request.source_id}. Missing={assessment.missing_logic}; "
                f"Unsupported={assessment.unsupported_logic}; Reason={assessment.reason}"
            )
        generation_messages = [
            *base_messages,
            _logic_repair_message(decision=decision, assessment=assessment),
        ]
        decision = _invoke_local_logic_decision(
            model=model,
            messages=generation_messages,
            source_id=request.source_id,
            label="logical structure repair",
        )

    raise LogicalStructureModelError(
        f"Unexpected logical structure state for source_id={request.source_id}"
    )


def call_prompt_decomposition_model(
    request: GraphBuildRequest,
) -> LocalDecompositionDecision:
    """Decompose one statement with semantic coverage verification.

    ``GraphBuilder`` still owns recursive traversal and deterministic hierarchy
    edges. This wrapper owns the semantic contract of each local decomposition:
    generation -> lossless-coverage audit -> bounded repair when needed.

    The returned ``LocalDecompositionDecision`` carries child-level source
    provenance and optional source-explicit local relation hints. ``GraphBuilder``
    remains responsible for resolving excerpts to source spans and materializing
    canonical graph edges.
    """
    depth = request.metadata.get("decomposition_depth", 0)

    base_messages: list[BaseMessage] = [
        SystemMessage(content=_DECOMPOSITION_SYSTEM_PROMPT),
        HumanMessage(
            content=(
                f"SOURCE_TYPE: {request.source_type.value}\n"
                f"SOURCE_ID: {request.source_id}\n"
                f"CURRENT_DEPTH: {depth}\n\n"
                "STATEMENT_BEGIN\n"
                f"{request.content}\n"
                "STATEMENT_END"
            )
        ),
    ]

    model = _get_model(reasoning_effort="low")
    generation_messages = list(base_messages)

    decision = _invoke_local_decomposition_decision(
        model=model,
        messages=generation_messages,
        source_id=request.source_id,
        depth=depth,
        label="prompt decomposition",
    )

    for audit_attempt in range(_MAX_DECOMPOSITION_COVERAGE_RETRIES + 1):
        grounding_issues = _decomposition_grounding_issues(
            request=request,
            decision=decision,
        )

        if grounding_issues:
            logger.warning(
                "Prompt decomposition provenance is approximate for "
                "source_id={} depth={}: {}",
                request.source_id,
                depth,
                grounding_issues,
            )

        # Provenance precision is diagnostic. Semantic coverage remains strict.
        # The builder will resolve exact excerpts when possible and otherwise
        # inherit a proven parent source span rather than inventing offsets.
        assessment = _assess_decomposition_coverage(
            request=request,
            decision=decision,
        )

        logger.info(
            "Prompt decomposition coverage: source_id={} depth={} "
            "attempt={}/{} complete={} missing={} unsupported={} reason={}",
            request.source_id,
            depth,
            audit_attempt + 1,
            _MAX_DECOMPOSITION_COVERAGE_RETRIES + 1,
            assessment.complete,
            len(assessment.missing_semantics),
            len(assessment.unsupported_children),
            assessment.reason,
        )

        if assessment.complete:
            return decision

        if audit_attempt >= _MAX_DECOMPOSITION_COVERAGE_RETRIES:
            raise PromptDecompositionModelError(
                "Semantic coverage remained incomplete after "
                f"{_MAX_DECOMPOSITION_COVERAGE_RETRIES} repair attempt(s) for "
                f"source_id={request.source_id} depth={depth}. "
                f"Missing={assessment.missing_semantics}; "
                f"Unsupported={assessment.unsupported_children}; "
                f"Reason={assessment.reason}"
            )

        logger.warning(
            "Prompt decomposition failed semantic coverage for source_id={} "
            "depth={}; retrying with audit feedback. missing={} unsupported={}",
            request.source_id,
            depth,
            assessment.missing_semantics,
            assessment.unsupported_children,
        )

        # Keep only the source plus the latest coverage feedback. We intentionally
        # do not include the previous model answer as an assistant message: the
        # audit already summarizes what was missing/unsupported, and this avoids
        # anchoring the repair on a lossy decomposition.
        generation_messages = [
            *base_messages,
            _coverage_feedback_message(assessment),
        ]
        decision = _invoke_local_decomposition_decision(
            model=model,
            messages=generation_messages,
            source_id=request.source_id,
            depth=depth,
            label="prompt decomposition repair",
        )

    # Defensive; the loop either returns a complete decision or raises above.
    raise PromptDecompositionModelError(
        f"Unexpected decomposition coverage state for source_id={request.source_id}"
    )

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

        if decision.relation == RelationType.DECOMPOSES_INTO:
            logger.warning(
                "Ignoring forbidden lateral decomposes_into relation "
                "for anchor_node_id={} candidate_node_id={}",
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