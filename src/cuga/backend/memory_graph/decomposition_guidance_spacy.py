from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from loguru import logger

from .atomic_payload_spacy import get_spacy_nlp


# These labels intentionally describe grammatical structure rather than the graph's
# persisted semantic schema. They are guidance for the decomposer, not authoritative
# facts and not retrieval payloads.
_SUBJECT_DEPS = {"nsubj", "nsubjpass", "nsubj:pass", "csubj", "csubjpass"}
_OBJECT_DEPS = {"dobj", "obj", "iobj", "dative", "attr", "oprd"}
_COMPLEMENT_DEPS = {"xcomp", "ccomp", "acomp"}
_FRAME_DEPS = {
    "ROOT",
    "conj",
    "advcl",
    "ccomp",
    "xcomp",
    "acl",
    "relcl",
    "parataxis",
}
_MODAL_LEMMAS = {
    "can",
    "could",
    "may",
    "might",
    "must",
    "shall",
    "should",
    "will",
    "would",
}
_RELATION_CUES = {
    "after",
    "and",
    "before",
    "because",
    "either",
    "else",
    "if",
    "once",
    "only",
    "or",
    "otherwise",
    "provided",
    "requires",
    "require",
    "required",
    "depends",
    "depend",
    "enable",
    "enables",
    "precede",
    "precedes",
    "follow",
    "follows",
    "then",
    "unless",
    "until",
    "when",
    "whenever",
    "while",
}


@dataclass(frozen=True)
class PredicateFrameGuidance:
    frame_id: str
    predicate: str
    predicate_surface: str
    subjects: tuple[str, ...] = ()
    inherited_subjects: tuple[str, ...] = ()
    objects: tuple[str, ...] = ()
    complements: tuple[str, ...] = ()
    modality: tuple[str, ...] = ()
    inherited_modality: tuple[str, ...] = ()
    negation: tuple[str, ...] = ()
    inherited_negation: tuple[str, ...] = ()
    markers: tuple[str, ...] = ()


@dataclass(frozen=True)
class DecompositionGuidance:
    frames: tuple[PredicateFrameGuidance, ...] = ()
    relation_cues: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_prompt_text(self) -> str:
        if not self.frames and not self.relation_cues:
            return (
                "No reliable predicate-frame scaffold was recovered. Preserve the "
                "source semantics directly and do not invent structure merely to "
                "satisfy the parser guidance."
            )

        lines: list[str] = [
            "PARSER-DERIVED STRUCTURAL SCAFFOLD",
            "This scaffold is deterministic guidance, not authoritative semantics.",
            "Use it to avoid dropping source participants, predicates, arguments,",
            "modality, or negation. If the parser is clearly wrong, preserve the",
            "source meaning rather than forcing the scaffold.",
            "",
        ]
        for frame in self.frames:
            lines.append(f"{frame.frame_id}:")
            lines.append(
                f"  predicate: {frame.predicate!r} (surface={frame.predicate_surface!r})"
            )
            if frame.subjects:
                lines.append("  explicit_subjects: " + ", ".join(frame.subjects))
            if frame.inherited_subjects:
                lines.append(
                    "  likely_shared_or_inherited_subjects: "
                    + ", ".join(frame.inherited_subjects)
                )
            if frame.objects:
                lines.append("  objects: " + ", ".join(frame.objects))
            if frame.complements:
                lines.append("  complements: " + ", ".join(frame.complements))
            if frame.modality:
                lines.append("  modality_or_auxiliaries: " + ", ".join(frame.modality))
            if frame.inherited_modality:
                lines.append(
                    "  likely_shared_or_inherited_modality: "
                    + ", ".join(frame.inherited_modality)
                )
            if frame.negation:
                lines.append("  negation: " + ", ".join(frame.negation))
            if frame.inherited_negation:
                lines.append(
                    "  likely_shared_or_inherited_negation: "
                    + ", ".join(frame.inherited_negation)
                )
            if frame.markers:
                lines.append("  local_markers: " + ", ".join(frame.markers))

        if self.relation_cues:
            lines.extend(
                [
                    "",
                    "Explicit connective/relation cues detected in the source:",
                    "  " + ", ".join(self.relation_cues),
                    "These cues may be represented by local_relations or the later",
                    "relation/logic normalization pass instead of being repeated in",
                    "every standalone child. Do not drop proposition-internal meaning.",
                ]
            )
        if self.notes:
            lines.extend(["", "Parser notes:"])
            lines.extend(f"  - {note}" for note in self.notes)
        return "\n".join(lines)


def _dedupe(values: list[str]) -> tuple[str, ...]:
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
    return tuple(result)


def _clean_chunk_text(chunk: Any) -> str:
    tokens = list(chunk)
    while tokens and tokens[0].is_punct:
        tokens.pop(0)
    while tokens and tokens[-1].is_punct:
        tokens.pop()
    if not tokens:
        return ""
    start = tokens[0].idx
    end = tokens[-1].idx + len(tokens[-1].text)
    return chunk.doc.text[start:end].strip()


def _noun_phrase(token: Any, noun_chunks: list[Any]) -> str:
    for chunk in noun_chunks:
        if chunk.start <= token.i < chunk.end:
            text = _clean_chunk_text(chunk)
            if text:
                return text
    return token.text.strip()


def _predicate_candidates(doc: Any) -> list[Any]:
    candidates: list[Any] = []
    for token in doc:
        if token.pos_ == "VERB" and token.dep_ in _FRAME_DEPS:
            candidates.append(token)
            continue
        # Copular/state clauses frequently have an ADJ/NOUN/PROPN root with a
        # copula child. Treat that root as a predicate frame too.
        if token.dep_ == "ROOT" and token.pos_ in {"ADJ", "NOUN", "PROPN"}:
            if any(child.dep_ == "cop" for child in token.children):
                candidates.append(token)
    if candidates:
        return candidates

    roots = [token for token in doc if token.dep_ == "ROOT"]
    return roots[:1]


def _explicit_subjects(predicate: Any, noun_chunks: list[Any]) -> tuple[str, ...]:
    return _dedupe(
        [
            _noun_phrase(child, noun_chunks)
            for child in predicate.children
            if child.dep_ in _SUBJECT_DEPS
        ]
    )


def _inherit_subjects(
    predicate: Any,
    *,
    frame_subjects_by_index: dict[int, tuple[str, ...]],
) -> tuple[str, ...]:
    if predicate.head is predicate:
        return ()

    # Shared-subject coordination and reduced adverbial clauses are common in
    # policy text: "you verify X and update Y" / "before changing X, you verify Y".
    # This is only a hint to the LLM; it is explicitly marked as inherited/likely.
    if predicate.dep_ not in {"conj", "advcl", "xcomp", "ccomp"}:
        return ()

    cursor = predicate.head
    visited: set[int] = set()
    while cursor is not None and cursor.i not in visited:
        visited.add(cursor.i)
        subjects = frame_subjects_by_index.get(cursor.i)
        if subjects:
            return subjects
        if cursor.head is cursor:
            break
        cursor = cursor.head
    return ()




def _inherit_feature(
    predicate: Any,
    *,
    feature_by_index: dict[int, tuple[str, ...]],
    allowed_deps: set[str],
) -> tuple[str, ...]:
    if predicate.head is predicate or predicate.dep_ not in allowed_deps:
        return ()
    cursor = predicate.head
    visited: set[int] = set()
    while cursor is not None and cursor.i not in visited:
        visited.add(cursor.i)
        values = feature_by_index.get(cursor.i)
        if values:
            return values
        if cursor.head is cursor:
            break
        cursor = cursor.head
    return ()

def _frame_objects(predicate: Any, noun_chunks: list[Any]) -> tuple[str, ...]:
    values: list[str] = []
    for child in predicate.children:
        if child.dep_ in _OBJECT_DEPS:
            values.append(_noun_phrase(child, noun_chunks))
            continue
        # Prepositional arguments are useful anchors even when they are not
        # grammatical direct objects, e.g. "transfer to a human agent".
        if child.dep_ in {"prep", "agent"}:
            for grandchild in child.children:
                if grandchild.dep_ in {"pobj", "obj"}:
                    values.append(_noun_phrase(grandchild, noun_chunks))
    return _dedupe(values)


def _frame_complements(predicate: Any) -> tuple[str, ...]:
    values: list[str] = []
    for child in predicate.children:
        if child.dep_ in _COMPLEMENT_DEPS:
            subtree = sorted(child.subtree, key=lambda token: token.i)
            if subtree:
                start = subtree[0].idx
                end = subtree[-1].idx + len(subtree[-1].text)
                values.append(child.doc.text[start:end].strip())
    return _dedupe(values)


def _frame_modality(predicate: Any) -> tuple[str, ...]:
    values: list[str] = []
    for child in predicate.children:
        lemma = (child.lemma_ or child.text).strip().casefold()
        if child.dep_ in {"aux", "auxpass", "aux:pass", "cop"}:
            if child.tag_ == "MD" or lemma in _MODAL_LEMMAS:
                values.append(child.text)
    return _dedupe(values)


def _frame_negation(predicate: Any) -> tuple[str, ...]:
    return _dedupe(
        [child.text for child in predicate.children if child.dep_ == "neg"]
    )


def _frame_markers(predicate: Any) -> tuple[str, ...]:
    values: list[str] = []
    for child in predicate.children:
        if child.dep_ in {"mark", "cc"}:
            values.append(child.text)
    if predicate.dep_ == "conj":
        for sibling in predicate.head.children:
            if sibling.dep_ == "cc":
                values.append(sibling.text)
    return _dedupe(values)


def _relation_cues(doc: Any) -> tuple[str, ...]:
    cues: list[str] = []
    for token in doc:
        normalized = token.lower_.strip().casefold()
        lemma = (token.lemma_ or token.text).strip().casefold()
        if normalized not in _RELATION_CUES and lemma not in _RELATION_CUES:
            continue
        if token.dep_ in {
            "mark", "cc", "advmod", "prep", "agent", "ROOT", "conj",
        } or token.pos_ == "VERB":
            cues.append(token.text)
    return _dedupe(cues)


@lru_cache(maxsize=4096)
def extract_decomposition_guidance_spacy(text: str) -> DecompositionGuidance:
    """Return parser-derived predicate frames for one decomposition request.

    The result is deliberately advisory. It helps the LLM preserve grammatical
    participants and clause structure but is never used as a semantic validator.
    """
    source = str(text).strip()
    if not source:
        return DecompositionGuidance()

    nlp = get_spacy_nlp()
    doc = nlp(source)
    noun_chunks = list(doc.noun_chunks)
    predicates = _predicate_candidates(doc)

    explicit_by_index: dict[int, tuple[str, ...]] = {
        predicate.i: _explicit_subjects(predicate, noun_chunks)
        for predicate in predicates
    }
    modality_by_index: dict[int, tuple[str, ...]] = {
        predicate.i: _frame_modality(predicate)
        for predicate in predicates
    }
    negation_by_index: dict[int, tuple[str, ...]] = {
        predicate.i: _frame_negation(predicate)
        for predicate in predicates
    }

    frames: list[PredicateFrameGuidance] = []
    for index, predicate in enumerate(predicates, start=1):
        lemma = (predicate.lemma_ or predicate.text).strip()
        explicit_subjects = explicit_by_index.get(predicate.i, ())
        inherited_subjects = (
            ()
            if explicit_subjects
            else _inherit_subjects(
                predicate,
                frame_subjects_by_index=explicit_by_index,
            )
        )
        modality = modality_by_index.get(predicate.i, ())
        negation = negation_by_index.get(predicate.i, ())
        frames.append(
            PredicateFrameGuidance(
                frame_id=f"frame_{index}",
                predicate=lemma,
                predicate_surface=predicate.text,
                subjects=explicit_subjects,
                inherited_subjects=inherited_subjects,
                objects=_frame_objects(predicate, noun_chunks),
                complements=_frame_complements(predicate),
                modality=modality,
                inherited_modality=(
                    ()
                    if modality
                    else _inherit_feature(
                        predicate,
                        feature_by_index=modality_by_index,
                        allowed_deps={"conj"},
                    )
                ),
                negation=negation,
                inherited_negation=(
                    ()
                    if negation
                    else _inherit_feature(
                        predicate,
                        feature_by_index=negation_by_index,
                        allowed_deps={"conj"},
                    )
                ),
                markers=_frame_markers(predicate),
            )
        )

    notes: list[str] = []
    if len(frames) > 1:
        notes.append(
            "Multiple predicate frames were detected. Prefer the minimum number "
            "of standalone propositions needed to represent all independent frames."
        )
    if any(frame.inherited_subjects for frame in frames):
        notes.append(
            "Some subjects are parser-inherited from a governing/shared clause; "
            "make them explicit only when the source context makes that inheritance clear."
        )
    if any(frame.inherited_modality or frame.inherited_negation for frame in frames):
        notes.append(
            "Some modality/negation is shared across coordinated predicates. Treat "
            "these as likely scope inheritance and preserve the source scope carefully."
        )

    guidance = DecompositionGuidance(
        frames=tuple(frames),
        relation_cues=_relation_cues(doc),
        notes=tuple(notes),
    )
    logger.debug(
        "spaCy decomposition guidance: chars={} frames={} cues={}",
        len(source),
        len(guidance.frames),
        list(guidance.relation_cues),
    )
    return guidance
