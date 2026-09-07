from __future__ import annotations

import threading
from typing import Any

from loguru import logger

from .schemas import GraphBuildRequest, PropositionPayload


_SPACY_MODEL_NAME = "en_core_web_trf"
_SPACY_PIPE_BATCH_SIZE = 32

_NLP = None
_NLP_LOAD_LOCK = threading.Lock()
_NLP_PROCESS_LOCK = threading.Lock()

_ACTION_SEMANTIC_ROLES = {
    "requirement",
    "prohibition",
    "permission",
    "procedure",
    "intended_action",
}

_PASSIVE_SUBJECT_DEPS = {"nsubjpass", "nsubj:pass"}
_PASSIVE_AUX_DEPS = {"auxpass", "aux:pass"}
_ACTIVE_SUBJECT_DEPS = {"nsubj", "csubj"}
_DIRECT_OBJECT_DEPS = {"dobj", "obj", "dative", "iobj", "attr", "oprd"}
_PREPOSITIONAL_OBJECT_DEPS = {"pobj"}


class SpacyAtomicPayloadExtractorError(RuntimeError):
    """Raised when local dependency-based S/P/O extraction cannot run safely."""


def get_spacy_nlp():
    global _NLP
    if _NLP is not None:
        return _NLP

    with _NLP_LOAD_LOCK:
        if _NLP is not None:
            return _NLP
        try:
            import spacy
        except ImportError as exc:
            raise SpacyAtomicPayloadExtractorError(
                "spaCy is required for local decomposition guidance and post-tree S/P/O "
                "extraction. Install spaCy and the en_core_web_trf pipeline before "
                "building the graph."
            ) from exc

        try:
            _NLP = spacy.load(_SPACY_MODEL_NAME)
        except OSError as exc:
            raise SpacyAtomicPayloadExtractorError(
                "spaCy model 'en_core_web_trf' is not installed. Install the transformer "
                "pipeline (for example: `python -m spacy download en_core_web_trf`) "
                "before building the graph. The local parser is required by both "
                "decomposition guidance and final S/P/O extraction; legacy LLM "
                "paths are preserved separately and are not automatic fallbacks."
            ) from exc

        logger.info(
            "Loaded local spaCy dependency pipeline: model={} (decomposition guidance + S/P/O)",
            _SPACY_MODEL_NAME,
        )
        return _NLP


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _clean_chunk_text(chunk: Any) -> str:
    tokens = list(chunk)
    while tokens and (tokens[0].dep_ == "det" or tokens[0].pos_ == "DET"):
        tokens.pop(0)
    while tokens and tokens[-1].is_punct:
        tokens.pop()
    if not tokens:
        return ""
    start = tokens[0].idx
    end = tokens[-1].idx + len(tokens[-1].text)
    return chunk.doc.text[start:end].strip()


def _noun_phrase_for_token(token: Any, noun_chunks: list[Any]) -> str:
    for chunk in noun_chunks:
        if chunk.start <= token.i < chunk.end:
            text = _clean_chunk_text(chunk)
            if text:
                return text
    return token.text.strip()


def _is_passive(doc: Any) -> bool:
    return any(
        token.dep_ in _PASSIVE_SUBJECT_DEPS or token.dep_ in _PASSIVE_AUX_DEPS
        for token in doc
    )


def _passive_agents(doc: Any, noun_chunks: list[Any]) -> list[str]:
    agents: list[str] = []

    # spaCy English commonly represents "by X" as:
    # by --agent--> VERB, X --pobj--> by
    for token in doc:
        if token.dep_ == "agent":
            for child in token.children:
                if child.dep_ in _PREPOSITIONAL_OBJECT_DEPS:
                    agents.append(_noun_phrase_for_token(child, noun_chunks))

    # Also tolerate Universal-Dependencies-style obl:agent + case(by).
    for token in doc:
        if not token.dep_.startswith("obl"):
            continue
        if any(child.dep_ == "case" and child.lower_ == "by" for child in token.children):
            agents.append(_noun_phrase_for_token(token, noun_chunks))

    return _dedupe(agents)


def _looks_imperative(doc: Any, semantic_role: str | None) -> bool:
    if any(token.dep_ in _ACTIVE_SUBJECT_DEPS for token in doc):
        return False

    roots = [token for token in doc if token.dep_ == "ROOT"]
    if not roots:
        return False
    root = roots[0]

    # A base-form verbal root with no overt subject is the strongest parse-level
    # signal. The semantic role is used only as a conservative policy-language
    # fallback for parser variation around negated/modal imperatives.
    if root.pos_ == "VERB" and root.tag_ in {"VB", "VBP"}:
        return True
    return semantic_role in _ACTION_SEMANTIC_ROLES and root.pos_ == "VERB"


def _extract_predicates(doc: Any) -> list[str]:
    predicates: list[str] = []

    # Keep all lexical verbs in an already-atomic leaf. Multiple grounded verbs
    # are useful lexical alternatives for retrieval and are not interpreted as a
    # decomposition signal here.
    for token in doc:
        if token.pos_ != "VERB":
            continue
        lemma = token.lemma_.strip() if token.lemma_ else token.text.strip()
        if lemma:
            predicates.append(lemma)

    if predicates:
        return _dedupe(predicates)

    # Copular/state statements may have no VERB token in some dependency schemes.
    # Keep a grounded state/relation anchor rather than inventing one.
    roots = [token for token in doc if token.dep_ == "ROOT"]
    for root in roots:
        if root.pos_ == "AUX":
            lemma = root.lemma_.strip() if root.lemma_ else root.text.strip()
            if lemma:
                predicates.append(lemma)
        elif root.pos_ in {"ADJ", "NOUN", "PROPN"}:
            predicates.append(root.text.strip())
            for child in root.children:
                if child.dep_ in {"cop", "aux"} and child.pos_ == "AUX":
                    lemma = child.lemma_.strip() if child.lemma_ else child.text.strip()
                    if lemma:
                        predicates.append(lemma)

    return _dedupe(predicates)


def _extract_payload_from_doc(
    doc: Any,
    *,
    semantic_role: str | None,
) -> PropositionPayload:
    noun_chunks = list(doc.noun_chunks)
    passive = _is_passive(doc)

    subjects: list[str] = []
    objects: list[str] = []

    if passive:
        subjects.extend(_passive_agents(doc, noun_chunks))
        for token in doc:
            if token.dep_ in _PASSIVE_SUBJECT_DEPS:
                objects.append(_noun_phrase_for_token(token, noun_chunks))
    else:
        for token in doc:
            if token.dep_ in _ACTIVE_SUBJECT_DEPS:
                subjects.append(_noun_phrase_for_token(token, noun_chunks))

    # Direct and indirect objects remain useful lexical targets regardless of
    # whether the sentence itself is active or passive.
    for token in doc:
        if token.dep_ in _DIRECT_OBJECT_DEPS:
            objects.append(_noun_phrase_for_token(token, noun_chunks))
            continue
        if token.dep_ in _PREPOSITIONAL_OBJECT_DEPS:
            # "by X" in a passive is the semantic actor, not an object.
            if passive and token.head.dep_ == "agent":
                continue
            objects.append(_noun_phrase_for_token(token, noun_chunks))

    if not passive and not subjects and _looks_imperative(doc, semantic_role):
        subjects.append("you")

    return PropositionPayload(
        subjects=_dedupe(subjects),
        predicates=_extract_predicates(doc),
        objects=_dedupe(objects),
    )


_get_nlp = get_spacy_nlp


def extract_atomic_payloads_spacy(
    request: GraphBuildRequest,
    *,
    leaves: list[dict[str, Any]],
) -> dict[str, PropositionPayload]:
    """Extract S/P/O from confirmed atomic leaves with ``en_core_web_trf``.

    The dependency parser performs grammatical analysis locally. This function
    then maps dependency/POS/lemma labels into the graph's lightweight lexical
    payload using deterministic Python rules. No generative LLM is called.
    """
    if not leaves:
        return {}

    expected_ids = [str(leaf["temporary_id"]) for leaf in leaves]
    if len(expected_ids) != len(set(expected_ids)):
        raise SpacyAtomicPayloadExtractorError(
            f"Duplicate atomic payload extraction IDs for source_id={request.source_id}"
        )

    texts = [str(leaf["content"]) for leaf in leaves]
    nlp = get_spacy_nlp()

    # Reuse one heavyweight transformer pipeline across builds. Serializing the
    # local parser call avoids concurrent threads duplicating transformer work or
    # contending for the same model state. ``nlp.pipe`` still batches leaves.
    with _NLP_PROCESS_LOCK:
        docs = list(nlp.pipe(texts, batch_size=_SPACY_PIPE_BATCH_SIZE))

    if len(docs) != len(leaves):
        raise SpacyAtomicPayloadExtractorError(
            "spaCy atomic payload extraction returned an unexpected document count: "
            f"source_id={request.source_id} expected={len(leaves)} actual={len(docs)}"
        )

    mapping: dict[str, PropositionPayload] = {}
    for leaf, doc in zip(leaves, docs, strict=True):
        temporary_id = str(leaf["temporary_id"])
        semantic_role_raw = leaf.get("semantic_role")
        semantic_role = (
            str(semantic_role_raw) if semantic_role_raw is not None else None
        )
        mapping[temporary_id] = _extract_payload_from_doc(
            doc,
            semantic_role=semantic_role,
        )

    logger.info(
        "Post-tree atomic S/P/O dependency extraction complete: source_id={} "
        "leaves={} model={}",
        request.source_id,
        len(leaves),
        _SPACY_MODEL_NAME,
    )
    return mapping
