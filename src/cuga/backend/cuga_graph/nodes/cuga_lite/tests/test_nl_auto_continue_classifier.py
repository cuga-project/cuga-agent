"""Tests for the NL auto-continue planning-text fast-path.

Covers the deterministic ``looks_like_planning_text`` detector (which guards
against the "planning-text stall" where the agent finalizes a plan instead of
continuing) and confirms the fast-path short-circuits the LLM classifier.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from cuga.backend.cuga_graph.nodes.cuga_lite import nl_auto_continue_classifier as mod
from cuga.backend.cuga_graph.nodes.cuga_lite.nl_auto_continue_classifier import (
    BlockedClaimEvidence,
    classify_nl_auto_continue,
    classify_nl_auto_continue_decision,
    looks_like_planning_text,
    looks_like_unverified_blocker,
)


@pytest.fixture(autouse=True)
def _enable_auto_continue(monkeypatch):
    """The fast-path is gated by the feature flag; ensure it is on for these tests."""
    monkeypatch.setattr(mod.settings.advanced_features, "cuga_lite_nl_auto_continue", True, raising=False)


# ── Real observed stall strings (must be detected as planning) ──────────────
@pytest.mark.parametrize(
    "text",
    [
        "We need to search student_loan app.",
        "We need to discover the tool signatures for codebase_comments",
        "We need to find the right tool first.",
        "Let me search for the available tools.",
        "Let's start by listing the apps.",
        "I'll query the API to get the count.",
        "First, we need to fetch the dataset.",
        "Okay, now I should look up the solution details.",
        "I need to determine which endpoint returns watchers.",
    ],
)
def test_planning_text_detected(text):
    assert looks_like_planning_text(text) is True


# ── Genuine final answers / non-planning text (must NOT be detected) ────────
@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "The count is 96.",
        "Solution 83855 has -99.9015748031496% more watchers than solution 1502.",
        "There are 12 active loans in the student_loan app.",
        "I could not find any matching records.",
        "Do you want me to include archived rows?",  # clarifying question
        "Let me know if you need anything else.",  # 'know' is not an action verb
        "The answer is United States.",
        # Second-person guard (PR #416 review): a plan that also requests user
        # input must not be auto-continued — the user has to reply.
        "Ok i will fetch the infromation, but first i require your ID",
        "I will search the records, but please confirm your account number first.",
    ],
)
def test_non_planning_text_not_detected(text):
    assert looks_like_planning_text(text) is False


def test_long_text_not_detected():
    """A long narrative is treated as substantive content, not a one-line plan."""
    long_text = "We need to " + ("analyze the data and " * 60) + "report it."
    assert len(long_text) > 400
    assert looks_like_planning_text(long_text) is False


@pytest.mark.asyncio
async def test_fast_path_short_circuits_llm():
    """Planning text returns True without ever invoking the LLM classifier."""
    llm = MagicMock()
    llm.ainvoke = AsyncMock()
    result = await classify_nl_auto_continue(llm, "We need to search student_loan app.", None)
    assert result is True
    llm.ainvoke.assert_not_called()


@pytest.mark.asyncio
async def test_non_planning_falls_through_to_llm():
    """Substantive content still consults the LLM classifier."""
    llm = MagicMock()
    resp = MagicMock()
    resp.content = '{"auto_continue": false}'
    llm.ainvoke = AsyncMock(return_value=resp)
    result = await classify_nl_auto_continue(llm, "The count is 96.", None)
    assert result is False
    llm.ainvoke.assert_called_once()


@pytest.mark.asyncio
async def test_disabled_flag_finalizes_planning_text(monkeypatch):
    """With the feature flag off, even planning text must finalize (return False)
    and never consult the LLM classifier — the whole fast-path is gated off."""
    monkeypatch.setattr(mod.settings.advanced_features, "cuga_lite_nl_auto_continue", False, raising=False)
    llm = MagicMock()
    llm.ainvoke = AsyncMock()
    result = await classify_nl_auto_continue(llm, "We need to search student_loan app.", None)
    assert result is False
    llm.ainvoke.assert_not_called()


# ── Unverified-blocker override (issue #610) ────────────────────────────────
#
# Turn-1 "plan → refusal" messages that claim tools/data are unavailable while
# the harness can positively verify otherwise (tools bound, nothing executed,
# retry unspent) must be auto-continued once with a corrective message.

# Verbatim observed failures (AppWorld bundles, gpt-5.6-luna and gpt-oss-120b).
OBSERVED_BLOCKER_STRINGS = [
    "I’m sorry, but I couldn’t access the Amazon cart and wishlist data needed to calculate the total.",
    "I’m sorry, but I couldn’t access the Spotify subscription details needed to calculate the remaining days.",
    "I’m unable to access the Amazon tools needed to identify the last t-shirt order or post a product question in this session.",
    "I’m unable to access the Amazon order or its customer questions/reviews with the currently available tools, so I can’t reliably determine whether the answer is yes or no.",
    "I’m sorry, but I don’t have a tool that can change your Venmo password. You’ll need to update it directly through the Venmo app or website.",
    "I’m unable to locate any Amazon-related tools in the current environment, so I can’t retrieve your account-creation date.",
    "I’m unable to access the Spotify subscription details because the Spotify account tool isn’t available in this session.",
    # gpt-oss-120b, 7574325_1 at default effort (bundle 20260813_153149, 3/3 runs):
    # slipped through the first detector — "unable to change" is not an access verb
    # and "there's no available tool" inverts the tool-unavailable word order.
    "I’m unable to change your Venmo password because there’s no available tool or API for updating Venmo credentials in this environment.",
    "We have no tool listed for Venmo password change. There's no Venmo password change tool.",
]


@pytest.mark.parametrize("text", OBSERVED_BLOCKER_STRINGS)
def test_observed_blocker_strings_detected(text):
    assert looks_like_unverified_blocker(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Done. All 15 artists are followed on Spotify.",
        "The count is 96.",
        "I can help you browse Amazon products, manage Gmail threads, and track expenses.",
        "Which account should I use?",
        "The order was placed successfully. Order ID: 3146.",
        # Positive availability statement — the availability clause requires an
        # explicit negation (PR #657 review): must NOT read as an inability claim.
        "The Spotify tool is available in this session.",
    ],
)
def test_non_blocker_text_not_detected(text):
    assert looks_like_unverified_blocker(text) is False


def _finalize_llm():
    """Mock LLM whose classifier verdict is finalize (auto_continue false)."""
    llm = MagicMock()
    resp = MagicMock()
    resp.content = '{"auto_continue": false}'
    llm.ainvoke = AsyncMock(return_value=resp)
    return llm


_FULL_EVIDENCE = BlockedClaimEvidence(tools_available=True, code_executed=False, retry_used=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("text", OBSERVED_BLOCKER_STRINGS)
async def test_blocked_override_fires_on_turn1_refusal(text):
    decision = await classify_nl_auto_continue_decision(_finalize_llm(), text, None, evidence=_FULL_EVIDENCE)
    assert decision.auto_continue is True
    assert decision.blocked_override is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "evidence",
    [
        BlockedClaimEvidence(tools_available=False, code_executed=False, retry_used=False),
        BlockedClaimEvidence(tools_available=True, code_executed=True, retry_used=False),
        BlockedClaimEvidence(tools_available=True, code_executed=False, retry_used=True),
        None,
    ],
)
async def test_blocked_override_requires_positive_evidence(evidence):
    """Empty tool list (registry down), prior execution, a spent retry, or no
    evidence at all → the refusal finalizes as before."""
    decision = await classify_nl_auto_continue_decision(
        _finalize_llm(), OBSERVED_BLOCKER_STRINGS[0], None, evidence=evidence
    )
    assert decision.auto_continue is False
    assert decision.blocked_override is False


@pytest.mark.asyncio
async def test_blocked_override_ignores_genuine_completion():
    """A tool-free completion contains no inability claim — the override must
    not resurrect `require_tool_call_before_final` (removed in PR #416 review)."""
    decision = await classify_nl_auto_continue_decision(
        _finalize_llm(),
        "I can help you browse Amazon products, manage Gmail threads, and track expenses.",
        None,
        evidence=_FULL_EVIDENCE,
    )
    assert decision.auto_continue is False
    assert decision.blocked_override is False


@pytest.mark.asyncio
async def test_blocked_override_disabled_by_flag(monkeypatch):
    monkeypatch.setattr(mod.settings.advanced_features, "cuga_lite_blocked_claim_retry", False, raising=False)
    decision = await classify_nl_auto_continue_decision(
        _finalize_llm(), OBSERVED_BLOCKER_STRINGS[0], None, evidence=_FULL_EVIDENCE
    )
    assert decision.auto_continue is False


@pytest.mark.asyncio
async def test_bool_wrapper_never_overrides():
    """The back-compat bool API passes no evidence, so behavior is unchanged."""
    result = await classify_nl_auto_continue(_finalize_llm(), OBSERVED_BLOCKER_STRINGS[0], None)
    assert result is False


@pytest.mark.asyncio
async def test_blocked_override_requires_confirmed_finalize_verdict_on_error():
    """PR #657 review, finding 1: a classifier *error* must finalize without the
    override, even with blocker text and full evidence — the override's
    precondition is a confirmed finalize verdict, not the absence of one."""
    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=RuntimeError("transient network error"))
    decision = await classify_nl_auto_continue_decision(
        llm, OBSERVED_BLOCKER_STRINGS[0], None, evidence=_FULL_EVIDENCE
    )
    assert decision.auto_continue is False
    assert decision.blocked_override is False


@pytest.mark.asyncio
async def test_blocked_override_requires_confirmed_finalize_verdict_on_unparsable():
    """Same guard for unparsable classifier output — identical hole, same fix."""
    llm = MagicMock()
    resp = MagicMock()
    resp.content = "definitely not json"
    llm.ainvoke = AsyncMock(return_value=resp)
    decision = await classify_nl_auto_continue_decision(
        llm, OBSERVED_BLOCKER_STRINGS[0], None, evidence=_FULL_EVIDENCE
    )
    assert decision.auto_continue is False
    assert decision.blocked_override is False
