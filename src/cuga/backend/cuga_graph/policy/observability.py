"""Helpers for exposing policy decisions from existing graph metadata."""

from typing import Any, Iterable, Optional

from cuga.backend.cuga_graph.policy.models import (
    PolicyActionType,
    PolicyDecision,
    PolicyDecisionOutcome,
    PolicyDecisionStage,
    PolicyMatch,
    PolicyType,
)


POLICY_DECISIONS_KEY = "policy_decisions"

_ACTION_BY_METADATA_TYPE = {
    PolicyType.INTENT_GUARD.value: PolicyActionType.BLOCK_INTENT,
    PolicyType.PLAYBOOK.value: PolicyActionType.GUIDE_PROMPT,
    PolicyType.TOOL_GUIDE.value: PolicyActionType.TOOL_INJECT_DESCRIPTION,
    PolicyType.TOOL_APPROVAL.value: PolicyActionType.TOOL_REQUIRE_APPROVAL,
    PolicyType.OUTPUT_FORMATTER.value: PolicyActionType.FORMAT_OUTPUT,
    "tool_restriction": PolicyActionType.MODIFY_TOOLS,
    "context_injection": PolicyActionType.INJECT_CONTEXT,
    "log_only": PolicyActionType.LOG_ONLY,
}


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
    tool_name: Optional[str] = None,
    agent_name: Optional[str] = None,
) -> Optional[PolicyDecision]:
    """Create a public decision from policy metadata already stored on state."""
    policy_id = metadata.get("policy_id")
    policy_name = metadata.get("policy_name")
    if not policy_id or not policy_name:
        return None

    raw_policy_type = metadata.get("policy_type", PolicyType.CUSTOM)
    try:
        policy_type = PolicyType(raw_policy_type)
    except (TypeError, ValueError):
        policy_type = PolicyType.CUSTOM

    raw_action_type = metadata.get("action_type")
    try:
        action_type = PolicyActionType(raw_action_type) if raw_action_type else None
    except (TypeError, ValueError):
        action_type = None
    if action_type is None:
        metadata_type = raw_policy_type.value if isinstance(raw_policy_type, PolicyType) else raw_policy_type
        action_type = _ACTION_BY_METADATA_TYPE.get(metadata_type)

    if tool_name is None:
        matched_tools = metadata.get("matched_tools") or []
        required_tools = metadata.get("required_tools") or []
        candidate_tools = matched_tools or required_tools
        if candidate_tools:
            tool_name = str(candidate_tools[0])

    return PolicyDecision(
        policy_id=policy_id,
        policy_name=policy_name,
        policy_type=policy_type,
        action_type=action_type,
        stage=stage,
        outcome=outcome,
        confidence=metadata.get("policy_confidence"),
        reasoning=metadata.get("policy_reasoning"),
        tool_name=tool_name,
        agent_name=agent_name or metadata.get("agent_name"),
    )


def append_policy_decisions(
    metadata: dict[str, Any], decisions: Iterable[Optional[PolicyDecision]]
) -> list[PolicyDecision]:
    """Append ordered decisions to a metadata dict, deduplicating identical events."""
    existing = _valid_policy_decisions(metadata.get(POLICY_DECISIONS_KEY) or [])
    identities = {_decision_identity(item) for item in existing}

    for decision in decisions:
        if decision is None:
            continue
        identity = _decision_identity(decision)
        if identity not in identities:
            existing.append(decision)
            identities.add(identity)

    metadata[POLICY_DECISIONS_KEY] = [item.model_dump(mode="json") for item in existing]
    return existing


def serialize_policy_decisions(metadata: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return checkpoint-safe decisions stored on a policy metadata dict."""
    return [
        item.model_dump(mode="json")
        for item in _valid_policy_decisions((metadata or {}).get(POLICY_DECISIONS_KEY) or [])
    ]


def carry_policy_decisions(source: Optional[dict[str, Any]], target: dict[str, Any]) -> list[PolicyDecision]:
    """Carry an existing decision trail into replacement policy metadata."""
    return append_policy_decisions(
        target,
        [PolicyDecision.model_validate(item) for item in serialize_policy_decisions(source)],
    )


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


def _valid_policy_decisions(items: Iterable[Any]) -> list[PolicyDecision]:
    """Ignore stale malformed entries so observability cannot disable enforcement."""
    decisions = []
    for item in items:
        try:
            decisions.append(PolicyDecision.model_validate(item))
        except (TypeError, ValueError):
            continue
    return decisions
