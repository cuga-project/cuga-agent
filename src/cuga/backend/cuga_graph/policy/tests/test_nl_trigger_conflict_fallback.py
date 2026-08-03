"""Unit tests: NL conflict-resolution fallback must still produce a policy match."""

import pytest
from langchain_core.exceptions import OutputParserException
from unittest.mock import AsyncMock, MagicMock

from cuga.backend.cuga_graph.policy.agent import (
    PolicyAgent,
    PolicyConflictResolution,
    PolicyContext,
)
from cuga.backend.cuga_graph.policy.models import NaturalLanguageTrigger, Playbook
from cuga.backend.cuga_graph.policy.storage import PolicyStorage


def _playbook() -> Playbook:
    return Playbook(
        id="playbook_family_claims",
        name="Family Healthcare Plan Navigation",
        description="Guide for navigating family healthcare plans and claims",
        triggers=[
            NaturalLanguageTrigger(
                value=["get my daughter's claims", "family member claims"],
                target="intent",
                threshold=0.7,
            ),
        ],
        markdown_content="# Family Healthcare Plan Navigation\n\nUse get_plan then get_claims.",
        priority=50,
        enabled=True,
    )


def _llm_with_structured(side_effect_or_result):
    structured = MagicMock()
    structured.ainvoke = AsyncMock(side_effect=side_effect_or_result)
    llm = MagicMock()
    llm.with_structured_output = MagicMock(return_value=structured)
    return llm, structured


@pytest.mark.unit
@pytest.mark.asyncio
async def test_conflict_resolution_error_fallback_meets_threshold():
    """
    When LLM conflict resolution fails to parse JSON, fallback selects the first
    policy. That selection must use confidence >= the policy's NL threshold so
    _evaluate_natural_language_policies does not discard it (issue #577).
    """
    storage = PolicyStorage(collection_name="test_nl_conflict_fallback")
    await storage.initialize_async()

    try:
        playbook = _playbook()
        await storage.add_policy(playbook)

        llm, structured = _llm_with_structured(
            OutputParserException("Invalid json output:\nOUTPUT_PARSING_FAILURE")
        )
        agent = PolicyAgent(storage=storage, llm=llm, embedding_function=None)
        context = PolicyContext(
            user_input="get my daughter's claims",
            chat_messages=[],
            sub_task="",
            agent_response="",
        )

        resolution = await agent._resolve_nl_trigger_conflicts(
            [(playbook, playbook.triggers)],
            context,
            target="intent",
            target_text=context.user_input,
        )
        assert resolution is not None, "Fallback should select first policy on LLM parse error"
        resolved_policy, confidence, reasoning = resolution
        assert resolved_policy.name == "Family Healthcare Plan Navigation"
        assert confidence >= 0.7, (
            f"Fallback confidence {confidence} must meet trigger threshold 0.7 "
            f"(otherwise evaluate path rejects the match). Reasoning: {reasoning}"
        )
        assert structured.ainvoke.await_count == 2, "Should retry once before falling back"

        evaluated = await agent._evaluate_natural_language_policies("intent", context)
        assert evaluated is not None, (
            "NL evaluation must match after conflict-resolution parse failure "
            "(fallback was discarding matches with confidence 0.5 < threshold 0.7)"
        )
        matched_policy, match_confidence, _, _ = evaluated
        assert matched_policy.name == "Family Healthcare Plan Navigation"
        assert match_confidence >= 0.7
    finally:
        await storage.disconnect()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_conflict_resolution_retries_then_succeeds():
    """Parse failure on first attempt should retry and accept a valid second response."""
    storage = PolicyStorage(collection_name="test_nl_conflict_retry")
    await storage.initialize_async()

    try:
        playbook = _playbook()
        await storage.add_policy(playbook)

        success = PolicyConflictResolution(
            matched_policy_index=1,
            confidence=0.9,
            reasoning="Matches family claims playbook",
        )
        llm, structured = _llm_with_structured(
            [
                OutputParserException("Invalid json output:\nOUTPUT_PARSING_FAILURE"),
                success,
            ]
        )
        agent = PolicyAgent(storage=storage, llm=llm, embedding_function=None)
        context = PolicyContext(
            user_input="get my daughter's claims",
            chat_messages=[],
            sub_task="",
            agent_response="",
        )

        resolution = await agent._resolve_nl_trigger_conflicts(
            [(playbook, playbook.triggers)],
            context,
            target="intent",
            target_text=context.user_input,
        )
        assert resolution is not None
        resolved_policy, confidence, reasoning = resolution
        assert resolved_policy.name == "Family Healthcare Plan Navigation"
        assert confidence == 0.9
        assert "LLM conflict resolution" in reasoning
        assert structured.ainvoke.await_count == 2
    finally:
        await storage.disconnect()
