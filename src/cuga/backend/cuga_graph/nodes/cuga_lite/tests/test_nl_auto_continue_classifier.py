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
    classify_nl_auto_continue,
    looks_like_planning_text,
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
