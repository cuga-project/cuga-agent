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


def _playbook(
    *,
    playbook_id: str = "playbook_family_claims",
    name: str = "Family Healthcare Plan Navigation",
    triggers: list[NaturalLanguageTrigger] | None = None,
) -> Playbook:
    if triggers is None:
        triggers = [
            NaturalLanguageTrigger(
                value=["get my daughter's claims", "family member claims"],
                target="intent",
                threshold=0.7,
            ),
        ]
    return Playbook(
        id=playbook_id,
        name=name,
        description="Guide for navigating family healthcare plans and claims",
        triggers=triggers,
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


def _parse_error_chain():
    return _chain_mock(side_effect=OutputParserException("Invalid json output:\nOUTPUT_PARSING_FAILURE"))


@pytest.mark.unit
def test_min_nl_threshold_uses_strictest_trigger():
    triggers = [
        NaturalLanguageTrigger(value=["a"], target="intent", threshold=0.8),
        NaturalLanguageTrigger(value=["b"], target="intent", threshold=0.3),
        NaturalLanguageTrigger(value=["c"], target="agent_response", threshold=0.9),
    ]
    assert PolicyAgent._min_nl_threshold(triggers) == 0.3
    assert PolicyAgent._fallback_confidence(triggers) == 0.3


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
        chain = _parse_error_chain()

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
async def test_conflict_resolution_fallback_uses_min_of_multiple_triggers():
    """Fallback confidence is min(threshold) across multiple NL triggers on one policy."""
    storage = PolicyStorage(collection_name="test_nl_conflict_multi_trigger")
    await storage.initialize_async()

    try:
        playbook = _playbook(
            triggers=[
                NaturalLanguageTrigger(
                    value=["family member claims"],
                    target="intent",
                    threshold=0.8,
                ),
                NaturalLanguageTrigger(
                    value=["daughter claims"],
                    target="intent",
                    threshold=0.6,
                ),
            ]
        )
        await storage.add_policy(playbook)
        agent = PolicyAgent(storage=storage, llm=MagicMock(), embedding_function=None)
        context = PolicyContext(
            user_input="get my daughter's claims",
            chat_messages=[],
            sub_task="",
            agent_response="",
        )
        chain = _parse_error_chain()
        intent_triggers = [t for t in playbook.triggers if t.target == "intent"]

        with patch(
            "cuga.backend.cuga_graph.policy.agent.BaseAgent.get_chain",
            return_value=chain,
        ):
            resolution = await agent._resolve_nl_trigger_conflicts(
                [(playbook, intent_triggers)],
                context,
                target="intent",
                target_text=context.user_input,
            )

        assert resolution is not None
        _, confidence, _ = resolution
        assert confidence == 0.6

        with patch(
            "cuga.backend.cuga_graph.policy.agent.BaseAgent.get_chain",
            return_value=chain,
        ):
            evaluated = await agent._evaluate_natural_language_policies("intent", context)
        assert evaluated is not None
        _, match_confidence, _, _ = evaluated
        assert match_confidence == 0.6
    finally:
        await storage.disconnect()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_conflict_resolution_fallback_threshold_below_half():
    """Thresholds under 0.5 produce fallback confidence under 0.5 (clears that gate)."""
    storage = PolicyStorage(collection_name="test_nl_conflict_low_threshold")
    await storage.initialize_async()

    try:
        playbook = _playbook(
            triggers=[
                NaturalLanguageTrigger(
                    value=["family member claims"],
                    target="intent",
                    threshold=0.8,
                ),
                NaturalLanguageTrigger(
                    value=["loose match"],
                    target="intent",
                    threshold=0.3,
                ),
            ]
        )
        await storage.add_policy(playbook)
        agent = PolicyAgent(storage=storage, llm=MagicMock(), embedding_function=None)
        context = PolicyContext(
            user_input="get my daughter's claims",
            chat_messages=[],
            sub_task="",
            agent_response="",
        )
        chain = _parse_error_chain()

        with patch(
            "cuga.backend.cuga_graph.policy.agent.BaseAgent.get_chain",
            return_value=chain,
        ):
            resolution = await agent._resolve_nl_trigger_conflicts(
                [(playbook, playbook.triggers)],
                context,
                target="intent",
                target_text=context.user_input,
            )
            evaluated = await agent._evaluate_natural_language_policies("intent", context)

        assert resolution is not None
        _, confidence, _ = resolution
        assert confidence == 0.3
        assert evaluated is not None
        _, match_confidence, _, _ = evaluated
        assert match_confidence == 0.3
    finally:
        await storage.disconnect()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_conflict_resolution_fallback_filters_by_target():
    """Evaluate path only applies thresholds for triggers matching the target."""
    storage = PolicyStorage(collection_name="test_nl_conflict_mixed_targets")
    await storage.initialize_async()

    try:
        playbook = _playbook(
            triggers=[
                NaturalLanguageTrigger(
                    value=["family member claims"],
                    target="intent",
                    threshold=0.55,
                ),
                NaturalLanguageTrigger(
                    value=["format claims response"],
                    target="agent_response",
                    threshold=0.95,
                ),
            ]
        )
        await storage.add_policy(playbook)
        agent = PolicyAgent(storage=storage, llm=MagicMock(), embedding_function=None)
        context = PolicyContext(
            user_input="get my daughter's claims",
            chat_messages=[],
            sub_task="",
            agent_response="",
        )
        chain = _parse_error_chain()

        with patch(
            "cuga.backend.cuga_graph.policy.agent.BaseAgent.get_chain",
            return_value=chain,
        ):
            evaluated = await agent._evaluate_natural_language_policies("intent", context)

        assert evaluated is not None
        _, match_confidence, _, _ = evaluated
        assert match_confidence == 0.55
    finally:
        await storage.disconnect()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_conflict_resolution_fallback_uses_first_of_multiple_policies():
    """On parse error, fallback selects index 0 of the candidate list."""
    storage = PolicyStorage(collection_name="test_nl_conflict_multi_policy")
    await storage.initialize_async()

    try:
        first = _playbook(
            playbook_id="playbook_first",
            name="First Playbook",
            triggers=[
                NaturalLanguageTrigger(value=["first intent"], target="intent", threshold=0.65),
            ],
        )
        second = _playbook(
            playbook_id="playbook_second",
            name="Second Playbook",
            triggers=[
                NaturalLanguageTrigger(value=["second intent"], target="intent", threshold=0.9),
            ],
        )
        await storage.add_policy(first)
        await storage.add_policy(second)
        agent = PolicyAgent(storage=storage, llm=MagicMock(), embedding_function=None)
        context = PolicyContext(
            user_input="ambiguous request",
            chat_messages=[],
            sub_task="",
            agent_response="",
        )
        chain = _parse_error_chain()
        candidates = [(first, first.triggers), (second, second.triggers)]

        with patch(
            "cuga.backend.cuga_graph.policy.agent.BaseAgent.get_chain",
            return_value=chain,
        ):
            resolution = await agent._resolve_nl_trigger_conflicts(
                candidates,
                context,
                target="intent",
                target_text=context.user_input,
            )

        assert resolution is not None
        resolved_policy, confidence, _ = resolution
        assert resolved_policy.name == "First Playbook"
        assert confidence == 0.65
    finally:
        await storage.disconnect()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_conflict_resolution_no_llm_returns_none():
    """Defensive early exit: no llm / no query text does not invent a match."""
    agent = PolicyAgent(storage=MagicMock(), llm=None, embedding_function=None)
    playbook = _playbook()
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
    assert resolution is None


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
