from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from loguru import logger
from pydantic import ValidationError

from .logic_linker import logic_slot_match_score, match_logic_node_to_slot_candidates
from .atomic_payload_spacy import extract_atomic_payloads_spacy
from .logging_utils import memory_graph_trace_enabled
from .decomposition_stanza_dedisco import (
    call_prompt_decomposition_model,
    classify_terminal_for_retrieval,
)
from .model_wrapper import (
    call_logic_structure_model,
    split_source_for_decomposition,
)
from .retrieval import build_retrieval_text, retrieval_text_hash
from .retrieval_embedding_qwen import (
    embed_retrieval_texts_qwen,
    embedding_model_name as qwen_embedding_model_name,
    retrieval_embeddings_enabled,
)
from .schemas import (
    BuildValidation,
    CreationMethod,
    DecompositionDraft,
    DraftHierarchyEdge,
    DraftLogicAssertion,
    DraftLogicExpression,
    DraftLogicRule,
    DraftLogicSlot,
    DraftLogicSlotBinding,
    DraftNode,
    DraftRelation,
    EdgeFamily,
    GraphBuildRequest,
    GraphBuildResult,
    LocalDecompositionDecision,
    LocalLogicDecision,
    LocalLogicOperand,
    LocalNormalizedRelation,
    LogicPropositionCandidate,
    LogicCompoundAssertion,
    LogicCompoundRule,
    LogicExpression,
    LogicLayer,
    LogicLiteral,
    LogicLiteralAssertion,
    LogicRelation,
    LogicRelationType,
    LogicSlot,
    LogicSlotCandidate,
    LogicalOperator,
    LogicSlotBinding,
    MemoryEdge,
    MemoryNode,
    NodeKind,
    PropositionPayload,
    RelationOrigin,
    RelationType,
    RetrievalEmbedding,
    SourceReference,
    SourceSpan,
)
from .validation import DecompositionValidator


_LEXICAL_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?")
_ACTION_SEMANTIC_ROLES = {
    "requirement",
    "prohibition",
    "permission",
    "procedure",
    "intended_action",
}
_IMPERATIVE_PREFIX_WORDS = {
    "ask", "avoid", "call", "change", "check", "collect", "confirm",
    "continue", "create", "do", "ensure", "execute", "get", "give",
    "keep", "list", "log", "make", "provide",
    "read", "request", "respond", "return", "search", "send", "stop",
    "tell", "transfer", "unlock", "update", "use", "verify", "wait", "write",
}
_AUXILIARY_OR_MODAL_WORDS = {
    "can", "cannot", "could", "did", "do", "does", "had", "has", "have",
    "may", "might", "must", "shall", "should", "will", "would",
}
_COPULA_FORMS = {"am", "are", "be", "been", "being", "is", "was", "were"}
_PREDICATE_SKIP_WORDS = {
    "also", "always", "directly", "ever", "first", "immediately", "just",
    "never", "not", "only", "simply", "then",
}
_IRREGULAR_PREDICATE_LEMMAS = {
    "am": "be", "are": "be", "been": "be", "being": "be", "is": "be",
    "was": "be", "were": "be", "did": "do", "does": "do", "done": "do",
    "had": "have", "has": "have",
}


def _lexical_tokens(text: str) -> list[str]:
    return [match.group(0).casefold() for match in _LEXICAL_TOKEN_RE.finditer(text)]


def _phrase_grounded(candidate: str, source: str) -> bool:
    candidate_tokens = _lexical_tokens(candidate)
    source_tokens = _lexical_tokens(source)
    if not candidate_tokens or len(candidate_tokens) > len(source_tokens):
        return False
    width = len(candidate_tokens)
    return any(
        source_tokens[index : index + width] == candidate_tokens
        for index in range(len(source_tokens) - width + 1)
    )


def _predicate_roots(token: str) -> set[str]:
    word = token.casefold().strip()
    if not word:
        return set()
    roots = {word, _IRREGULAR_PREDICATE_LEMMAS.get(word, word)}
    if len(word) > 3 and word.endswith("ies"):
        roots.add(word[:-3] + "y")
    if len(word) > 3 and word.endswith("s"):
        roots.add(word[:-1])
    if len(word) > 4 and word.endswith("es"):
        roots.add(word[:-2])
    if len(word) > 4 and word.endswith("ed"):
        stem = word[:-2]
        roots.add(stem)
        roots.add(stem + "e")
        if len(stem) >= 2 and stem[-1] == stem[-2]:
            roots.add(stem[:-1])
    if len(word) > 5 and word.endswith("ing"):
        stem = word[:-3]
        roots.add(stem)
        roots.add(stem + "e")
        if len(stem) >= 2 and stem[-1] == stem[-2]:
            roots.add(stem[:-1])
    return {root for root in roots if root}


def _predicate_grounded(candidate: str, source: str) -> bool:
    if _phrase_grounded(candidate, source):
        return True
    candidate_tokens = _lexical_tokens(candidate)
    if len(candidate_tokens) != 1:
        return False
    candidate_roots = _predicate_roots(candidate_tokens[0])
    return any(
        candidate_roots & _predicate_roots(source_token)
        for source_token in _lexical_tokens(source)
    )


def _allows_implicit_you(source: str, semantic_role: str | None) -> bool:
    tokens = _lexical_tokens(source)
    if not tokens:
        return False
    if tokens[0] in {"do", "never", "always", "please"}:
        return True
    return (
        semantic_role in _ACTION_SEMANTIC_ROLES
        and tokens[0] in _IMPERATIVE_PREFIX_WORDS
    )


def _recover_predicate_from_source(
    source: str,
    *,
    semantic_role: str | None,
) -> str | None:
    """Conservative deterministic predicate fallback for rare empty extractions."""
    tokens = _lexical_tokens(source)
    if not tokens:
        return None

    # Imperatives such as "Do not transfer..." / "Never send...".
    if tokens[0] == "do":
        for token in tokens[1:]:
            if token not in _PREDICATE_SKIP_WORDS:
                return token
    if tokens[0] in {"never", "always", "please"} and len(tokens) > 1:
        for token in tokens[1:]:
            if token in _PREDICATE_SKIP_WORDS:
                continue
            return token
    if semantic_role in _ACTION_SEMANTIC_ROLES and tokens[0] in _IMPERATIVE_PREFIX_WORDS:
        return tokens[0]

    # Modal constructions: "X must use...", "Y may be...".
    for index, token in enumerate(tokens[:-1]):
        if token not in _AUXILIARY_OR_MODAL_WORDS:
            continue
        for following in tokens[index + 1 :]:
            if following in _PREDICATE_SKIP_WORDS:
                continue
            return following

    # Copular statements still have a valid relation/state anchor.
    for token in tokens:
        if token in _COPULA_FORMS:
            return _IRREGULAR_PREDICATE_LEMMAS.get(token, token)

    # Last conservative fallback: an overt participial verb form.
    for token in tokens:
        if len(token) > 4 and (token.endswith("ed") or token.endswith("ing")):
            return token
    return None


def _dedupe_lexical_values(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
    return cleaned


def _sanitize_atomic_payload(
    *,
    source: str,
    semantic_role: str | None,
    payload: PropositionPayload,
) -> tuple[PropositionPayload, dict[str, list[str]], bool]:
    """Deterministically filter one generated payload against authoritative text."""
    dropped: dict[str, list[str]] = {"subjects": [], "predicates": [], "objects": []}

    subjects: list[str] = []
    for value in _dedupe_lexical_values(payload.subjects):
        if _phrase_grounded(value, source) or (
            value.casefold() == "you" and _allows_implicit_you(source, semantic_role)
        ):
            subjects.append(value)
        else:
            dropped["subjects"].append(value)

    predicates: list[str] = []
    for value in _dedupe_lexical_values(payload.predicates):
        if _predicate_grounded(value, source):
            predicates.append(value)
        else:
            dropped["predicates"].append(value)

    objects: list[str] = []
    for value in _dedupe_lexical_values(payload.objects):
        if _phrase_grounded(value, source):
            objects.append(value)
        else:
            dropped["objects"].append(value)

    used_predicate_fallback = False
    if not predicates:
        fallback = _recover_predicate_from_source(
            source,
            semantic_role=semantic_role,
        )
        if fallback is not None and _predicate_grounded(fallback, source):
            predicates = [fallback]
            used_predicate_fallback = True

    return (
        PropositionPayload(
            subjects=subjects,
            predicates=predicates,
            objects=objects,
        ),
        dropped,
        used_predicate_fallback,
    )


_RETRIEVAL_BLANK = " "


def _blank_retrieval_payload() -> PropositionPayload:
    """Return zero-signal placeholders for unavailable S/P/O metadata."""
    return PropositionPayload(
        subjects=[_RETRIEVAL_BLANK],
        predicates=[_RETRIEVAL_BLANK],
        objects=[_RETRIEVAL_BLANK],
    )


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


# Cross-chunk augmentation is deliberately bounded. The slot-binding model is an
# identity matcher, not a global search engine; lexical pre-ranking keeps the
# number of extra LLM calls proportional to the new chunk rather than to the full
# accumulated graph.
_MAX_CROSS_CHUNK_AUGMENTATION_NODES = 16
_MIN_CROSS_CHUNK_AUGMENTATION_SCORE = 0.10


@dataclass
class _DraftAccumulator:
    nodes: list[DraftNode] = field(default_factory=list)
    hierarchy: list[DraftHierarchyEdge] = field(default_factory=list)
    relations: list[DraftRelation] = field(default_factory=list)
    logic_slots: list[DraftLogicSlot] = field(default_factory=list)
    logic_assertions: list[DraftLogicAssertion] = field(default_factory=list)
    logic_rules: list[DraftLogicRule] = field(default_factory=list)
    next_logic_slot_id: int = 0
    deferred_local_relations: list[dict[str, Any]] = field(default_factory=list)
    next_id: int = 0

    def allocate_id(self) -> str:
        temporary_id = f"local-{self.next_id}"
        self.next_id += 1
        return temporary_id

    def allocate_logic_slot_id(self) -> str:
        temporary_id = f"logic-slot-{self.next_logic_slot_id}"
        self.next_logic_slot_id += 1
        return temporary_id


def _skip_logic_enrichment(request: GraphBuildRequest) -> bool:
    """Whether this build is a retrieval-only semantic view.

    Candidate verification uses decomposition/S-P-O/embeddings only for
    retrieval. Logic normalization/audits are intentionally deferred until an
    accepted candidate is rebuilt for persistent STATE/reasoning promotion.
    """
    return bool(request.metadata.get("skip_logic_enrichment", False))


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

        # Default graph construction now attaches one local Qwen3 embedding per
        # final atomic leaf. The encoder itself remains lazy: constructing a
        # GraphBuilder does not load the ~0.6B model. Custom embedding adapters
        # still take precedence, and CUGA_RETRIEVAL_EMBEDDINGS=0 restores the
        # previous lexical/S-P-O-only behavior.
        if (
            embedding_callable is None
            and embedding_model_name is None
            and retrieval_embeddings_enabled()
        ):
            embedding_callable = embed_retrieval_texts_qwen
            embedding_model_name = qwen_embedding_model_name()

        if embedding_model_name is not None and not embedding_model_name.strip():
            raise ValueError("embedding_model_name cannot be empty")

        self._model_callable = model_callable
        self._validator = validator or DecompositionValidator()
        self._max_decomposition_depth = max_decomposition_depth
        self._embedding_callable = embedding_callable
        self._embedding_model_name = embedding_model_name

    def build(self, request: GraphBuildRequest) -> GraphBuildResult:
        if bool(request.metadata.get("candidate", False)):
            logger.info(
                "[PROMPT_VERIFIER_CANDIDATE_RAW] session_id={} source_id={} chars={}\n"
                "----- CANDIDATE RAW START -----\n{}\n"
                "----- CANDIDATE RAW END -----",
                request.session_id,
                request.source_id,
                len(request.content),
                request.content,
            )

        draft = self._build_hierarchical_draft(request)
        validation = self._validator.validate(request=request, draft=draft)

        if not validation.accepted:
            raise InvalidDecompositionError(validation)

        result = self._materialize(
            request=request,
            draft=draft,
            validation=validation,
        )

        if bool(request.metadata.get("candidate", False)):
            self._log_candidate_decomposition_trace(request=request, result=result)

        return self._attach_retrieval_embeddings(result)

    @staticmethod
    def _log_candidate_decomposition_trace(
        *,
        request: GraphBuildRequest,
        result: GraphBuildResult,
    ) -> None:
        """Log verifier candidate decomposition with stable/canonical node IDs.

        The prompt verifier reports verdicts using materialized UUID node IDs, so
        logging only construction-time ``local-*`` IDs is not enough to diagnose
        a rejection after the fact.  Keep this trace candidate-only and emit it
        after materialization so every atomic statement can be joined directly to
        the corresponding verifier ``Atom verdict`` line.
        """

        node_by_id = {node.id: node for node in result.nodes}
        atomic_nodes = [
            node for node in result.nodes if node.kind == NodeKind.ATOMIC_FACT
        ]

        logger.info(
            "[PROMPT_VERIFIER_CANDIDATE_DECOMPOSITION] "
            "session_id={} source_id={} atomic_count={} semantic_node_count={}",
            request.session_id,
            request.source_id,
            len(atomic_nodes),
            max(0, len(result.nodes) - 1),
        )

        for index, node in enumerate(atomic_nodes):
            source_spans = [
                (source_ref.span.start, source_ref.span.end)
                for source_ref in node.source_refs
                if source_ref.span is not None
            ]
            parent_contexts = []
            for parent_id in node.support_node_ids:
                parent = node_by_id.get(parent_id)
                if parent is None:
                    parent_contexts.append(
                        {"id": parent_id, "kind": "unknown", "content": None}
                    )
                    continue
                parent_contexts.append(
                    {
                        "id": parent.id,
                        "kind": parent.kind.value,
                        "content": parent.content,
                    }
                )

            logger.info(
                "[PROMPT_VERIFIER_CANDIDATE_ATOM] "
                "index={} node_id={} temporary_id={} depth={} "
                "semantic_role={} logic_asserted={} source_spans={} "
                "parent_contexts={} content={!r}",
                index,
                node.id,
                node.metadata.get("temporary_id"),
                node.depth,
                node.metadata.get("semantic_role"),
                node.logic_asserted,
                source_spans,
                parent_contexts,
                node.content,
            )

    def _build_hierarchical_draft(
        self,
        request: GraphBuildRequest,
    ) -> DecompositionDraft:
        accumulator = _DraftAccumulator()

        # Active decomposition path: exact deterministic source partitioning +
        # local Stanza/DeDisCo recursion. No LLM contextualization or decomposition
        # audit is used here; every semantic node is derived from source text or a
        # deterministic Stanza reconstruction guarded by semantic closure.
        source_chunks = split_source_for_decomposition(request.content)

        for chunk_index, chunk in enumerate(source_chunks):
            prior_node_ids = {node.temporary_id for node in accumulator.nodes}
            prior_slot_ids = {slot.temporary_id for slot in accumulator.logic_slots}

            primary_span = SourceSpan(start=chunk.start, end=chunk.end)
            authoritative_spans = [primary_span]

            self._expand_statement(
                source_request=request,
                statement=chunk.text,
                statement_source_spans=authoritative_spans,
                semantic_role=None,
                statement_metadata={
                    "source_contextualization": "identity",
                    "decomposition_pipeline": (
                        "stanza_dedisco_v12_retrieval_only_asymmetric_guard"
                    ),
                    "contextual_chunk_index": chunk_index,
                    "primary_source_span": primary_span.model_dump(mode="json"),
                    "context_source_spans": [],
                    "authoritative_source_span_count": 1,
                    "derived_contextualized_text": False,
                },
                parent_temporary_id=None,
                depth=0,
                ancestors=frozenset(),
                accumulator=accumulator,
            )

            # Reuse the existing identity-only logic-slot augmentation operation
            # across chunk boundaries. Local logic extraction never needs the full
            # source: new atoms may resolve slots created by earlier chunks, and
            # older atoms may resolve slots introduced by this chunk. This is the
            # only inter-chunk reconciliation step; it does not rediscover or
            # rewrite already-valid local rules.
            new_atomic_ids = {
                node.temporary_id
                for node in accumulator.nodes
                if node.temporary_id not in prior_node_ids
                and node.kind == NodeKind.ATOMIC_FACT
            }
            new_slot_ids = {
                slot.temporary_id
                for slot in accumulator.logic_slots
                if slot.temporary_id not in prior_slot_ids
            }
            if _skip_logic_enrichment(request):
                augmented_bindings = 0
                coalesced_slots = 0
                deduped_assertions = 0
                deduped_rules = 0
                deduped_relations = _dedupe_draft_relations(accumulator)
            else:
                augmented_bindings = self._augment_logic_across_chunk_boundary(
                    accumulator=accumulator,
                    new_atomic_ids=new_atomic_ids,
                    new_slot_ids=new_slot_ids,
                )
                coalesced_slots = _coalesce_equivalent_logic_slots(accumulator)
                deduped_assertions, deduped_rules = _dedupe_draft_logic_constraints(
                    accumulator
                )
                deduped_relations = _dedupe_draft_relations(accumulator)
            logger.info(
                "Structure chunk integrated: source_id={} chunk={} atoms={} new_slots={} "
                "cross_chunk_bindings={} coalesced_slots={} deduped_assertions={} "
                "deduped_rules={} deduped_relations={} total_relations={} "
                "total_slots={} assertions={} rules={}",
                request.source_id,
                chunk_index,
                len(new_atomic_ids),
                len(new_slot_ids),
                augmented_bindings,
                coalesced_slots,
                deduped_assertions,
                deduped_rules,
                deduped_relations,
                len(accumulator.relations),
                len(accumulator.logic_slots),
                len(accumulator.logic_assertions),
                len(accumulator.logic_rules),
            )

        populated_payloads = self._populate_final_atomic_payloads(
            source_request=request,
            accumulator=accumulator,
        )

        return DecompositionDraft(
            nodes=accumulator.nodes,
            hierarchy=accumulator.hierarchy,
            relations=accumulator.relations,
            logic_slots=accumulator.logic_slots,
            logic_assertions=accumulator.logic_assertions,
            logic_rules=accumulator.logic_rules,
            metadata={
                "construction": (
                    "stanza_dedisco_v12_chunked"
                    if len(source_chunks) > 1
                    else "stanza_dedisco_v12"
                ),
                "decomposition_pipeline": (
                    "stanza_dedisco_v12_retrieval_only_asymmetric_guard"
                ),
                "source_id": request.source_id,
                "source_chunk_count": len(source_chunks),
                "source_chunks": [
                    {
                        "index": index,
                        "primary_start": chunk.start,
                        "primary_end": chunk.end,
                        "primary_chars": chunk.end - chunk.start,
                        "context_spans": [],
                        "context_count": 0,
                        "contextualized_chars": len(chunk.text),
                    }
                    for index, chunk in enumerate(source_chunks)
                ],
                "deferred_local_relations": accumulator.deferred_local_relations,
                "post_tree_atomic_payload_extractions": populated_payloads,
            },
        )

    def _populate_final_atomic_payloads(
        self,
        *,
        source_request: GraphBuildRequest,
        accumulator: _DraftAccumulator,
    ) -> int:
        """Populate final atomic S/P/O without allowing metadata failure to abort.

        Retrieval-only literal/code/path leaves bypass spaCy and receive the
        caller-requested blank placeholder fields. Ordinary propositions still
        use local spaCy plus deterministic grounding/fallback. If that path
        unexpectedly cannot recover a predicate, the node is preserved with the
        same placeholders rather than failing the entire authority build.
        """
        children_by_parent: dict[str, list[str]] = defaultdict(list)
        for edge in accumulator.hierarchy:
            if edge.parent_temporary_id is not None:
                children_by_parent[edge.parent_temporary_id].append(
                    edge.child_temporary_id
                )

        atomic_leaves: list[DraftNode] = []
        skipped_composites = 0
        for node in accumulator.nodes:
            has_children = bool(children_by_parent.get(node.temporary_id))
            if has_children:
                if node.kind != NodeKind.COMPOSITE:
                    raise InvalidModelOutputError(
                        "Atomic draft node unexpectedly has semantic children after "
                        "recursive construction for source_id={} temporary_id={}".format(
                            source_request.source_id,
                            node.temporary_id,
                        )
                    )
                node.proposition = None
                skipped_composites += 1
                continue

            if node.kind != NodeKind.ATOMIC_FACT:
                raise InvalidModelOutputError(
                    "Composite draft node has no semantic children after recursive "
                    "construction for source_id={} temporary_id={}".format(
                        source_request.source_id,
                        node.temporary_id,
                    )
                )
            node.proposition = None
            atomic_leaves.append(node)

        retrieval_only_leaves = [
            node for node in atomic_leaves
            if bool(node.metadata.get("retrieval_only", False))
        ]
        proposition_leaves = [
            node for node in atomic_leaves
            if not bool(node.metadata.get("retrieval_only", False))
        ]

        for node in retrieval_only_leaves:
            node.proposition = _blank_retrieval_payload()
            node.metadata["spo_placeholder"] = True
            node.metadata["spo_placeholder_reason"] = "retrieval_only_terminal"

        leaves_payload = [
            {
                "temporary_id": node.temporary_id,
                "content": node.content,
                "routing_text": node.routing_text,
                "semantic_role": node.metadata.get("semantic_role"),
            }
            for node in proposition_leaves
        ]
        extracted = (
            extract_atomic_payloads_spacy(
                source_request,
                leaves=leaves_payload,
            )
            if leaves_payload
            else {}
        )

        fallback_count = 0
        dropped_count = 0
        unexpected_placeholder_count = 0
        for node in proposition_leaves:
            candidate = extracted[node.temporary_id]
            semantic_role = node.metadata.get("semantic_role")
            sanitized, dropped, used_fallback = _sanitize_atomic_payload(
                source=node.content,
                semantic_role=(
                    str(semantic_role) if semantic_role is not None else None
                ),
                payload=candidate,
            )
            dropped_values = sum(len(values) for values in dropped.values())
            dropped_count += dropped_values
            fallback_count += int(used_fallback)

            if dropped_values and memory_graph_trace_enabled():
                logger.info(
                    "Deterministic atomic payload grounding filtered parser candidates: "
                    "source_id={} temporary_id={} dropped={}",
                    source_request.source_id,
                    node.temporary_id,
                    dropped,
                )

            if not sanitized.predicates:
                unexpected_placeholder_count += 1
                node.proposition = _blank_retrieval_payload()
                node.metadata["spo_placeholder"] = True
                node.metadata["spo_placeholder_reason"] = (
                    "no_grounded_predicate_after_spacy_and_fallback"
                )
                logger.warning(
                    "Post-tree spaCy S/P/O produced no grounded predicate; "
                    "preserving node with blank retrieval placeholders instead of "
                    "failing graph construction: source_id={} temporary_id={} "
                    "content={!r}",
                    source_request.source_id,
                    node.temporary_id,
                    node.content,
                )
                continue

            node.proposition = sanitized

        logger.info(
            "Post-tree atomic S/P/O extraction complete: source_id={} leaves={} "
            "extractor=en_core_web_trf skipped_nodes_with_children={} "
            "retrieval_only_placeholders={} unexpected_spo_placeholders={} "
            "dropped_ungrounded={} predicate_fallbacks={}",
            source_request.source_id,
            len(atomic_leaves),
            skipped_composites,
            len(retrieval_only_leaves),
            unexpected_placeholder_count,
            dropped_count,
            fallback_count,
        )
        return len(atomic_leaves)

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
                    **(
                        {"semantic_role": semantic_role}
                        if semantic_role is not None
                        else {}
                    ),
                },
            }
        )

        decision = self._parse_local_model_output(
            self._model_callable(local_request)
        )

        temporary_id = accumulator.allocate_id()

        if decision.kind == "atomic":
            terminal_class, terminal_reasons = classify_terminal_for_retrieval(
                statement
            )
            retrieval_only = (
                terminal_class.startswith("retrieval_only:")
                or terminal_class == "structural_artifact"
            )
            terminal_metadata: dict[str, Any] = {
                "terminal_class": terminal_class,
            }
            if terminal_reasons:
                terminal_metadata["terminal_class_reasons"] = list(terminal_reasons)
            if retrieval_only:
                terminal_metadata.update(
                    {
                        "retrieval_only": True,
                        "non_propositional": True,
                    }
                )
            if terminal_class == "structural_artifact":
                terminal_metadata["structural_artifact"] = True

            draft_node = DraftNode(
                temporary_id=temporary_id,
                kind=NodeKind.ATOMIC_FACT,
                content=statement,
                routing_text=decision.routing_text,
                source_spans=_dedupe_spans(statement_source_spans),
                proposition=None,
                metadata={
                    **(statement_metadata or {}),
                    **(
                        {"semantic_role": semantic_role}
                        if semantic_role is not None
                        else {}
                    ),
                    **terminal_metadata,
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
            if (
                not draft_node.metadata.get("retrieval_only", False)
                and not _skip_logic_enrichment(source_request)
            ):
                self._extract_logic_for_statement(
                    source_request=source_request,
                    statement=statement,
                    statement_source_spans=statement_source_spans,
                    parent_temporary_id=temporary_id,
                    depth=depth,
                    accumulator=accumulator,
                )
            elif _skip_logic_enrichment(source_request):
                logger.debug(
                    "Skipping logic enrichment for retrieval-only candidate build: "
                    "source_id={} temporary_id={} preview={!r}",
                    source_request.source_id,
                    temporary_id,
                    statement[:180],
                )
            else:
                logger.debug(
                    "Skipping logic normalization for retrieval-only terminal: "
                    "source_id={} temporary_id={} class={} preview={!r}",
                    source_request.source_id,
                    temporary_id,
                    draft_node.metadata.get("terminal_class"),
                    statement[:180],
                )
            return temporary_id

        next_ancestors = ancestors | {statement_key}
        # Surface text is not semantic identity. Track same-looking direct
        # children together with their resolved source occurrence so we only
        # coalesce the narrow case that is provably duplicate model output:
        # identical child meaning grounded to the same single exact occurrence.
        # Repeated wording with different/ambiguous provenance remains separate.
        seen_children: dict[
            str,
            list[tuple[str, tuple[int, int] | None]],
        ] = defaultdict(list)
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

            child_source_spans, child_provenance_exact = _resolve_excerpt_to_root_spans(
                root_content=source_request.content,
                parent_statement=statement,
                parent_source_spans=statement_source_spans,
                excerpt=child.source_text,
            )
            occurrence_key = _single_exact_span_key(
                child_source_spans,
                exact=child_provenance_exact,
            )

            duplicate_temporary_id: str | None = None
            for prior_temporary_id, prior_occurrence_key in seen_children.get(
                child_key,
                [],
            ):
                if (
                    occurrence_key is not None
                    and prior_occurrence_key is not None
                    and occurrence_key == prior_occurrence_key
                ):
                    duplicate_temporary_id = prior_temporary_id
                    break

            if duplicate_temporary_id is not None:
                # Preserve the model's original child-index namespace so any
                # local relation referencing this duplicate index still resolves
                # deterministically to the canonical occurrence.
                direct_child_ids.append(duplicate_temporary_id)
                logger.warning(
                    "Coalescing duplicate direct child from the same exact source "
                    "occurrence: source_id={} parent={} child_index={} existing_child={} "
                    "span={} preview={!r}",
                    source_request.source_id,
                    temporary_id,
                    child_index,
                    duplicate_temporary_id,
                    occurrence_key,
                    child_text[:200],
                )
                continue

            repeated_surface_form = bool(seen_children.get(child_key))
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
            seen_children[child_key].append(
                (child_temporary_id, occurrence_key)
            )

            metadata_updates: dict[str, Any] = {}
            if not child_provenance_exact:
                metadata_updates.update(
                    {
                        "source_provenance_precision": "inherited_parent_span",
                        "immediate_source_text": child.source_text,
                    }
                )
            if repeated_surface_form:
                metadata_updates.update(
                    {
                        "repeated_surface_form": True,
                        "occurrence_identity": "preserved_distinct",
                    }
                )
                logger.info(
                    "Preserving same-looking direct child as a distinct semantic "
                    "occurrence: source_id={} parent={} child_index={} span={} "
                    "preview={!r}",
                    source_request.source_id,
                    temporary_id,
                    child_index,
                    occurrence_key,
                    child_text[:200],
                )

            if metadata_updates:
                _merge_node_metadata(
                    accumulator=accumulator,
                    temporary_id=child_temporary_id,
                    updates=metadata_updates,
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

            if relation_source_id == relation_target_id:
                logger.warning(
                    "Ignoring local relation whose child indices coalesced to the "
                    "same semantic occurrence: source_id={} parent={} relation_index={} "
                    "relation={}",
                    source_request.source_id,
                    temporary_id,
                    relation_index,
                    relation.relation.value,
                )
                continue

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
                        "relation_metadata": dict(relation.metadata),
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
                        **dict(relation.metadata),
                    },
                )
            )

        if not _skip_logic_enrichment(source_request):
            self._extract_logic_for_statement(
                source_request=source_request,
                statement=statement,
                statement_source_spans=statement_source_spans,
                parent_temporary_id=temporary_id,
                depth=depth,
                accumulator=accumulator,
            )
        return temporary_id

    def _extract_logic_for_statement(
        self,
        *,
        source_request: GraphBuildRequest,
        statement: str,
        statement_source_spans: list[SourceSpan],
        parent_temporary_id: str,
        depth: int,
        accumulator: _DraftAccumulator,
    ) -> None:
        """Normalize source-explicit relations and sparse compound logic locally.

        ``call_logic_structure_model`` performs a cheap lexical gate before any LLM
        call. The model only normalizes the statement's natural-language
        relationships/operators. Python routes source-explicit binary relations
        (including reducible IMPLIES) to the semantic relation layer and keeps only
        irreducible/unresolved Boolean structure in the logic layer. Normalization
        failure is fail-soft and never invalidates the semantic subtree.
        """
        proposition_nodes = _atomic_descendants(
            accumulator,
            parent_temporary_id,
        )
        propositions = [
            LogicPropositionCandidate(
                proposition_index=index,
                temporary_id=node.temporary_id,
                content=node.content,
                routing_text=node.routing_text,
                semantic_role=(
                    str(node.metadata.get("semantic_role"))
                    if node.metadata.get("semantic_role") is not None
                    else None
                ),
            )
            for index, node in enumerate(proposition_nodes)
        ]

        parent_node = _draft_node_by_id(accumulator, parent_temporary_id)
        parent_semantic_role = parent_node.metadata.get("semantic_role")
        semantic_logic_signal = (
            parent_semantic_role == "condition"
            or (
                len(propositions) >= 2
                and any(
                    proposition.semantic_role == "condition"
                    for proposition in propositions
                )
            )
        )

        local_request = source_request.model_copy(
            update={
                "source_id": (
                    f"{source_request.source_id}::logic-node-{parent_temporary_id}"
                ),
                "content": statement,
                "metadata": {
                    **source_request.metadata,
                    "logic_pass": True,
                    "logic_source_id": source_request.source_id,
                    "logic_parent_temporary_id": parent_temporary_id,
                    "decomposition_depth": depth,
                    "logic_semantic_signal": semantic_logic_signal,
                    "logic_fail_soft": True,
                },
            }
        )
        structure = call_logic_structure_model(local_request, propositions)
        if not structure.relations and not (
            structure.logic.slots
            or structure.logic.expressions
            or structure.logic.assertions
            or structure.logic.rules
        ):
            return

        if structure.relations:
            _append_normalized_relations(
                source_request=source_request,
                statement=statement,
                statement_source_spans=statement_source_spans,
                parent_temporary_id=parent_temporary_id,
                propositions=propositions,
                relations=list(structure.relations),
                accumulator=accumulator,
            )

        if (
            structure.logic.slots
            or structure.logic.expressions
            or structure.logic.assertions
            or structure.logic.rules
        ):
            _append_local_logic_decision(
                source_request=source_request,
                statement=statement,
                statement_source_spans=statement_source_spans,
                parent_temporary_id=parent_temporary_id,
                propositions=propositions,
                decision=structure.logic,
                accumulator=accumulator,
            )

    def _augment_logic_across_chunk_boundary(
        self,
        *,
        accumulator: _DraftAccumulator,
        new_atomic_ids: set[str],
        new_slot_ids: set[str],
    ) -> int:
        """Resolve cross-chunk slot identity using the runtime augmentation model.

        Local logic units stay local. At each chunk boundary we only perform the
        already-defined semantic-identity operation in both directions:

        * new atomic propositions -> slots accumulated from earlier chunks;
        * earlier atomic propositions -> slots introduced by the new chunk.

        No implication, arithmetic, temporal reasoning, or rule discovery happens
        here. The operation merely resolves placeholders that two independently
        parsed chunks use for the same proposition.
        """
        if not accumulator.logic_slots:
            return 0

        node_by_id = {node.temporary_id: node for node in accumulator.nodes}
        slot_by_id = {slot.temporary_id: slot for slot in accumulator.logic_slots}
        prior_atomic_ids = {
            node.temporary_id
            for node in accumulator.nodes
            if node.kind == NodeKind.ATOMIC_FACT
            and node.temporary_id not in new_atomic_ids
        }
        prior_slot_ids = set(slot_by_id) - new_slot_ids

        changed = 0

        def bind_nodes_to_slots(
            node_ids: set[str],
            candidate_slot_ids: set[str],
        ) -> None:
            nonlocal changed
            if not node_ids or not candidate_slot_ids:
                return

            slot_texts = [
                slot_by_id[slot_id].source_text
                for slot_id in candidate_slot_ids
                if slot_id in slot_by_id
            ]
            ranked_node_ids: list[tuple[float, str]] = []
            for node_id in node_ids:
                node = node_by_id.get(node_id)
                if node is None or node.kind != NodeKind.ATOMIC_FACT:
                    continue
                node_text = node.routing_text or node.content
                best_score = max(
                    (logic_slot_match_score(node_text, slot_text) for slot_text in slot_texts),
                    default=0.0,
                )
                if best_score >= _MIN_CROSS_CHUNK_AUGMENTATION_SCORE:
                    ranked_node_ids.append((best_score, node_id))

            ranked_node_ids.sort(key=lambda item: (-item[0], item[1]))
            selected_node_ids = [
                node_id
                for _, node_id in ranked_node_ids[:_MAX_CROSS_CHUNK_AUGMENTATION_NODES]
            ]

            for node_id in selected_node_ids:
                node = node_by_id.get(node_id)
                if node is None or node.kind != NodeKind.ATOMIC_FACT:
                    continue
                candidates = [
                    LogicSlotCandidate(
                        slot_id=slot_id,
                        source_text=slot_by_id[slot_id].source_text,
                        bound_node_ids=list(
                            slot_by_id[slot_id].bound_semantic_temporary_ids
                        ),
                        context_paths=_draft_slot_context_paths(
                            accumulator,
                            slot_by_id[slot_id],
                        ),
                    )
                    for slot_id in sorted(candidate_slot_ids)
                ]
                decisions = match_logic_node_to_slot_candidates(
                    node_id=node.temporary_id,
                    node_content=node.content,
                    node_routing_text=node.routing_text,
                    node_context_paths=_draft_node_context_paths(
                        accumulator,
                        node.temporary_id,
                    ),
                    candidates=candidates,
                )
                for decision in decisions:
                    slot = slot_by_id.get(decision.slot_id)
                    if slot is None:
                        continue
                    existing = next(
                        (
                            binding
                            for binding in slot.bindings
                            if binding.semantic_temporary_id == node.temporary_id
                        ),
                        None,
                    )
                    if existing is not None:
                        if existing.value != decision.value:
                            raise InvalidModelOutputError(
                                "Cross-chunk logic augmentation produced conflicting "
                                f"polarity for slot={slot.temporary_id} "
                                f"node={node.temporary_id}"
                            )
                        continue
                    slot.bindings.append(
                        DraftLogicSlotBinding(
                            semantic_temporary_id=node.temporary_id,
                            value=decision.value,
                        )
                    )
                    slot.metadata = {
                        **slot.metadata,
                        "cross_chunk_augmented": True,
                    }
                    changed += 1

        bind_nodes_to_slots(new_atomic_ids, prior_slot_ids)
        bind_nodes_to_slots(prior_atomic_ids, new_slot_ids)
        return changed

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
                    logic_asserted=draft_node.logic_asserted,
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

        logic_slot_ids = {
            slot.temporary_id: str(uuid4())
            for slot in draft.logic_slots
        }
        logic_slots: list[LogicSlot] = []
        for draft_slot in draft.logic_slots:
            source_refs = [
                SourceReference(
                    source_id=request.source_id,
                    source_type=request.source_type,
                    span=span,
                    turn_id=request.source_id,
                )
                for span in draft_slot.evidence_spans
            ]
            slot_metadata = dict(draft_slot.metadata)
            logic_parent_temporary_id = slot_metadata.pop(
                "logic_parent_temporary_id",
                None,
            )
            if logic_parent_temporary_id in temporary_to_canonical:
                slot_metadata["logic_parent_node_id"] = temporary_to_canonical[
                    logic_parent_temporary_id
                ]
            logic_slots.append(
                LogicSlot(
                    id=logic_slot_ids[draft_slot.temporary_id],
                    source_text=draft_slot.source_text,
                    bindings=[
                        LogicSlotBinding(
                            node_id=temporary_to_canonical[binding.semantic_temporary_id],
                            value=binding.value,
                        )
                        for binding in draft_slot.bindings
                    ],
                    source_refs=source_refs,
                    confidence=draft_slot.confidence,
                    metadata=slot_metadata,
                )
            )

        literal_assertions: list[LogicLiteralAssertion] = []
        compound_assertions: list[LogicCompoundAssertion] = []
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
            literal = _draft_logic_literal(draft_assertion.root)
            common = {
                "parent_node_id": temporary_to_canonical[
                    draft_assertion.parent_temporary_id
                ],
                "source_refs": source_refs,
                "confidence": draft_assertion.confidence,
            }
            if literal is not None:
                slot_temp_id, value = literal
                literal_assertions.append(
                    LogicLiteralAssertion(
                        literal=LogicLiteral(
                            slot_id=logic_slot_ids[slot_temp_id],
                            value=value,
                        ),
                        metadata={
                            **draft_assertion.metadata,
                            "logic_representation": "literal_assertion",
                        },
                        **common,
                    )
                )
            else:
                compound_assertions.append(
                    LogicCompoundAssertion(
                        root=_materialize_logic_expression(
                            draft_assertion.root,
                            logic_slot_ids,
                        ),
                        metadata={
                            **draft_assertion.metadata,
                            "logic_representation": "compound_ast",
                        },
                        **common,
                    )
                )

        logic_relations: list[LogicRelation] = []
        compound_rules: list[LogicCompoundRule] = []
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
            condition_literal = _draft_logic_literal(draft_rule.condition)
            effect_literal = _draft_logic_literal(draft_rule.effect)
            common = {
                "parent_node_id": temporary_to_canonical[
                    draft_rule.parent_temporary_id
                ],
                "source_refs": source_refs,
                "confidence": draft_rule.confidence,
            }
            if condition_literal is not None and effect_literal is not None:
                condition_slot, condition_value = condition_literal
                effect_slot, effect_value = effect_literal
                logic_relations.append(
                    LogicRelation(
                        relation=LogicRelationType.IMPLIES,
                        antecedent=LogicLiteral(
                            slot_id=logic_slot_ids[condition_slot],
                            value=condition_value,
                        ),
                        consequent=LogicLiteral(
                            slot_id=logic_slot_ids[effect_slot],
                            value=effect_value,
                        ),
                        metadata={
                            **draft_rule.metadata,
                            "logic_representation": "simple_relation",
                        },
                        **common,
                    )
                )
            else:
                compound_rules.append(
                    LogicCompoundRule(
                        condition=_materialize_logic_expression(
                            draft_rule.condition,
                            logic_slot_ids,
                        ),
                        effect=_materialize_logic_expression(
                            draft_rule.effect,
                            logic_slot_ids,
                        ),
                        metadata={
                            **draft_rule.metadata,
                            "logic_representation": "compound_ast",
                        },
                        **common,
                    )
                )

        return GraphBuildResult(
            root_id=root.id,
            nodes=nodes,
            edges=edges,
            logic_layer=LogicLayer(
                slots=logic_slots,
                literal_assertions=literal_assertions,
                relations=logic_relations,
                compound_assertions=compound_assertions,
                compound_rules=compound_rules,
            ),
            validation=validation,
        )

    def _attach_retrieval_embeddings(
        self,
        result: GraphBuildResult,
    ) -> GraphBuildResult:
        """Attach one cached retrieval embedding to every atomic node.

        Embeddings are generated in local batches immediately after the semantic
        tree has received its final spaCy S/P/O payloads, validation has passed,
        and canonical graph nodes have been materialized. RAW_SOURCE and
        COMPOSITE nodes remain unembedded because verifier retrieval operates
        over atomic propositions only.

        By default GraphBuilder wires the local Qwen3-Embedding-0.6B adapter.
        Set CUGA_RETRIEVAL_EMBEDDINGS=0 to disable it; when disabled (or when a
        caller deliberately supplies no adapter) retrieval falls back to the
        normalized lexical/S-P-O score.
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


def _atomic_descendants(
    accumulator: _DraftAccumulator,
    parent_temporary_id: str,
) -> list[DraftNode]:
    """Return atomic descendants in deterministic decomposition order."""
    node_by_id = {node.temporary_id: node for node in accumulator.nodes}
    parent = node_by_id[parent_temporary_id]
    if parent.kind == NodeKind.ATOMIC_FACT:
        return [parent]

    children: dict[str, list[str]] = defaultdict(list)
    for edge in accumulator.hierarchy:
        if edge.parent_temporary_id is not None:
            children[edge.parent_temporary_id].append(edge.child_temporary_id)

    descendant_ids: set[str] = set()
    stack = list(children.get(parent_temporary_id, []))
    while stack:
        node_id = stack.pop()
        if node_id in descendant_ids:
            continue
        descendant_ids.add(node_id)
        stack.extend(children.get(node_id, []))

    return [
        node
        for node in accumulator.nodes
        if node.temporary_id in descendant_ids
        and node.kind == NodeKind.ATOMIC_FACT
    ]


def _append_normalized_relations(
    *,
    source_request: GraphBuildRequest,
    statement: str,
    statement_source_spans: list[SourceSpan],
    parent_temporary_id: str,
    propositions: list[LogicPropositionCandidate],
    relations: list[LocalNormalizedRelation],
    accumulator: _DraftAccumulator,
) -> None:
    """Materialize normalized binary structure directly as draft graph edges."""
    proposition_by_index = {
        item.proposition_index: item
        for item in propositions
    }
    parent_node = _draft_node_by_id(accumulator, parent_temporary_id)
    contextual_chunk_index = parent_node.metadata.get("contextual_chunk_index")

    for relation_index, relation in enumerate(relations):
        source_proposition = proposition_by_index.get(
            relation.source_proposition_index
        )
        target_proposition = proposition_by_index.get(
            relation.target_proposition_index
        )
        if source_proposition is None or target_proposition is None:
            raise InvalidModelOutputError(
                "Normalized relation references proposition index outside the "
                "supplied semantic catalog"
            )

        if source_proposition.temporary_id == target_proposition.temporary_id:
            # Schema validation should already reject this by proposition index,
            # but keep the graph invariant local too in case two catalog entries
            # ever alias the same semantic occurrence.
            logger.warning(
                "Ignoring normalized relation self-edge: source_id={} parent={} "
                "relation={} proposition_index={} target_index={}",
                source_request.source_id,
                parent_temporary_id,
                relation.relation.value,
                relation.source_proposition_index,
                relation.target_proposition_index,
            )
            continue

        # A source-explicit implication/prerequisite describes a rule between
        # propositions; its operand nodes are not thereby asserted true. This
        # preserves the old logic-rule behavior if those semantic nodes later
        # bind to slots used by compound SAT reasoning.
        if relation.relation in {
            RelationType.IMPLIES,
            RelationType.REQUIRES,
        }:
            _draft_node_by_id(
                accumulator,
                source_proposition.temporary_id,
            ).logic_asserted = False
            _draft_node_by_id(
                accumulator,
                target_proposition.temporary_id,
            ).logic_asserted = False

        evidence_spans, exact = _resolve_excerpt_to_root_spans(
            root_content=source_request.content,
            parent_statement=statement,
            parent_source_spans=statement_source_spans,
            excerpt=relation.evidence_text,
        )
        accumulator.relations.append(
            DraftRelation(
                source_temporary_id=source_proposition.temporary_id,
                target_temporary_id=target_proposition.temporary_id,
                relation=relation.relation,
                directed=relation.relation not in _SYMMETRIC_RELATIONS,
                origin=RelationOrigin.SOURCE_EXPLICIT,
                evidence_spans=evidence_spans,
                confidence=relation.confidence,
                metadata={
                    "construction": "normalized_source_relation",
                    "normalization_relation_index": relation_index,
                    "logic_parent_temporary_id": parent_temporary_id,
                    "contextual_chunk_index": contextual_chunk_index,
                    "immediate_evidence_text": relation.evidence_text,
                    "source_provenance_precision": (
                        "exact" if exact else "inherited_parent_span"
                    ),
                },
            )
        )


def _append_local_logic_decision(
    *,
    source_request: GraphBuildRequest,
    statement: str,
    statement_source_spans: list[SourceSpan],
    parent_temporary_id: str,
    propositions: list[LogicPropositionCandidate],
    decision: LocalLogicDecision,
    accumulator: _DraftAccumulator,
) -> None:
    """Materialize one partial local AST without creating semantic nodes."""
    proposition_by_index = {
        item.proposition_index: item
        for item in propositions
    }
    parent_node = _draft_node_by_id(accumulator, parent_temporary_id)
    contextual_chunk_index = parent_node.metadata.get("contextual_chunk_index")
    slot_temp_by_local: dict[int, str] = {}
    bound_node_by_local_slot: dict[int, tuple[str, bool]] = {}

    for slot in decision.slots:
        slot_temp_id = accumulator.allocate_logic_slot_id()
        slot_temp_by_local[slot.slot_id] = slot_temp_id

        bindings: list[DraftLogicSlotBinding] = []
        if slot.proposition_index is not None:
            proposition = proposition_by_index.get(slot.proposition_index)
            if proposition is None:
                raise InvalidModelOutputError(
                    "Logical AST references proposition_index outside the supplied "
                    f"catalog: {slot.proposition_index}"
                )
            bindings.append(
                DraftLogicSlotBinding(
                    semantic_temporary_id=proposition.temporary_id,
                    value=slot.proposition_value,
                )
            )
            bound_node_by_local_slot[slot.slot_id] = (
                proposition.temporary_id,
                slot.proposition_value,
            )

        slot_spans, slot_exact = _resolve_excerpt_to_root_spans(
            root_content=source_request.content,
            parent_statement=statement,
            parent_source_spans=statement_source_spans,
            excerpt=slot.source_text,
        )
        accumulator.logic_slots.append(
            DraftLogicSlot(
                temporary_id=slot_temp_id,
                source_text=slot.source_text,
                bindings=bindings,
                evidence_spans=slot_spans,
                confidence=slot.confidence,
                metadata={
                    "construction": "partial_logic_slot",
                    "local_slot_id": slot.slot_id,
                    "contextual_chunk_index": contextual_chunk_index,
                    "logic_parent_temporary_id": parent_temporary_id,
                    "source_provenance_precision": (
                        "exact" if slot_exact else "inherited_parent_span"
                    ),
                    "initially_resolved": bool(bindings),
                },
            )
        )

    expression_by_id = {
        node.expression_id: node
        for node in decision.expressions
    }

    def materialize_ref(ref: LocalLogicOperand) -> DraftLogicExpression:
        if ref.slot_id is not None:
            try:
                slot_temp_id = slot_temp_by_local[ref.slot_id]
            except KeyError as exc:
                raise InvalidModelOutputError(
                    f"Logical AST references unknown slot_id={ref.slot_id}"
                ) from exc
            return DraftLogicExpression(
                slot_temporary_id=slot_temp_id,
                slot_value=ref.value,
            )

        if ref.expression_id is None:
            raise InvalidModelOutputError("Logical AST operand has no reference")
        try:
            node = expression_by_id[ref.expression_id]
        except KeyError as exc:
            raise InvalidModelOutputError(
                f"Logical AST references unknown expression_id={ref.expression_id}"
            ) from exc
        return DraftLogicExpression(
            operator=node.operator,
            threshold=node.threshold,
            operands=[materialize_ref(operand) for operand in node.operands],
        )

    def slot_ids_in_ref(ref: LocalLogicOperand) -> set[int]:
        if ref.slot_id is not None:
            return {ref.slot_id}
        if ref.expression_id is None:
            return set()
        node = expression_by_id[ref.expression_id]
        result: set[int] = set()
        for operand in node.operands:
            result.update(slot_ids_in_ref(operand))
        return result

    # A node used only as a rule term exists semantically, but its existence must
    # not be interpreted as a Boolean truth assignment. Explicit assertions below
    # re-establish truth when the source independently asserts the same term.
    rule_slot_ids: set[int] = set()
    for rule in decision.rules:
        rule_slot_ids.update(slot_ids_in_ref(rule.condition))
        rule_slot_ids.update(slot_ids_in_ref(rule.effect))
    for local_slot_id in rule_slot_ids:
        bound = bound_node_by_local_slot.get(local_slot_id)
        if bound is None:
            continue
        bound_temp_id, _ = bound
        node = _draft_node_by_id(accumulator, bound_temp_id)
        node.logic_asserted = False

    for assertion_index, assertion in enumerate(decision.assertions):
        evidence_spans, exact = _resolve_excerpt_to_root_spans(
            root_content=source_request.content,
            parent_statement=statement,
            parent_source_spans=statement_source_spans,
            excerpt=assertion.evidence_text,
        )
        accumulator.logic_assertions.append(
            DraftLogicAssertion(
                root=materialize_ref(assertion.root),
                parent_temporary_id=parent_temporary_id,
                evidence_spans=evidence_spans,
                confidence=assertion.confidence,
                metadata={
                    "construction": "partial_logic_assertion",
                    "logic_index": assertion_index,
                    "contextual_chunk_index": contextual_chunk_index,
                    "immediate_evidence_text": assertion.evidence_text,
                    "source_provenance_precision": (
                        "exact" if exact else "inherited_parent_span"
                    ),
                },
            )
        )

    for rule_index, rule in enumerate(decision.rules):
        evidence_spans, exact = _resolve_excerpt_to_root_spans(
            root_content=source_request.content,
            parent_statement=statement,
            parent_source_spans=statement_source_spans,
            excerpt=rule.evidence_text,
        )
        accumulator.logic_rules.append(
            DraftLogicRule(
                condition=materialize_ref(rule.condition),
                effect=materialize_ref(rule.effect),
                parent_temporary_id=parent_temporary_id,
                evidence_spans=evidence_spans,
                confidence=rule.confidence,
                metadata={
                    "construction": "partial_logic_rule",
                    "logic_index": rule_index,
                    "contextual_chunk_index": contextual_chunk_index,
                    "immediate_evidence_text": rule.evidence_text,
                    "source_provenance_precision": (
                        "exact" if exact else "inherited_parent_span"
                    ),
                },
            )
        )


def _rewrite_draft_logic_slot_refs(
    expression: DraftLogicExpression,
    replacements: dict[str, str],
) -> None:
    if expression.slot_temporary_id is not None:
        expression.slot_temporary_id = replacements.get(
            expression.slot_temporary_id,
            expression.slot_temporary_id,
        )
        return
    for operand in expression.operands:
        _rewrite_draft_logic_slot_refs(operand, replacements)


def _coalesce_equivalent_logic_slots(accumulator: _DraftAccumulator) -> int:
    """Unify same-orientation slots only when the whole components are compatible.

    Chunk-local extraction can introduce separate placeholders for the same
    proposition. Cross-chunk augmentation may bind both placeholders to the same
    semantic node. A shared identical ``(semantic-node, polarity)`` binding is
    evidence that the slots denote the same Boolean variable in the same
    orientation.

    Coalescing must nevertheless remain conservative. Two slot components can
    share one identical binding while disagreeing on the polarity of another
    semantic node. Unconditionally unioning such components creates an invalid
    slot containing the same semantic node twice with opposite polarities.

    Therefore every union is checked against the *entire current component*.
    Any overlapping semantic node with different polarity vetoes the union and
    the slots remain separate. This is intentionally a same-orientation
    coalescer; it does not try to infer or rewrite complement relationships.
    """
    slots = list(accumulator.logic_slots)
    if len(slots) < 2:
        return 0

    parent: dict[str, str] = {slot.temporary_id: slot.temporary_id for slot in slots}
    order = {slot.temporary_id: index for index, slot in enumerate(slots)}

    # Keep one polarity per semantic node for each union-find component. Slots
    # are expected to be individually consistent; cross-chunk augmentation also
    # rejects an opposite-polarity duplicate within one slot. Build this map
    # defensively so the coalescer can fail with a precise message if that
    # invariant is ever violated before unioning begins.
    component_bindings: dict[str, dict[str, bool]] = {}
    for slot in slots:
        polarity_by_node: dict[str, bool] = {}
        for binding in slot.bindings:
            node_id = binding.semantic_temporary_id
            polarity = bool(binding.value)
            prior = polarity_by_node.get(node_id)
            if prior is not None and prior != polarity:
                raise InvalidModelOutputError(
                    "Logic slot already contains opposite-polarity bindings for "
                    "the same semantic node before coalescing: slot_id={} "
                    "semantic_temporary_id={} polarities=({}, {})".format(
                        slot.temporary_id,
                        node_id,
                        prior,
                        polarity,
                    )
                )
            polarity_by_node[node_id] = polarity
        component_bindings[slot.temporary_id] = polarity_by_node

    def find(slot_id: str) -> str:
        while parent[slot_id] != slot_id:
            parent[slot_id] = parent[parent[slot_id]]
            slot_id = parent[slot_id]
        return slot_id

    skipped_conflicting_unions = 0

    def try_union(left: str, right: str) -> bool:
        """Union two components only if all shared node polarities agree."""
        nonlocal skipped_conflicting_unions

        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            return True

        left_bindings = component_bindings[left_root]
        right_bindings = component_bindings[right_root]
        conflicting_nodes = [
            node_id
            for node_id in left_bindings.keys() & right_bindings.keys()
            if left_bindings[node_id] != right_bindings[node_id]
        ]
        if conflicting_nodes:
            skipped_conflicting_unions += 1
            conflict_preview = [
                {
                    "semantic_temporary_id": node_id,
                    "left_polarity": left_bindings[node_id],
                    "right_polarity": right_bindings[node_id],
                }
                for node_id in sorted(conflicting_nodes)[:5]
            ]
            if memory_graph_trace_enabled():
                logger.warning(
                    "Logic-slot coalescing skipped polarity-incompatible union: "
                    "left_root={} right_root={} conflicts={} preview={}",
                    left_root,
                    right_root,
                    len(conflicting_nodes),
                    conflict_preview,
                )
            return False

        # Preserve stable first-slot canonicalization, matching the previous
        # deterministic behavior.
        if order[left_root] <= order[right_root]:
            canonical_root = left_root
            absorbed_root = right_root
        else:
            canonical_root = right_root
            absorbed_root = left_root

        parent[absorbed_root] = canonical_root
        merged_map = dict(component_bindings[canonical_root])
        merged_map.update(component_bindings[absorbed_root])
        component_bindings[canonical_root] = merged_map
        component_bindings.pop(absorbed_root, None)
        return True

    binding_owner: dict[tuple[str, bool], str] = {}
    for slot in slots:
        for binding in slot.bindings:
            key = (binding.semantic_temporary_id, bool(binding.value))
            owner = binding_owner.get(key)
            if owner is None:
                binding_owner[key] = slot.temporary_id
            else:
                try_union(owner, slot.temporary_id)

    replacements = {slot_id: find(slot_id) for slot_id in parent}
    replacements = {
        slot_id: canonical
        for slot_id, canonical in replacements.items()
        if slot_id != canonical
    }
    if not replacements:
        if skipped_conflicting_unions:
            logger.info(
                "Logic-slot coalescing complete with no merges: slots={} "
                "skipped_polarity_conflicts={}",
                len(slots),
                skipped_conflicting_unions,
            )
        return 0

    slot_by_id = {slot.temporary_id: slot for slot in slots}
    groups: dict[str, list[DraftLogicSlot]] = defaultdict(list)
    for slot in slots:
        groups[find(slot.temporary_id)].append(slot)

    merged_slots: list[DraftLogicSlot] = []
    for canonical_id, members in sorted(
        groups.items(),
        key=lambda item: order[item[0]],
    ):
        canonical = slot_by_id[canonical_id]

        # Deduplicate by semantic node ID, not by (node, polarity). A valid
        # coalesced slot has exactly one orientation for each semantic node.
        merged_by_node: dict[str, DraftLogicSlotBinding] = {}
        for member in members:
            for binding in member.bindings:
                node_id = binding.semantic_temporary_id
                prior = merged_by_node.get(node_id)
                if prior is None:
                    merged_by_node[node_id] = binding
                    continue
                if bool(prior.value) != bool(binding.value):
                    # This should be unreachable because try_union() checks the
                    # complete component before every merge. Keep an explicit
                    # invariant guard so a future mutation cannot silently
                    # reintroduce the invalid DraftLogicSlot seen in wlateral_65.
                    raise InvalidModelOutputError(
                        "Logic-slot coalescer invariant violated: merged component "
                        "contains opposite polarities for one semantic node: "
                        "canonical_slot_id={} semantic_temporary_id={} "
                        "polarities=({}, {}) members={}".format(
                            canonical_id,
                            node_id,
                            prior.value,
                            binding.value,
                            [member.temporary_id for member in members],
                        )
                    )

        canonical.bindings = list(merged_by_node.values())
        canonical.evidence_spans = _dedupe_spans(
            [span for member in members for span in member.evidence_spans]
        )
        canonical.confidence = max(member.confidence for member in members)
        if len(members) > 1:
            canonical.metadata = {
                **canonical.metadata,
                "coalesced_logic_slots": [
                    member.temporary_id
                    for member in members
                    if member.temporary_id != canonical_id
                ],
            }
        merged_slots.append(canonical)

    for assertion in accumulator.logic_assertions:
        _rewrite_draft_logic_slot_refs(assertion.root, replacements)
    for rule in accumulator.logic_rules:
        _rewrite_draft_logic_slot_refs(rule.condition, replacements)
        _rewrite_draft_logic_slot_refs(rule.effect, replacements)

    accumulator.logic_slots = merged_slots
    logger.info(
        "Logic-slot coalescing complete: input_slots={} output_slots={} "
        "coalesced_slots={} skipped_polarity_conflicts={}",
        len(slots),
        len(merged_slots),
        len(replacements),
        skipped_conflicting_unions,
    )
    return len(replacements)


def _draft_logic_expression_key(
    expression: DraftLogicExpression,
) -> tuple[Any, ...]:
    """Canonical structural key after slot coalescing."""
    if expression.slot_temporary_id is not None:
        return (
            "literal",
            expression.slot_temporary_id,
            bool(expression.slot_value),
        )
    return (
        "expression",
        expression.operator.value if expression.operator is not None else None,
        expression.threshold,
        tuple(
            _draft_logic_expression_key(operand)
            for operand in expression.operands
        ),
    )


def _dedupe_draft_logic_constraints(
    accumulator: _DraftAccumulator,
) -> tuple[int, int]:
    """Remove exact duplicate assertions/rules created by overlapping scopes.

    Per-statement logic normalization intentionally favors local calls. A broad
    parent and a narrower child can occasionally normalize the same source-explicit
    constraint. After equivalent slots have been coalesced, identical constraints
    are semantically redundant, so keep the earliest copy and merge provenance.
    """
    assertion_by_key: dict[tuple[Any, ...], DraftLogicAssertion] = {}
    unique_assertions: list[DraftLogicAssertion] = []
    removed_assertions = 0
    for assertion in accumulator.logic_assertions:
        key = _draft_logic_expression_key(assertion.root)
        prior = assertion_by_key.get(key)
        if prior is None:
            assertion_by_key[key] = assertion
            unique_assertions.append(assertion)
            continue
        prior.evidence_spans = _dedupe_spans(
            [*prior.evidence_spans, *assertion.evidence_spans]
        )
        prior.confidence = max(prior.confidence, assertion.confidence)
        removed_assertions += 1

    rule_by_key: dict[tuple[Any, ...], DraftLogicRule] = {}
    unique_rules: list[DraftLogicRule] = []
    removed_rules = 0
    for rule in accumulator.logic_rules:
        key = (
            _draft_logic_expression_key(rule.condition),
            _draft_logic_expression_key(rule.effect),
        )
        prior = rule_by_key.get(key)
        if prior is None:
            rule_by_key[key] = rule
            unique_rules.append(rule)
            continue
        prior.evidence_spans = _dedupe_spans(
            [*prior.evidence_spans, *rule.evidence_spans]
        )
        prior.confidence = max(prior.confidence, rule.confidence)
        removed_rules += 1

    accumulator.logic_assertions = unique_assertions
    accumulator.logic_rules = unique_rules
    return removed_assertions, removed_rules


def _dedupe_draft_relations(accumulator: _DraftAccumulator) -> int:
    """Remove exact duplicate semantic relations created by overlapping passes."""
    relation_by_key: dict[tuple[Any, ...], DraftRelation] = {}
    unique_relations: list[DraftRelation] = []
    removed = 0

    for relation in accumulator.relations:
        if relation.directed:
            endpoints = (
                relation.source_temporary_id,
                relation.target_temporary_id,
            )
        else:
            endpoints = tuple(
                sorted(
                    (
                        relation.source_temporary_id,
                        relation.target_temporary_id,
                    )
                )
            )
        key = (
            relation.relation,
            relation.directed,
            *endpoints,
        )
        prior = relation_by_key.get(key)
        if prior is None:
            relation_by_key[key] = relation
            unique_relations.append(relation)
            continue

        prior.evidence_spans = _dedupe_spans(
            [*prior.evidence_spans, *relation.evidence_spans]
        )
        prior.confidence = max(prior.confidence, relation.confidence)
        prior.metadata = {
            **prior.metadata,
            "deduped_relation_sources": sorted(
                {
                    str(prior.metadata.get("construction", "unknown")),
                    str(relation.metadata.get("construction", "unknown")),
                }
            ),
        }
        removed += 1

    accumulator.relations = unique_relations
    return removed


def _draft_logic_literal(
    expression: DraftLogicExpression,
) -> tuple[str, bool] | None:
    """Return a signed literal when ``expression`` does not need an AST.

    A direct slot is a literal. A NOT around a literal is normalized by flipping
    its sign so trivial negation does not force persistent AST storage.
    """
    if expression.slot_temporary_id is not None:
        return expression.slot_temporary_id, expression.slot_value
    if (
        expression.operator == LogicalOperator.NOT
        and len(expression.operands) == 1
    ):
        nested = _draft_logic_literal(expression.operands[0])
        if nested is not None:
            slot_id, value = nested
            return slot_id, not value
    return None


def _materialize_logic_expression(
    expression: DraftLogicExpression,
    slot_temporary_to_canonical: dict[str, str],
) -> LogicExpression:
    if expression.slot_temporary_id is not None:
        leaf = LogicExpression(
            slot_id=slot_temporary_to_canonical[expression.slot_temporary_id]
        )
        if expression.slot_value:
            return leaf
        return LogicExpression(
            operator=LogicalOperator.NOT,
            operands=[leaf],
        )

    return LogicExpression(
        operator=expression.operator,
        threshold=expression.threshold,
        operands=[
            _materialize_logic_expression(operand, slot_temporary_to_canonical)
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



def _single_exact_span_key(
    spans: list[SourceSpan],
    *,
    exact: bool,
) -> tuple[int, int] | None:
    """Return a safe occurrence key only for one uniquely aligned source span.

    When the same excerpt appears more than once, the resolver intentionally
    returns multiple exact spans. In that case we cannot know which occurrence a
    model child refers to, so occurrence identity remains distinct rather than
    risking an incorrect merge.
    """
    if not exact or len(spans) != 1:
        return None
    span = spans[0]
    return span.start, span.end


def _dedupe_context_paths(paths: list[list[str]]) -> list[list[str]]:
    seen: set[tuple[str, ...]] = set()
    result: list[list[str]] = []
    for path in paths:
        cleaned = [item.strip() for item in path if item and item.strip()]
        if not cleaned:
            continue
        key = tuple(cleaned)
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def _draft_node_context_paths(
    accumulator: _DraftAccumulator,
    temporary_id: str,
    *,
    max_depth: int = 5,
) -> list[list[str]]:
    """Return bounded semantic ancestor paths for one draft occurrence.

    Paths contain ancestor content from broadest retained ancestor to immediate
    parent. The node's own content is supplied separately to identity models.
    """
    if max_depth <= 0:
        return []

    node_by_id = {node.temporary_id: node for node in accumulator.nodes}
    parents: dict[str, list[str]] = defaultdict(list)
    for edge in accumulator.hierarchy:
        if edge.parent_temporary_id is not None:
            parents[edge.child_temporary_id].append(edge.parent_temporary_id)

    paths: list[list[str]] = []

    def walk(node_id: str, reverse_path: list[str], seen: set[str]) -> None:
        if len(reverse_path) >= max_depth:
            paths.append(list(reversed(reverse_path)))
            return
        parent_ids = parents.get(node_id, [])
        if not parent_ids:
            if reverse_path:
                paths.append(list(reversed(reverse_path)))
            return
        progressed = False
        for parent_id in parent_ids:
            if parent_id in seen:
                continue
            parent = node_by_id.get(parent_id)
            if parent is None:
                continue
            progressed = True
            label = (parent.content or parent.routing_text).strip()
            walk(
                parent_id,
                [*reverse_path, label[:600]],
                {*seen, parent_id},
            )
        if not progressed and reverse_path:
            paths.append(list(reversed(reverse_path)))

    walk(temporary_id, [], {temporary_id})
    return _dedupe_context_paths(paths)


def _draft_slot_context_paths(
    accumulator: _DraftAccumulator,
    slot: DraftLogicSlot,
) -> list[list[str]]:
    """Collect origin and bound-occurrence context for a draft logic slot."""
    paths: list[list[str]] = []
    origin_parent_id = slot.metadata.get("logic_parent_temporary_id")
    if isinstance(origin_parent_id, str) and origin_parent_id:
        origin_parent = _draft_node_by_id(accumulator, origin_parent_id)
        ancestor_paths = _draft_node_context_paths(accumulator, origin_parent_id)
        if ancestor_paths:
            paths.extend(
                [*path, origin_parent.content[:600]]
                for path in ancestor_paths
            )
        else:
            paths.append([origin_parent.content[:600]])

    for binding in slot.bindings:
        paths.extend(
            _draft_node_context_paths(
                accumulator,
                binding.semantic_temporary_id,
            )
        )

    return _dedupe_context_paths(paths)


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