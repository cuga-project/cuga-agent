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
)
from cuga.backend.cuga_graph.policy.observability import (
    append_policy_decisions,
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
    state = SimpleNamespace(policy_decisions=[])
    required = decision_from_metadata(
        {
            "policy_id": "approval-1",
            "policy_name": "Approve deletion",
            "policy_type": "tool_approval",
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

    append_policy_decisions(state, [required, required, approved])

    assert [decision.outcome for decision in state.policy_decisions] == [
        PolicyDecisionOutcome.APPROVAL_REQUIRED,
        PolicyDecisionOutcome.APPROVED,
    ]
    assert serialize_policy_decisions(state)[1]["outcome"] == "approved"


def test_serialize_policy_decisions_accepts_checkpoint_dictionaries():
    state = SimpleNamespace(
        policy_decisions=[
            {
                "policy_id": "checkpoint-guard",
                "policy_name": "Checkpoint guard",
                "policy_type": "intent_guard",
                "action_type": "block_intent",
                "stage": "input",
                "outcome": "blocked",
            }
        ]
    )

    serialized = serialize_policy_decisions(state)

    assert serialized[0]["policy_id"] == "checkpoint-guard"
    assert serialized[0]["outcome"] == "blocked"


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
        policy_decisions=[],
    )

    command, metadata = await PolicyEnactment.check_and_enact(
        state,
        policy_types=[PolicyType.INTENT_GUARD],
    )

    assert metadata is None
    assert command is not None
    assert command.update["policy_decisions"][0]["policy_id"] == "guard-delete"
    assert command.update["policy_decisions"][0]["outcome"] == "blocked"
