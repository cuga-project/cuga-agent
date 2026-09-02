"""Unit tests for mode-aware finalize disposition (#445).

Scoped to autonomous deferral (type C), interactive ask_user, and planning continue.
Pattern B soft grounding bounce is deferred.
"""

from __future__ import annotations

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.finalize_disposition import (
    FinalizeDisposition,
    looks_like_ask_user,
    looks_like_autonomous_deferral,
    resolve_finalize_disposition,
)

pytestmark = pytest.mark.unit


# ── Detectors ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "Would you like me to continue processing the remaining unfriending actions?",
        "Would you like to continue searching for even earlier liked songs?",
        "Let me know how you'd like to proceed!",
        "I can retry the purchase for you. Let me know how you'd like to proceed.",
        "To proceed, I recommend: double-checking if it was sent from a different sender.",
        "Once a valid card is available, I can complete the order.",
        "Shall I keep going with the remaining steps?",
    ],
)
def test_autonomous_deferral_detected(text):
    assert looks_like_autonomous_deferral(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "",
        "The count is 96.",
        "Task complete—no further action is needed.",
        "We need to search student_loan app.",
        "Hello!",
    ],
)
def test_autonomous_deferral_not_detected(text):
    assert looks_like_autonomous_deferral(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "Which account should I use?",
        "What is your user id?",
        "Ok I will fetch the information, but first I require your ID",
        "Please provide your email so I can continue.",
    ],
)
def test_ask_user_detected(text):
    assert looks_like_ask_user(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "Hello!",
        "The count is 96.",
        "We need to search student_loan app.",
    ],
)
def test_ask_user_not_detected(text):
    assert looks_like_ask_user(text) is False


# ── resolve_finalize_disposition matrix ─────────────────────────────────────


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


def test_interactive_clarifying_question_falls_through_to_classifier():
    """Pure ask_user text (no deferral) no longer short-circuits (#732 review:
    the pattern proved over-broad in eval replay). It reads as FINALIZE here —
    shared_nodes.py then consults classify_auto_continue, whose spec already
    finalizes real clarifying questions correctly."""
    assert _resolve("Which account should I use?") == FinalizeDisposition.FINALIZE


def test_interactive_id_request_falls_through_to_classifier():
    assert (
        _resolve("Ok I will fetch the information, but first I require your ID")
        == FinalizeDisposition.FINALIZE
    )


def test_autonomous_interrogative_deferral_continues():
    text = "Would you like me to continue processing the remaining unfriending actions?"
    assert _resolve(text, autonomous=True) == FinalizeDisposition.CONTINUE


def test_interactive_same_deferral_asks_user():
    text = "Would you like me to continue processing the remaining unfriending actions?"
    assert _resolve(text, autonomous=False) == FinalizeDisposition.ASK_USER


def test_autonomous_statement_deferral_continues():
    text = "Let me know how you'd like to proceed!"
    assert _resolve(text, autonomous=True) == FinalizeDisposition.CONTINUE


def test_autonomous_statement_deferral_continues_curly_apostrophe():
    text = "Let me know how you’d like to proceed!"
    assert _resolve(text, autonomous=True) == FinalizeDisposition.CONTINUE


def test_autonomous_clarifying_question_falls_through_to_classifier():
    """Ask_user-only text no longer short-circuits CONTINUE in autonomous mode
    either — it falls through to the classifier same as interactive mode."""
    assert _resolve("Which account should I use?", autonomous=True) == FinalizeDisposition.FINALIZE


def test_greeting_finalizes():
    assert _resolve("Hello!") == FinalizeDisposition.FINALIZE


def test_give_up_finalizes_without_bounce():
    """Pattern B is deferred — give-ups finalize (no soft bounce)."""
    text = (
        "We have exhausted all discovered tools and none provide game-level event data. "
        "The number cannot be determined from this API."
    )
    assert _resolve(text) == FinalizeDisposition.FINALIZE


def test_nl_auto_continue_off_skips_planning_continue():
    assert (
        _resolve("We need to search student_loan app.", nl_auto_continue=False)
        == FinalizeDisposition.FINALIZE
    )


def test_nl_auto_continue_off_skips_deferral_too():
    """Kill switch (#732 review): with nl_auto_continue off, deferral text must
    not force a continue even in autonomous mode — the operator's off-switch
    should disable all interception, not just the classifier fallback."""
    text = "Would you like me to continue processing the remaining unfriending actions?"
    assert _resolve(text, autonomous=True, nl_auto_continue=False) == FinalizeDisposition.FINALIZE


def test_deferral_with_unverified_blocker_falls_through_to_classifier():
    """Issue #610: a false refusal phrased as a deferral question must not ship
    an uncorrected bare "continue" — it needs classify_auto_continue's
    blocked-claim override to fire instead (#732 review)."""
    text = "I'm unable to access the Spotify tools. Would you like me to try a different approach?"
    assert _resolve(text, autonomous=True) == FinalizeDisposition.FINALIZE
    assert _resolve(text, autonomous=False) == FinalizeDisposition.FINALIZE


def test_deferral_across_paragraphs_not_detected():
    """The bounded-gap fix (#732 review) must not bridge separate sentences."""
    text = "I can confirm the order shipped.\n\nOnce you receive the package, let me know."
    assert _resolve(text, autonomous=True) == FinalizeDisposition.FINALIZE
