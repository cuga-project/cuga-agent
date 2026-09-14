"""Unit tests for mode-aware finalize disposition (#445).

The deterministic deferral regex (``_DEFERRAL_RE`` / ``looks_like_autonomous_
deferral``) was removed after live AppWorld evidence (#732 comments) showed
it net-hurts task completion — see the module docstring in
``finalize_disposition.py``. Only the planning-text fast-path (#416) is
deterministic here; ask-user, deferral, and genuine-completion text all fall
through to ``FinalizeDisposition.FINALIZE`` and are resolved by the
mode-aware LLM classifier instead (tested separately in
``test_nl_auto_continue_classifier.py``).
"""

from __future__ import annotations

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.finalize_disposition import (
    FinalizeDisposition,
    resolve_finalize_disposition,
)

pytestmark = pytest.mark.unit


def _resolve(
    text: str,
    *,
    autonomous: bool = False,
    nl_auto_continue: bool = True,
) -> FinalizeDisposition:
    return resolve_finalize_disposition(
        text,
        autonomous=autonomous,
        nl_auto_continue=nl_auto_continue,
    )


def test_planning_continues():
    assert _resolve("We need to search student_loan app.") == FinalizeDisposition.CONTINUE


def test_nl_auto_continue_off_skips_planning_continue():
    assert (
        _resolve("We need to search student_loan app.", nl_auto_continue=False)
        == FinalizeDisposition.FINALIZE
    )


def test_greeting_finalizes():
    assert _resolve("Hello!") == FinalizeDisposition.FINALIZE


def test_give_up_finalizes_without_bounce():
    """Pattern B is deferred — give-ups finalize (no soft bounce)."""
    text = (
        "We have exhausted all discovered tools and none provide game-level event data. "
        "The number cannot be determined from this API."
    )
    assert _resolve(text) == FinalizeDisposition.FINALIZE


@pytest.mark.parametrize(
    "text",
    [
        "Which account should I use?",
        "Ok I will fetch the information, but first I require your ID",
        "Would you like me to continue processing the remaining unfriending actions?",
        "Let me know how you'd like to proceed!",
        "Shall I keep going with the remaining steps?",
        # #732 comments: this exact text regressed a live AppWorld task
        # (325d6ec_1) from 3/3 passing to 3/3 failing when a deferral regex
        # forced continuation here every time — the task's implicit stopping
        # point was already satisfied. Now resolved by the classifier instead.
        "Would you like to continue searching for even earlier liked songs?",
        # Once/i-can phrasing that an earlier regex revision matched, including
        # on quoted third-party text (sami-marreed's 797-task replay finding).
        "Once a valid card is available, I can complete the order.",
        "Let me know if it looks good. I can place the order once you confirm. Best, Stephen Mccoy",
        # Issue #610: a false refusal phrased as a deferral question — must
        # fall through so classify_auto_continue's blocked-claim override
        # (not this layer) decides.
        "I'm unable to access the Spotify tools. Would you like me to try a different approach?",
    ],
)
def test_ask_user_and_deferral_text_falls_through_to_classifier(text):
    """Neither autonomous mode nor interactive mode gets a deterministic
    verdict here anymore for any of this text — resolve_finalize_disposition
    only special-cases planning text; everything else is FINALIZE, which
    routes shared_nodes.py to consult the mode-aware classifier."""
    assert _resolve(text, autonomous=True) == FinalizeDisposition.FINALIZE
    assert _resolve(text, autonomous=False) == FinalizeDisposition.FINALIZE


def test_nl_auto_continue_off_does_not_change_deferral_handling():
    """The kill switch only ever gated the planning fast-path and the (now
    removed) deferral fast-path; deferral text already falls through
    regardless, so toggling it off changes nothing for this text."""
    text = "Would you like me to continue processing the remaining unfriending actions?"
    assert _resolve(text, autonomous=True, nl_auto_continue=False) == FinalizeDisposition.FINALIZE
    assert _resolve(text, autonomous=True, nl_auto_continue=True) == FinalizeDisposition.FINALIZE
