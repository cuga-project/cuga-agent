from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Literal

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from loguru import logger
from pydantic import BaseModel, ValidationError

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

from .logging_utils import memory_graph_trace_enabled
from .model_decisions import (
    LogicalStructureAssessment,
    DecompositionAuditResult,
    ChunkSemanticAuditIssue as ChunkSemanticAuditIssue,
    ChunkSemanticAuditResult,
    AtomicClassificationAssessment,
    LogicNormalizationClause as LogicNormalizationClause,
    LogicNormalizationDecision,
    CompiledStructureDecision,
    SemanticSegmentPlan as SemanticSegmentPlan,
    SemanticSegmentationDecision,
    FinalChunkContextPlan,
    FinalChunkContextDecision,
    ContextualChunkIssue as ContextualChunkIssue,
    ContextualChunkingAssessment,
    FinalChunkContextRepair,
    SemanticLeafChunk,
    ContextSourceBlock,
    ContextualSourceChunk,
)
from .model_prompts import (
    _SEMANTIC_SEGMENTATION_SYSTEM_PROMPT,
    _FINAL_CONTEXTUALIZATION_SYSTEM_PROMPT,
    _CONTEXTUAL_CHUNKING_AUDIT_SYSTEM_PROMPT,
    _LOGIC_NORMALIZATION_SYSTEM_PROMPT,
    _LOGICAL_STRUCTURE_AUDIT_SYSTEM_PROMPT,
    _RELATION_SYSTEM_PROMPT,
    _CHUNK_SEMANTIC_AUDIT_SYSTEM_PROMPT,
    _ATOMIC_CLASSIFICATION_AUDIT_SYSTEM_PROMPT,
    _DECOMPOSITION_AUDIT_SYSTEM_PROMPT,
)
from .source_chunking import (
    _hard_split_span,
    split_source_for_decomposition as split_source_for_decomposition,
)


from .logic_slot_binding_deberta import (
    MATCHER_MODEL_NAME as _LOGIC_SLOT_MATCHER_NAME,
    call_logic_slot_binding_model as _call_logic_slot_binding_backend,
)


class DecompositionModelNotConfiguredError(RuntimeError):
    """Raised when no decomposition model is configured."""


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
            # Keep this field in exported matcher datasets.
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


_CONTEXT_BLOCK_MAX_CHARS = 700
_CONTEXTUAL_CHUNK_MAX_CHARS = 2500
_CONTEXTUALIZED_TEXT_MAX_CHARS = 3000
_MAX_SEMANTIC_SEGMENTATION_DEPTH = 8
_MAX_SEMANTIC_SEGMENTATION_RETRIES = 1
_MAX_CONTEXTUALIZATION_RETRIES = 1


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
            # S/P/O is computed after semantic decomposition.
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

    logger.debug(
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

    logger.debug(
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
        logger.debug(
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
            logger.debug(
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
            logger.debug(
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
            logger.debug(
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
    """Match one semantic node to logic slots with the local DeBERTa backend."""
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
    """Invoke the Stanza + DeDisCo decomposition pipeline."""
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
