"""Helpers for collecting policy decisions without exposing raw graph metadata."""

from typing import Any, Iterable, Optional

from cuga.backend.cuga_graph.policy.models import (
    PolicyActionType,
    PolicyDecision,
    PolicyDecisionOutcome,
    PolicyDecisionStage,
    PolicyMatch,
    PolicyType,
)


def decision_from_match(
    policy_match: PolicyMatch,
    *,
    stage: PolicyDecisionStage,
    outcome: Optional[PolicyDecisionOutcome] = None,
    tool_name: Optional[str] = None,
) -> Optional[PolicyDecision]:
    """Create a safe public decision from an internal policy match."""
    if not policy_match.matched or policy_match.policy is None:
        return None

    action_type = policy_match.action.action_type if policy_match.action else None
    if outcome is None:
        if action_type == PolicyActionType.BLOCK_INTENT:
            outcome = PolicyDecisionOutcome.BLOCKED
        elif action_type == PolicyActionType.TOOL_REQUIRE_APPROVAL:
            outcome = PolicyDecisionOutcome.APPROVAL_REQUIRED
        else:
            outcome = PolicyDecisionOutcome.APPLIED

    return PolicyDecision(
        policy_id=policy_match.policy.id,
        policy_name=policy_match.policy.name,
        policy_type=policy_match.policy.type,
        action_type=action_type,
        stage=stage,
        outcome=outcome,
        confidence=policy_match.confidence,
        reasoning=policy_match.reasoning or None,
        tool_name=tool_name,
    )


def decision_from_metadata(
    metadata: dict[str, Any],
    *,
    outcome: PolicyDecisionOutcome,
    stage: PolicyDecisionStage = PolicyDecisionStage.TOOL,
) -> Optional[PolicyDecision]:
    """Create an approval lifecycle decision from sanitized state metadata."""
    policy_id = metadata.get("policy_id")
    policy_name = metadata.get("policy_name")
    if not policy_id or not policy_name:
        return None

    policy_type = metadata.get("policy_type", PolicyType.TOOL_APPROVAL)
    action_type = (
        PolicyActionType.TOOL_REQUIRE_APPROVAL
        if policy_type == PolicyType.TOOL_APPROVAL or policy_type == PolicyType.TOOL_APPROVAL.value
        else None
    )
    return PolicyDecision(
        policy_id=policy_id,
        policy_name=policy_name,
        policy_type=policy_type,
        action_type=action_type,
        stage=stage,
        outcome=outcome,
        confidence=metadata.get("policy_confidence"),
        reasoning=metadata.get("policy_reasoning"),
    )


def append_policy_decisions(
    state: Any, decisions: Iterable[Optional[PolicyDecision]]
) -> list[PolicyDecision]:
    """Append decisions to state in order, deduplicating identical lifecycle events."""
    existing = [
        PolicyDecision.model_validate(item) for item in (getattr(state, "policy_decisions", None) or [])
    ]
    identities = {_decision_identity(item) for item in existing}

    for decision in decisions:
        if decision is None:
            continue
        identity = _decision_identity(decision)
        if identity not in identities:
            existing.append(decision)
            identities.add(identity)

    setattr(state, "policy_decisions", existing)
    return existing


def serialize_policy_decisions(state: Any) -> list[dict[str, Any]]:
    """Serialize decisions for LangGraph command updates and checkpoints."""
    return [
        PolicyDecision.model_validate(item).model_dump(mode="json")
        for item in (getattr(state, "policy_decisions", None) or [])
    ]


def _decision_identity(
    decision: PolicyDecision,
) -> tuple[str, str, str, str, Optional[str], Optional[str]]:
    return (
        decision.policy_id,
        decision.policy_type.value,
        decision.stage.value,
        decision.outcome.value,
        decision.tool_name,
        decision.agent_name,
    )
