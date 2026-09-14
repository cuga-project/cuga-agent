"""Broad corpus regression test for finalize disposition (#445).

The deferral and ask-user detectors this corpus originally exercised
(``looks_like_autonomous_deferral`` / ``looks_like_ask_user``) were removed
after live AppWorld evidence showed the deterministic deferral regex
net-hurts task completion — see ``finalize_disposition.py``'s module
docstring and the #732 PR comments. ``resolve_finalize_disposition`` now
only special-cases planning text; everything else — deferral, ask-user,
genuine completion — resolves to ``FINALIZE`` and is left to the mode-aware
LLM classifier.

This file is kept as a regression guard: if a future change reintroduces a
deterministic fast-path, it must not fire CONTINUE/ASK_USER on any of this
corpus without re-litigating the live-AppWorld tradeoff documented above.
"""

from __future__ import annotations

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite.finalize_disposition import (
    FinalizeDisposition,
    resolve_finalize_disposition,
)

pytestmark = pytest.mark.unit


# Deferral-shaped text (interrogative and statement-form) that a deterministic
# fast-path could plausibly be tempted to force-continue on.
DEFERRAL_SHAPED = [
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
    "Let me know if it looks good. I can place the order once you confirm. Best, Stephen Mccoy",
]

# Ask-user-shaped text (clarifying questions, input requests) that a
# deterministic fast-path could plausibly be tempted to force-continue on.
ASK_USER_SHAPED = [
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

# Genuine completions / chatty-but-done text that must never be forced to
# continue either.
COMPLETION_SHAPED = [
    "Hello!",
    "The count is 96.",
    "Task complete—no further action is needed.",
    "Evidence: count = 1. Answer: Done.",
    "I liked the song and stopped.",
    "Purchase completed successfully.",
    "All set—just let me know if you need anything else later.",
    "We have exhausted all discovered tools and none provide game-level event data.",
]


@pytest.mark.parametrize("text", DEFERRAL_SHAPED + ASK_USER_SHAPED + COMPLETION_SHAPED)
@pytest.mark.parametrize("autonomous", [True, False])
def test_corpus_never_short_circuits_to_continue_or_ask_user(text, autonomous):
    assert resolve_finalize_disposition(text, autonomous=autonomous) == FinalizeDisposition.FINALIZE


def test_planning_text_still_continues():
    """The one remaining deterministic path stays intact."""
    assert resolve_finalize_disposition("We need to search student_loan app.") == FinalizeDisposition.CONTINUE
