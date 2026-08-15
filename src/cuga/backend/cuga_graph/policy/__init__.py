"""Policy system for CUGA agent."""

from typing import TYPE_CHECKING

from cuga.backend.cuga_graph.policy.models import (
    Policy,
    PolicyAction,
    PolicyActionType,
    PolicyMatch,
    PolicyType,
    Playbook,
    IntentGuard,
    ToolGuide,
    ToolGuard,
    ToolApproval,
    OutputFormatter,
    CustomPolicy,
    PlaybookStep,
    IntentGuardResponse,
    AlwaysTrigger,
    AppTrigger,
    KeywordTrigger,
    NaturalLanguageTrigger,
    StateTrigger,
    ToolTrigger,
)
from cuga.backend.cuga_graph.policy.storage import PolicyStorage
from cuga.backend.cuga_graph.policy.agent import PolicyAgent, PolicyContext, PlaybookEnactment
from cuga.backend.cuga_graph.policy.configurable import PolicyConfigurable, check_policy_in_node

if TYPE_CHECKING:
    from cuga.backend.cuga_graph.policy.enactment import PolicyEnactment


def __getattr__(name: str):
    """Lazily expose enactment to avoid loading graph/state dependencies."""
    if name == "PolicyEnactment":
        from cuga.backend.cuga_graph.policy.enactment import PolicyEnactment

        return PolicyEnactment
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "Policy",
    "PolicyAction",
    "PolicyActionType",
    "PolicyMatch",
    "PolicyType",
    "Playbook",
    "IntentGuard",
    "ToolGuide",
    "ToolGuard",
    "ToolApproval",
    "OutputFormatter",
    "CustomPolicy",
    "PlaybookStep",
    "IntentGuardResponse",
    "AlwaysTrigger",
    "AppTrigger",
    "KeywordTrigger",
    "NaturalLanguageTrigger",
    "StateTrigger",
    "ToolTrigger",
    "PolicyStorage",
    "PolicyAgent",
    "PolicyContext",
    "PlaybookEnactment",
    "PolicyConfigurable",
    "check_policy_in_node",
    "PolicyEnactment",
]
