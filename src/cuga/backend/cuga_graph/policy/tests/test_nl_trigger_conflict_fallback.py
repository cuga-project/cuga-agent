"""Unit tests: NL conflict-resolution fallback must still produce a policy match."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.exceptions import OutputParserException

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


def _chain_mock(*, return_value=None, side_effect=None):
    chain = MagicMock()
    if side_effect is not None:
        chain.ainvoke = AsyncMock(side_effect=side_effect)
    else:
        chain.ainvoke = AsyncMock(return_value=return_value)
    return chain


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

        agent = PolicyAgent(storage=storage, llm=MagicMock(), embedding_function=None)
        context = PolicyContext(
            user_input="get my daughter's claims",
            chat_messages=[],
            sub_task="",
            agent_response="",
        )
        chain = _chain_mock(side_effect=OutputParserException("Invalid json output:\nOUTPUT_PARSING_FAILURE"))

        with patch(
            "cuga.backend.cuga_graph.policy.agent.BaseAgent.get_chain",
            return_value=chain,
        ) as get_chain:
            resolution = await agent._resolve_nl_trigger_conflicts(
                [(playbook, playbook.triggers)],
                context,
                target="intent",
                target_text=context.user_input,
            )
            get_chain.assert_called_once()
            assert get_chain.call_args.args[2] is PolicyConflictResolution

        assert resolution is not None, "Fallback should select first policy on LLM parse error"
        resolved_policy, confidence, reasoning = resolution
        assert resolved_policy.name == "Family Healthcare Plan Navigation"
        assert confidence >= 0.7, (
            f"Fallback confidence {confidence} must meet trigger threshold 0.7 "
            f"(otherwise evaluate path rejects the match). Reasoning: {reasoning}"
        )

        with patch(
            "cuga.backend.cuga_graph.policy.agent.BaseAgent.get_chain",
            return_value=chain,
        ):
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
async def test_conflict_resolution_uses_base_agent_chain_result():
    """Successful BaseAgent.get_chain structured output is used as the match."""
    storage = PolicyStorage(collection_name="test_nl_conflict_chain_ok")
    await storage.initialize_async()

    try:
        playbook = _playbook()
        await storage.add_policy(playbook)

        success = PolicyConflictResolution(
            matched_policy_index=1,
            confidence=0.9,
            reasoning="Matches family claims playbook",
        )
        agent = PolicyAgent(storage=storage, llm=MagicMock(), embedding_function=None)
        context = PolicyContext(
            user_input="get my daughter's claims",
            chat_messages=[],
            sub_task="",
            agent_response="",
        )
        chain = _chain_mock(return_value=success)

        with patch(
            "cuga.backend.cuga_graph.policy.agent.BaseAgent.get_chain",
            return_value=chain,
        ) as get_chain:
            resolution = await agent._resolve_nl_trigger_conflicts(
                [(playbook, playbook.triggers)],
                context,
                target="intent",
                target_text=context.user_input,
            )
            get_chain.assert_called_once()
            assert get_chain.call_args.args[2] is PolicyConflictResolution

        assert resolution is not None
        resolved_policy, confidence, reasoning = resolution
        assert resolved_policy.name == "Family Healthcare Plan Navigation"
        assert confidence == 0.9
        assert "LLM conflict resolution" in reasoning
        chain.ainvoke.assert_awaited_once()
    finally:
        await storage.disconnect()
