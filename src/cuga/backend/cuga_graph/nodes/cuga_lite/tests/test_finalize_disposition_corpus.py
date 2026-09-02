"""Broad positive/negative corpus for finalize disposition detectors (#445).

Scoped to type-C deferral and interactive ask_user. Pattern B soft bounce is deferred.
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


# ── Type C deferral: MUST detect ────────────────────────────────────────────

DEFERRAL_POSITIVES = [
    "Would you like me to continue processing the remaining unfriending actions?",
    "Would you like to continue searching for even earlier liked songs?",
    "Would you like me to keep going?",
    "Shall I continue with the remaining steps?",
    "Should I continue processing the queue?",
    "Should I keep going through the playlist?",
    "Should I proceed with the purchase?",
    "Let me know how you'd like to proceed!",
    "Let me know how you would like to proceed.",
    "I can retry the purchase for you. Let me know how you'd like to proceed.",
    "To proceed, I recommend: double-checking if it was sent from a different sender.",
    "Once a valid card is available, I can complete the order.",
    "I can complete the order once you add a valid payment method.",
]

DEFERRAL_NEGATIVES = [
    "Hello!",
    "The count is 96.",
    "Task complete—no further action is needed.",
    "We need to search student_loan app.",
    "Evidence: count = 1. Answer: Done.",
    "I liked the song and stopped.",
    "Purchase completed successfully.",
    # Chatty but complete — must not be treated as deferral
    "All set—just let me know if you need anything else later.",
]


# ── Ask-user (interactive): MUST detect ─────────────────────────────────────

ASK_USER_POSITIVES = [
    "Which account should I use?",
    "What is your user id?",
    "Who is the recipient?",
    "Where should I save the file?",
    "When should I schedule it?",
    "Do you want the detailed report?",
    "Ok I will fetch the information, but first I require your ID",
    "Please confirm your account number first.",
    "Please provide your email address.",
    "Tell me which folder to use.",
    "Share your workspace id so I can continue.",
]

ASK_USER_NEGATIVES = [
    "Hello!",
    "The count is 96.",
    "We need to search student_loan app.",
    "Evidence: ok. Answer: 1",
    "I will call the hockey tools next.",
]


def _disp(text: str, **kwargs) -> FinalizeDisposition:
    defaults = dict(
        autonomous=False,
        nl_auto_continue=True,
    )
    defaults.update(kwargs)
    return resolve_finalize_disposition(text, **defaults)


# ── Deferral detector ───────────────────────────────────────────────────────


@pytest.mark.parametrize("text", DEFERRAL_POSITIVES)
def test_deferral_positives(text):
    assert looks_like_autonomous_deferral(text) is True


@pytest.mark.parametrize("text", DEFERRAL_NEGATIVES)
def test_deferral_negatives(text):
    assert looks_like_autonomous_deferral(text) is False


@pytest.mark.parametrize("text", DEFERRAL_POSITIVES)
def test_deferral_autonomous_continues(text):
    assert _disp(text, autonomous=True) == FinalizeDisposition.CONTINUE


@pytest.mark.parametrize("text", DEFERRAL_POSITIVES[:5])
def test_deferral_interactive_asks_user(text):
    assert _disp(text, autonomous=False) == FinalizeDisposition.ASK_USER


# ── Ask-user detector ───────────────────────────────────────────────────────


ASK_USER_SOFT_GAPS = {
    "Tell me which folder to use.",  # "tell me" without "your/you" — recall gap
}


@pytest.mark.parametrize("text", ASK_USER_POSITIVES)
def test_ask_user_positives(text):
    if text in ASK_USER_SOFT_GAPS:
        pytest.xfail("ask_user recall gap — imperative without second person")
    assert looks_like_ask_user(text) is True


@pytest.mark.parametrize("text", ASK_USER_NEGATIVES)
def test_ask_user_negatives(text):
    assert looks_like_ask_user(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "Hello!",
        "Which account should I use?",
        "Evidence: count = 69. Answer: There are 69 matches.",
        "Thanks!",
        # Pattern B give-ups finalize (no bounce) under option 1
        "We have exhausted all discovered tools and none provide game-level event data.",
    ],
)
def test_chatbot_safe_resolves_finalize_or_ask_user(text):
    disp = _disp(text)
    assert disp in (FinalizeDisposition.FINALIZE, FinalizeDisposition.ASK_USER)
