"""Mode-aware finalize disposition for NL-no-code turns (#445).

Scoped to autonomous deferral (type C) and interactive ask-user routing.
Pattern B soft grounding bounce is deferred — rare on M3 and regex-fragile.

Keeps the existing planning-text fast-path via ``looks_like_planning_text``.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Optional

from cuga.backend.cuga_graph.nodes.cuga_lite.nl_auto_continue_classifier import (
    looks_like_planning_text,
)


class FinalizeDisposition(str, Enum):
    CONTINUE = "continue"
    ASK_USER = "ask_user"
    FINALIZE = "finalize"


# Deferral language (#445) — interrogative and statement-form.
_DEFERRAL_RE = re.compile(
    r"(?:"
    r"would\s+you\s+like(?:\s+me)?\s+to\b|"
    r"shall\s+i\b|"
    r"should\s+i\s+(?:continue|keep\s+going|proceed)\b|"
    r"let\s+me\s+know\s+how\s+you(?:'d| would)\s+like\s+to\s+proceed|"
    r"let\s+me\s+know\s+how\s+you(?:'d| would)\s+like\s+to\b|"
    r"to\s+proceed,?\s+i\s+recommend\b|"
    r"once\s+.+\bi\s+can\s+(?:complete|retry|proceed|finish)\b|"
    r"i\s+can\s+.+\bonce\s+you\b"
    r")",
    re.IGNORECASE | re.DOTALL,
)

_SECOND_PERSON_RE = re.compile(r"\b(?:you|your|yours)\b", re.IGNORECASE)
_INPUT_REQUEST_RE = re.compile(
    r"(?:"
    r"\b(?:require|need|confirm|provide|tell\s+me|share)\b.{0,40}\b"
    r"(?:your|you)\b|"
    r"\b(?:please|first)\b.{0,40}\b(?:confirm|provide|send|share)\b|"
    r"\bwhich\b.{0,40}\?"
    r")",
    re.IGNORECASE | re.DOTALL,
)
_CLARIFYING_Q_RE = re.compile(r"\b(?:which|what|who|where|when|whom)\b", re.IGNORECASE)


def looks_like_autonomous_deferral(visible: str) -> bool:
    """True when the turn hands control back to the user (deferral language)."""
    t = (visible or "").strip()
    if not t:
        return False
    return bool(_DEFERRAL_RE.search(t))


def looks_like_ask_user(visible: str) -> bool:
    """Interactive turns that should wait for a real user reply."""
    t = (visible or "").strip()
    if not t:
        return False
    if looks_like_autonomous_deferral(t):
        return True
    if t.rstrip().endswith("?"):
        if _SECOND_PERSON_RE.search(t) or _CLARIFYING_Q_RE.search(t):
            return True
    if _INPUT_REQUEST_RE.search(t) and _SECOND_PERSON_RE.search(t):
        return True
    return False


def resolve_finalize_disposition(
    visible: str,
    *,
    autonomous: bool = False,
    nl_auto_continue: bool = True,
    classifier_says_continue: Optional[bool] = None,
) -> FinalizeDisposition:
    """Resolve disposition for an NL-no-code candidate final.

    Order: planning → deferral (mode-aware) → ask_user (mode-aware) →
    classifier continue → finalize.
    """
    text = (visible or "").strip()

    if nl_auto_continue and looks_like_planning_text(text):
        return FinalizeDisposition.CONTINUE

    if looks_like_autonomous_deferral(text):
        if autonomous:
            return FinalizeDisposition.CONTINUE
        return FinalizeDisposition.ASK_USER

    if looks_like_ask_user(text):
        if autonomous:
            return FinalizeDisposition.CONTINUE
        return FinalizeDisposition.ASK_USER

    if nl_auto_continue and classifier_says_continue is True:
        return FinalizeDisposition.CONTINUE

    return FinalizeDisposition.FINALIZE
