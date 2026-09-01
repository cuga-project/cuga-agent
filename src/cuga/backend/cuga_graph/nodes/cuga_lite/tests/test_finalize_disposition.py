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
    classifier_continue: bool | None = None,
) -> FinalizeDisposition:
    return resolve_finalize_disposition(
        text,
        autonomous=autonomous,
        nl_auto_continue=nl_auto_continue,
        classifier_says_continue=classifier_continue,
    )


def test_planning_continues():
    assert _resolve("We need to search student_loan app.") == FinalizeDisposition.CONTINUE


def test_interactive_clarifying_question_asks_user():
    assert _resolve("Which account should I use?") == FinalizeDisposition.ASK_USER


def test_interactive_id_request_asks_user():
    assert (
        _resolve("Ok I will fetch the information, but first I require your ID")
        == FinalizeDisposition.ASK_USER
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


def test_autonomous_clarifying_question_continues():
    assert _resolve("Which account should I use?", autonomous=True) == FinalizeDisposition.CONTINUE


def test_greeting_finalizes():
    assert _resolve("Hello!") == FinalizeDisposition.FINALIZE


def test_give_up_finalizes_without_bounce():
    """Pattern B is deferred — give-ups finalize (no soft bounce)."""
    text = (
        "We have exhausted all discovered tools and none provide game-level event data. "
        "The number cannot be determined from this API."
    )
    assert _resolve(text) == FinalizeDisposition.FINALIZE


def test_classifier_continue_when_ambiguous():
    assert (
        _resolve("Let me perform the second phase.", classifier_continue=True) == FinalizeDisposition.CONTINUE
    )


def test_nl_auto_continue_off_skips_planning_continue():
    assert (
        _resolve("We need to search student_loan app.", nl_auto_continue=False)
        == FinalizeDisposition.FINALIZE
    )
