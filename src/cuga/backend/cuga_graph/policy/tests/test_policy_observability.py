"""Unit tests for public policy decision collection."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import HumanMessage

from cuga.backend.cuga_graph.policy.configurable import PolicyConfigurable
from cuga.backend.cuga_graph.policy.enactment import PolicyEnactment
from cuga.backend.cuga_graph.policy.models import (
    IntentGuard,
    IntentGuardResponse,
    KeywordTrigger,
    PolicyAction,
    PolicyActionType,
    PolicyDecisionOutcome,
    PolicyDecisionStage,
    PolicyMatch,
    PolicyType,
    ToolGuide,
)
from cuga.backend.cuga_graph.policy.observability import (
    append_policy_decisions,
    carry_policy_decisions,
    decision_from_match,
    decision_from_metadata,
    serialize_policy_decisions,
)

pytestmark = pytest.mark.unit


def _intent_guard_match() -> PolicyMatch:
    policy = IntentGuard(
        id="guard-delete",
        name="Block bulk deletion",
        description="Prevent destructive bulk deletion",
        triggers=[KeywordTrigger(value=["delete all"])],
        response=IntentGuardResponse(response_type="natural_language", content="Request blocked"),
    )
    return PolicyMatch(
        matched=True,
        policy=policy,
        action=PolicyAction(
            action_type=PolicyActionType.BLOCK_INTENT,
            policy_id=policy.id,
            policy_type=PolicyType.INTENT_GUARD,
            content="Request blocked",
        ),
        confidence=0.95,
        reasoning="Bulk deletion matched the destructive-operation guard",
    )


def _tool_guide_match() -> PolicyMatch:
    policy = ToolGuide(
        id="guide-delete",
        name="Safe deletion guide",
        description="Guide deletion tool usage",
        triggers=[KeywordTrigger(value=["delete all"])],
        target_tools=["delete_records"],
        guide_content="Create a backup before deletion.",
    )
    return PolicyMatch(
        matched=True,
        policy=policy,
        action=PolicyAction(
            action_type=PolicyActionType.TOOL_INJECT_DESCRIPTION,
            policy_id=policy.id,
            policy_type=PolicyType.TOOL_GUIDE,
            content=policy.guide_content,
            modifications={
                "target_tools": policy.target_tools,
                "target_apps": policy.target_apps,
                "prepend": policy.prepend,
            },
        ),
        confidence=0.9,
        reasoning="Deletion guide matched",
    )


def test_decision_from_match_exposes_safe_summary():
    decision = decision_from_match(_intent_guard_match(), stage=PolicyDecisionStage.INPUT)

    assert decision is not None
    assert decision.policy_id == "guard-delete"
    assert decision.policy_type == PolicyType.INTENT_GUARD
    assert decision.action_type == PolicyActionType.BLOCK_INTENT
    assert decision.outcome == PolicyDecisionOutcome.BLOCKED
    dumped = decision.model_dump(mode="json")
    assert "content" not in dumped
    assert "triggers" not in dumped


def test_append_preserves_order_and_deduplicates_identical_events():
    metadata = {}
    required = decision_from_metadata(
        {
            "policy_id": "approval-1",
            "policy_name": "Approve deletion",
            "policy_type": "tool_approval",
            "required_tools": ["delete_records"],
        },
        outcome=PolicyDecisionOutcome.APPROVAL_REQUIRED,
    )
    approved = decision_from_metadata(
        {
            "policy_id": "approval-1",
            "policy_name": "Approve deletion",
            "policy_type": "tool_approval",
        },
        outcome=PolicyDecisionOutcome.APPROVED,
    )

    append_policy_decisions(metadata, [required, required, approved])

    assert [decision["outcome"] for decision in metadata["policy_decisions"]] == [
        "approval_required",
        "approved",
    ]
    assert metadata["policy_decisions"][0]["tool_name"] == "delete_records"
    assert serialize_policy_decisions(metadata)[1]["outcome"] == "approved"


def test_serialize_policy_decisions_accepts_checkpoint_dictionaries():
    metadata = {
        "policy_decisions": [
            {
                "policy_id": "checkpoint-guard",
                "policy_name": "Checkpoint guard",
                "policy_type": "intent_guard",
                "action_type": "block_intent",
                "stage": "input",
                "outcome": "blocked",
            }
        ]
    }

    serialized = serialize_policy_decisions(metadata)

    assert serialized[0]["policy_id"] == "checkpoint-guard"
    assert serialized[0]["outcome"] == "blocked"


def test_append_ignores_malformed_checkpoint_entries():
    metadata = {"policy_decisions": [{"policy_id": "incomplete"}]}
    blocked = decision_from_match(_intent_guard_match(), stage=PolicyDecisionStage.INPUT)

    append_policy_decisions(metadata, [blocked])

    assert [item["policy_id"] for item in metadata["policy_decisions"]] == ["guard-delete"]


def test_decision_from_metadata_prefers_matched_tool_over_required_tool():
    decision = decision_from_metadata(
        {
            "policy_id": "approval-1",
            "policy_name": "Approve deletion",
            "policy_type": "tool_approval",
            "required_tools": ["delete_records"],
            "matched_tools": ["delete_all_records"],
        },
        outcome=PolicyDecisionOutcome.APPROVAL_REQUIRED,
    )

    assert decision is not None
    assert decision.tool_name == "delete_all_records"


def test_carry_policy_decisions_preserves_existing_trail_in_replacement_metadata():
    source = {
        "policy_decisions": [
            {
                "policy_id": "guard-1",
                "policy_name": "Guard",
                "policy_type": "intent_guard",
                "action_type": "block_intent",
                "stage": "input",
                "outcome": "blocked",
            }
        ]
    }
    replacement = {"policy_id": "formatter-1", "policy_name": "Formatter"}

    carry_policy_decisions(source, replacement)

    assert replacement["policy_decisions"][0]["policy_id"] == "guard-1"
    assert replacement["policy_decisions"][0]["outcome"] == "blocked"


def test_decision_from_metadata_requires_policy_identity():
    assert decision_from_metadata({}, outcome=PolicyDecisionOutcome.DENIED) is None


@pytest.mark.asyncio
async def test_blocking_enactment_persists_decision_on_command(monkeypatch):
    policy_system = SimpleNamespace(
        match_policy=AsyncMock(return_value=_intent_guard_match()),
        agent=SimpleNamespace(),
    )
    monkeypatch.setattr(PolicyConfigurable, "from_config", lambda _config: policy_system)
    monkeypatch.setattr(
        PolicyConfigurable,
        "create_context_from_state",
        lambda _state, _config: SimpleNamespace(user_input="delete all records"),
    )
    state = SimpleNamespace(
        chat_messages=[HumanMessage(content="delete all records")],
        cuga_lite_metadata={},
    )

    command, metadata = await PolicyEnactment.check_and_enact(
        state,
        policy_types=[PolicyType.INTENT_GUARD],
    )

    assert metadata is None
    assert command is not None
    decisions = command.update["cuga_lite_metadata"]["policy_decisions"]
    assert decisions[0]["policy_id"] == "guard-delete"
    assert decisions[0]["outcome"] == "blocked"


@pytest.mark.asyncio
async def test_blocking_enactment_keeps_independent_guide_decisions(monkeypatch):
    policy_system = SimpleNamespace(
        match_policy=AsyncMock(return_value=_intent_guard_match()),
        agent=SimpleNamespace(check_tool_guide_policies=AsyncMock(return_value=[_tool_guide_match()])),
    )
    monkeypatch.setattr(PolicyConfigurable, "from_config", lambda _config: policy_system)
    monkeypatch.setattr(
        PolicyConfigurable,
        "create_context_from_state",
        lambda _state, _config: SimpleNamespace(user_input="delete all records"),
    )
    state = SimpleNamespace(
        chat_messages=[HumanMessage(content="delete all records")],
        cuga_lite_metadata={},
    )

    command, _metadata = await PolicyEnactment.check_and_enact(
        state,
        policy_types=[PolicyType.INTENT_GUARD, PolicyType.TOOL_GUIDE],
    )

    command_metadata = command.update["cuga_lite_metadata"]
    assert command_metadata["guide_policies"] == [
        {"policy_id": "guide-delete", "policy_name": "Safe deletion guide"}
    ]
    assert [item["policy_id"] for item in command_metadata["policy_decisions"]] == [
        "guard-delete",
        "guide-delete",
    ]


@pytest.mark.asyncio
async def test_cuga_agent_invoke_exposes_real_guard_decision(monkeypatch):
    """Exercise the actual SDK graph boundary, not only the enactment helper."""
    from langchain_core.language_models import FakeListChatModel

    from cuga import CugaAgent
    from cuga.backend.cuga_graph.nodes.answer.final_answer_agent.final_answer_agent import (
        FinalAnswerAgent,
    )

    guard_match = _intent_guard_match()
    no_match = PolicyMatch(matched=False, confidence=0.0, reasoning="No formatter matched")

    class StubPolicyAgent:
        async def match_policy(self, _context, target="intent", policy_types=None):
            return guard_match if target == "intent" else no_match

        async def check_tool_guide_policies(self, _context):
            return []

    policy_system = PolicyConfigurable(agent=StubPolicyAgent())
    policy_system._initialized = True
    agent = CugaAgent(
        tools=[],
        model=FakeListChatModel(responses=["unused"]),
        policy_system=policy_system,
        auto_load_policies=False,
        enable_knowledge=False,
    )
    monkeypatch.setattr(
        FinalAnswerAgent,
        "create",
        staticmethod(lambda: SimpleNamespace(name="FinalAnswerAgent")),
    )

    result = await agent.invoke("delete all records", thread_id="sdk-observability-guard")

    assert result.answer == "Request blocked"
    assert len(result.policy_decisions) == 1
    assert result.policy_decisions[0].policy_id == "guard-delete"
    assert result.policy_decisions[0].outcome == PolicyDecisionOutcome.BLOCKED
