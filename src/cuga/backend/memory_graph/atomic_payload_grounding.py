"""Shared source-grounded S/P/O payload sanitation for graph builders."""

from __future__ import annotations

import re

from .schemas import PropositionPayload


_LEXICAL_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?")
_ACTION_SEMANTIC_ROLES = {
    "requirement",
    "prohibition",
    "permission",
    "procedure",
    "intended_action",
}
_IMPERATIVE_PREFIX_WORDS = {
    "ask",
    "avoid",
    "call",
    "change",
    "check",
    "collect",
    "confirm",
    "continue",
    "create",
    "do",
    "ensure",
    "execute",
    "get",
    "give",
    "keep",
    "list",
    "log",
    "make",
    "provide",
    "read",
    "request",
    "respond",
    "return",
    "search",
    "send",
    "stop",
    "tell",
    "transfer",
    "unlock",
    "update",
    "use",
    "verify",
    "wait",
    "write",
}
_AUXILIARY_OR_MODAL_WORDS = {
    "can",
    "cannot",
    "could",
    "did",
    "do",
    "does",
    "had",
    "has",
    "have",
    "may",
    "might",
    "must",
    "shall",
    "should",
    "will",
    "would",
}
_COPULA_FORMS = {"am", "are", "be", "been", "being", "is", "was", "were"}
_PREDICATE_SKIP_WORDS = {
    "also",
    "always",
    "directly",
    "ever",
    "first",
    "immediately",
    "just",
    "never",
    "not",
    "only",
    "simply",
    "then",
}
_IRREGULAR_PREDICATE_LEMMAS = {
    "am": "be",
    "are": "be",
    "been": "be",
    "being": "be",
    "is": "be",
    "was": "be",
    "were": "be",
    "did": "do",
    "does": "do",
    "done": "do",
    "had": "have",
    "has": "have",
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
    return any(candidate_roots & _predicate_roots(source_token) for source_token in _lexical_tokens(source))


def _allows_implicit_you(source: str, semantic_role: str | None) -> bool:
    tokens = _lexical_tokens(source)
    if not tokens:
        return False
    if tokens[0] in {"do", "never", "always", "please"}:
        return True
    return semantic_role in _ACTION_SEMANTIC_ROLES and tokens[0] in _IMPERATIVE_PREFIX_WORDS


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
