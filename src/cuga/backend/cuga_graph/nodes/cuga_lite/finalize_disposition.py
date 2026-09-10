"""Mode-aware finalize disposition for NL-no-code turns (#445).

Scoped to autonomous deferral (type C). Interactive ask-user routing for
unambiguous clarifying questions is left to the LLM classifier rather than
a deterministic short-circuit (#732 review — see ``looks_like_ask_user``).
Pattern B soft grounding bounce is deferred — rare on M3 and regex-fragile.

Keeps the existing planning-text fast-path via ``looks_like_planning_text``.
"""

from __future__ import annotations

import re
from enum import Enum

from cuga.backend.cuga_graph.nodes.cuga_lite.nl_auto_continue_classifier import (
    looks_like_planning_text,
    looks_like_unverified_blocker,
)


class FinalizeDisposition(str, Enum):
    CONTINUE = "continue"
    ASK_USER = "ask_user"
    FINALIZE = "finalize"


# Deterministic detectors below run on the visible text only, never on
# unbounded model output — cap the scan window so a pathological (or just
# very long) response can't turn a fast regex check into measurable blocking
# CPU (#732 review: 2.5-6.8s observed on ~200KB of untruncated text).
_SCAN_MAX_LEN = 4000

# Deferral language (#445) — interrogative and statement-form. Gaps between
# anchors are bounded to a single clause (`[^.!?\n]{0,80}`) rather than
# unbounded `.+` under DOTALL, so the "once ... i can" / "i can ... once you"
# alternatives can't bridge separate sentences or paragraphs (#732 review:
# "I can confirm the order shipped.\n\nOnce you receive the package..." must
# not read as a single deferral clause). Apostrophes accept both the ASCII
# and typographic forms — the latter is common in LLM output.
_DEFERRAL_RE = re.compile(
    r"(?:"
    r"would\s+you\s+like(?:\s+me)?\s+to\b|"
    r"shall\s+i\b|"
    r"should\s+i\s+(?:continue|keep\s+going|proceed)\b|"
    r"let\s+me\s+know\s+how\s+you(?:['’]d| would)\s+like\s+to\b|"
    r"to\s+proceed,?\s+i\s+recommend\b|"
    r"once\s+[^.!?\n]{0,80}?\bi\s+can\s+(?:complete|retry|proceed|finish)\b|"
    r"i\s+can\s+[^.!?\n]{0,80}?\bonce\s+you\b"
    r")",
    re.IGNORECASE,
)

_SECOND_PERSON_RE = re.compile(r"\b(?:you|your|yours)\b", re.IGNORECASE)
_INPUT_REQUEST_RE = re.compile(
    r"(?:"
    r"\b(?:require|need|confirm|provide|tell\s+me|share)\b[^\n]{0,40}\b"
    r"(?:your|you)\b|"
    r"\b(?:please|first)\b[^\n]{0,40}\b(?:confirm|provide|send|share)\b|"
    r"\bwhich\b[^\n]{0,40}\?"
    r")",
    re.IGNORECASE,
)
_CLARIFYING_Q_RE = re.compile(r"\b(?:which|what|who|where|when|whom)\b", re.IGNORECASE)


def looks_like_autonomous_deferral(visible: str) -> bool:
    """True when the turn hands control back to the user (deferral language)."""
    t = (visible or "").strip()[:_SCAN_MAX_LEN]
    if not t:
        return False
    return bool(_DEFERRAL_RE.search(t))


def looks_like_ask_user(visible: str) -> bool:
    """Broader "this reads like it wants user input" detector.

    Not wired into ``resolve_finalize_disposition`` — replaying it over 3,736
    AppWorld eval finals (#732 review) found it firing on 12 completed,
    passing-task answers that merely mention "you"/"your" in passing (e.g.
    "...the largest share of your liked songs"). Kept as a tested primitive;
    callers that need this signal should treat it as a hint, not a verdict,
    and prefer ``classify_auto_continue`` for anything ambiguous.
    """
    t = (visible or "").strip()[:_SCAN_MAX_LEN]
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
) -> FinalizeDisposition:
    """Resolve disposition for an NL-no-code candidate final.

    Order: planning -> deferral (narrow, mode-aware) -> finalize (classifier
    fallback). Everything here is gated on ``nl_auto_continue`` — the
    operator kill switch — so turning it off restores pre-#445 behaviour
    (finalize as-is, no interception).

    ``looks_like_ask_user`` deliberately does NOT short-circuit here (#732
    review): its broader patterns proved over-broad in eval replay, so text
    it flags now falls through to ``classify_auto_continue``, whose spec
    already finalizes real clarifying questions correctly. Deferral text
    that also reads as an unverified-blocker claim (issue #610, e.g. "I'm
    unable to access the Spotify tools. Would you like me to try a different
    approach?") likewise falls through, so the classifier's one-shot
    corrective retry still gets a chance to fire instead of shipping a bare
    "continue" for a false refusal.
    """
    text = (visible or "").strip()

    if nl_auto_continue and looks_like_planning_text(text):
        return FinalizeDisposition.CONTINUE

    if nl_auto_continue and looks_like_autonomous_deferral(text) and not looks_like_unverified_blocker(text):
        if autonomous:
            return FinalizeDisposition.CONTINUE
        return FinalizeDisposition.ASK_USER

    return FinalizeDisposition.FINALIZE
