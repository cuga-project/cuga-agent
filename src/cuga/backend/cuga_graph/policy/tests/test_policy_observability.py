"""Unit tests for public policy decision collection."""

import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import HumanMessage

from cuga.backend.cuga_graph.nodes.cuga_lite.nl_auto_continue_classifier import AutoContinueDecision
from cuga.backend.cuga_graph.policy.configurable import PolicyConfigurable
from cuga.backend.cuga_graph.policy.enactment import PolicyEnactment
from cuga.backend.cuga_graph.policy.models import (
    AlwaysTrigger,
    IntentGuard,
    IntentGuardResponse,
    KeywordTrigger,
    OutputFormatter,
    Playbook,
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


def test_policy_enactment_export_is_lazy():
    script = """
import sys

import cuga.backend.cuga_graph.policy as policy

module_name = "cuga.backend.cuga_graph.policy.enactment"
assert "PolicyEnactment" in policy.__all__
assert module_name not in sys.modules
exported = policy.PolicyEnactment
assert module_name in sys.modules
assert exported.__module__ == module_name
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


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


def _playbook_match() -> PolicyMatch:
    policy = Playbook(
        id="playbook-onboard",
        name="Customer onboarding playbook",
        description="Guide customer onboarding requests",
        triggers=[AlwaysTrigger()],
        markdown_content="Follow the customer onboarding process.",
    )
    return PolicyMatch(
        matched=True,
        policy=policy,
        action=PolicyAction(
            action_type=PolicyActionType.GUIDE_PROMPT,
            policy_id=policy.id,
            policy_type=PolicyType.PLAYBOOK,
            content=policy.markdown_content,
            modifications={"steps": []},
        ),
        confidence=0.92,
        reasoning="The request requires the onboarding playbook",
    )


def _output_formatter_match() -> PolicyMatch:
    policy = OutputFormatter(
        id="formatter-json",
        name="JSON response formatter",
        description="Return the onboarding response in the required format",
        triggers=[AlwaysTrigger()],
        format_type="direct",
        format_config='{"status": "formatted"}',
    )
    return PolicyMatch(
        matched=True,
        policy=policy,
        action=PolicyAction(
            action_type=PolicyActionType.FORMAT_OUTPUT,
            policy_id=policy.id,
            policy_type=PolicyType.OUTPUT_FORMATTER,
            content=policy.format_config,
            modifications={},
        ),
        confidence=0.97,
        reasoning="The response requires the JSON formatter",
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
async def test_blocking_enactment_honors_custom_metadata_key_without_adapter(monkeypatch):
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
        supervisor_metadata={},
    )

    command, metadata = await PolicyEnactment.check_and_enact(
        state,
        policy_types=[PolicyType.INTENT_GUARD],
        metadata_key="supervisor_metadata",
    )

    assert metadata is None
    assert command is not None
    assert "cuga_lite_metadata" not in command.update
    decisions = command.update["supervisor_metadata"]["policy_decisions"]
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


@pytest.mark.asyncio
async def test_cuga_agent_invoke_preserves_sequential_input_and_output_decisions(monkeypatch):
    """Exercise the prepare-to-callback metadata trail through the public SDK."""
    from langchain_core.messages import AIMessage

    from cuga import CugaAgent
    from cuga.backend.cuga_graph.nodes.answer.final_answer_agent.final_answer_agent import (
        FinalAnswerAgent,
    )
    from cuga.config import settings

    playbook_match = _playbook_match()
    formatter_match = _output_formatter_match()
    no_match = PolicyMatch(matched=False, confidence=0.0, reasoning="No policy matched")

    class StubPolicyAgent:
        def __init__(self):
            self.calls = []

        async def match_policy(self, context, target="intent", policy_types=None):
            self.calls.append((target, context.agent_response))
            if target == "intent":
                return playbook_match
            if target == "agent_response":
                return formatter_match
            return no_match

        async def check_tool_guide_policies(self, _context):
            return []

    class DeterministicAsyncModel:
        model_name = "policy-observability-test"
        _llm_type = "policy-observability-test"

        def __init__(self):
            self.calls = []

        async def ainvoke(self, messages, config=None):
            self.calls.append(list(messages))
            return AIMessage(content="Original onboarding response")

    stub_policy_agent = StubPolicyAgent()
    policy_system = PolicyConfigurable(agent=stub_policy_agent)
    policy_system._initialized = True
    model = DeterministicAsyncModel()
    agent = CugaAgent(
        tools=[],
        model=model,
        policy_system=policy_system,
        auto_load_policies=False,
        filesystem_sync=False,
        enable_knowledge=False,
        enable_skills=False,
    )
    monkeypatch.setattr(agent, "_build_callbacks", lambda: [])
    monkeypatch.setattr(settings.policy, "enabled", True)
    monkeypatch.setattr(settings.policy, "playbook_refine", False)
    monkeypatch.setattr(settings.agent_spawn, "enabled", False)
    monkeypatch.setattr(
        FinalAnswerAgent,
        "create",
        staticmethod(lambda: SimpleNamespace(name="FinalAnswerAgent")),
    )
    # The adapter calls the decision-returning classifier (#610); a plain False
    # would not carry the blocked_override field the caller reads.
    monkeypatch.setattr(
        "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.graph_adapter.classify_nl_auto_continue_decision",
        AsyncMock(return_value=AutoContinueDecision(auto_continue=False)),
    )
    monkeypatch.setattr(
        "cuga.backend.evolve.memory.build_evolve_special_instructions_extension",
        AsyncMock(return_value=""),
    )
    monkeypatch.setattr(
        "cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_nodes.apply_context_summarization",
        AsyncMock(side_effect=lambda messages, *_args, **_kwargs: messages),
    )
    monkeypatch.setattr(
        "cuga.backend.llm.models.LLMManager.get_model",
        lambda *_args, **_kwargs: model,
    )

    result = await agent.invoke(
        "Help me onboard a customer",
        thread_id="sdk-observability-sequential",
        config={
            "configurable": {
                "enable_todos": False,
                "cuga_lite_bind_tools_mode": "none",
                "cuga_lite_enable_few_shots": False,
                "knowledge_engine": False,
            }
        },
    )

    assert result.answer == '{"status": "formatted"}'
    assert stub_policy_agent.calls == [
        ("intent", None),
        ("agent_response", "Original onboarding response"),
    ]
    assert len(model.calls) == 1
    assert "## Task Guidance" in model.calls[0][-1]["content"]
    assert [
        (decision.policy_id, decision.stage, decision.outcome) for decision in result.policy_decisions
    ] == [
        ("playbook-onboard", PolicyDecisionStage.INPUT, PolicyDecisionOutcome.APPLIED),
        ("formatter-json", PolicyDecisionStage.OUTPUT, PolicyDecisionOutcome.APPLIED),
    ]
