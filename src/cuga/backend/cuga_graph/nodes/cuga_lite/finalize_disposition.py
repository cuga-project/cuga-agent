"""Mode-aware finalize disposition for NL-no-code turns (#445).

Only the planning-text fast-path (pre-existing, from #416) is deterministic
here. Ask-user and deferral routing is left entirely to the mode-aware LLM
classifier (``classify_nl_auto_continue_decision`` in
``nl_auto_continue_classifier.py`` — in autonomous mode it answers five
factual questions and ``decide_autonomous`` applies the policy).

A deterministic deferral regex (``_DEFERRAL_RE`` / ``looks_like_autonomous_
deferral``) shipped in an earlier revision of this PR and was removed after
live AppWorld evidence (#732 comments) showed it net-hurts task completion:
an offline replay over 797 saved final answers found it a safe, zero-
false-positive verdict-matcher, but that replay could only check whether the
disposition matched a hand-labeled expectation on already-recorded text — it
could not detect that forcing continuation on a task the model had already
effectively finished (e.g. "would you like me to continue searching for even
earlier liked songs?" after the ground-truth-satisfying answer was already
given) burns the step budget on live re-runs. Concretely: task `325d6ec_1`
regressed from passing 3/3 runs (finalizes on the deferral, already correct)
to failing 3/3 (forced to continue searching past the answer, hits the
70-step ceiling) once the regex was in the loop; removing it recovered most
of that and also improved a second task (`6474048_1`, 2/3 -> 3/3) via
turn-by-turn classifier judgment instead of a blanket forced continue.
"""

from __future__ import annotations

from enum import Enum

from cuga.backend.cuga_graph.nodes.cuga_lite.nl_auto_continue_classifier import (
    looks_like_planning_text,
)


class FinalizeDisposition(str, Enum):
    CONTINUE = "continue"
    ASK_USER = "ask_user"
    FINALIZE = "finalize"


def resolve_finalize_disposition(
    visible: str,
    *,
    autonomous: bool = False,
    nl_auto_continue: bool = True,
) -> FinalizeDisposition:
    """Resolve disposition for an NL-no-code candidate final.

    Only the planning-text fast-path short-circuits here; everything else
    (ask-user, deferral, genuine completion) falls through to
    ``FinalizeDisposition.FINALIZE``, which routes the caller to consult
    ``classify_auto_continue`` — the mode-aware LLM classifier — instead of a
    deterministic verdict. ``nl_auto_continue`` remains the operator kill
    switch for the planning fast-path: turning it off restores pre-#445
    behaviour (finalize as-is, no interception).

    ``autonomous`` is accepted for interface stability with callers and
    tests that pass it, but no longer changes this function's own verdict —
    mode-awareness now lives entirely in the classifier (see module
    docstring).
    """
    text = (visible or "").strip()

    if nl_auto_continue and looks_like_planning_text(text):
        return FinalizeDisposition.CONTINUE

    return FinalizeDisposition.FINALIZE
