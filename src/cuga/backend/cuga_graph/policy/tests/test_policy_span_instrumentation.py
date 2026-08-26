"""Phase 8 policy span tests: PolicyEnactment.check_and_enact instrumentation.

See docs/traceloop-instrumentation-plan.md Phase 8: policy decisions
(name/type, outcome, latency) must be visible as span attributes independent
of whether the matched policy happened to invoke an LLM. Uses a bare OTel SDK
TracerProvider + InMemorySpanExporter, the same pattern
tests/unit/test_traceloop_node_instrumentation.py established for Phase 7 -
LangGraph's own auto-instrumentation already provides ambient span context
around node execution, so a bare recording span is enough to capture
attributes set via opentelemetry.trace.get_current_span().
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import HumanMessage
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from cuga.backend.cuga_graph.policy.configurable import PolicyConfigurable
from cuga.backend.cuga_graph.policy.enactment import PolicyEnactment
from cuga.backend.cuga_graph.policy.models import (
    AlwaysTrigger,
    IntentGuard,
    IntentGuardResponse,
    KeywordTrigger,
    Playbook,
    PolicyAction,
    PolicyActionType,
    PolicyMatch,
    PolicyType,
)

pytestmark = pytest.mark.unit


def _start_recording_span(monkeypatch):
    """Install a bare OTel SDK TracerProvider (no Traceloop involved) and start
    a real, recording root span, mirroring what LangGraph's own auto-instrumentation
    already does around every node call in production."""
    import opentelemetry.trace as otel_trace_module

    monkeypatch.setattr(otel_trace_module, "_TRACER_PROVIDER", None)
    monkeypatch.setattr(otel_trace_module._TRACER_PROVIDER_SET_ONCE, "_done", False)

    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    otel_trace_module.set_tracer_provider(provider)
    tracer = provider.get_tracer("test")
    return tracer, exporter


def _attrs(exporter) -> dict:
    spans = exporter.get_finished_spans()
    assert spans, "expected at least one finished span"
    return dict(spans[0].attributes)


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


def _stub_policy_system(match_result: PolicyMatch, monkeypatch) -> None:
    policy_system = SimpleNamespace(
        match_policy=AsyncMock(return_value=match_result),
        agent=SimpleNamespace(),
    )
    monkeypatch.setattr(PolicyConfigurable, "from_config", lambda _config: policy_system)
    monkeypatch.setattr(
        PolicyConfigurable,
        "create_context_from_state",
        lambda _state, _config: SimpleNamespace(user_input="delete all records"),
    )


@pytest.mark.asyncio
async def test_check_and_enact_records_blocked_policy_span(monkeypatch):
    tracer, exporter = _start_recording_span(monkeypatch)
    _stub_policy_system(_intent_guard_match(), monkeypatch)
    state = SimpleNamespace(
        chat_messages=[HumanMessage(content="delete all records")],
        cuga_lite_metadata={},
    )

    with tracer.start_as_current_span("test-node-span"):
        await PolicyEnactment.check_and_enact(state, policy_types=[PolicyType.INTENT_GUARD])

    attrs = _attrs(exporter)
    assert attrs["cuga.policy.matched"] is True
    assert attrs["cuga.policy.outcome"] == "blocked"
    assert attrs["cuga.policy.policy_type"] == "intent_guard"
    assert attrs["cuga.policy.policy_id"] == "guard-delete"
    assert attrs["cuga.policy.stage"] == "input"
    assert attrs["cuga.policy.latency_ms"] >= 0
    assert attrs["cuga.policy.reasoning"] == "Bulk deletion matched the destructive-operation guard"


@pytest.mark.asyncio
async def test_check_and_enact_records_applied_policy_span(monkeypatch):
    tracer, exporter = _start_recording_span(monkeypatch)
    _stub_policy_system(_playbook_match(), monkeypatch)
    state = SimpleNamespace(
        chat_messages=[HumanMessage(content="help me onboard a customer")],
        cuga_lite_metadata={},
    )

    with tracer.start_as_current_span("test-node-span"):
        await PolicyEnactment.check_and_enact(state, policy_types=[PolicyType.PLAYBOOK])

    attrs = _attrs(exporter)
    assert attrs["cuga.policy.matched"] is True
    assert attrs["cuga.policy.outcome"] == "applied"
    assert attrs["cuga.policy.policy_type"] == "playbook"
    assert attrs["cuga.policy.policy_id"] == "playbook-onboard"


@pytest.mark.asyncio
async def test_check_and_enact_records_no_match_span(monkeypatch):
    tracer, exporter = _start_recording_span(monkeypatch)
    no_match = PolicyMatch(matched=False, confidence=0.0, reasoning="Nothing matched")
    _stub_policy_system(no_match, monkeypatch)
    state = SimpleNamespace(
        chat_messages=[HumanMessage(content="hello")],
        cuga_lite_metadata={},
    )

    with tracer.start_as_current_span("test-node-span"):
        await PolicyEnactment.check_and_enact(state, policy_types=[PolicyType.INTENT_GUARD])

    attrs = _attrs(exporter)
    assert attrs["cuga.policy.matched"] is False
    assert "cuga.policy.outcome" not in attrs


@pytest.mark.asyncio
async def test_check_and_enact_gates_reasoning_on_trace_content_flag(monkeypatch):
    monkeypatch.setenv("TRACELOOP_TRACE_CONTENT", "false")
    tracer, exporter = _start_recording_span(monkeypatch)
    _stub_policy_system(_intent_guard_match(), monkeypatch)
    state = SimpleNamespace(
        chat_messages=[HumanMessage(content="delete all records")],
        cuga_lite_metadata={},
    )

    with tracer.start_as_current_span("test-node-span"):
        await PolicyEnactment.check_and_enact(state, policy_types=[PolicyType.INTENT_GUARD])

    attrs = _attrs(exporter)
    assert attrs["cuga.policy.outcome"] == "blocked"
    assert "cuga.policy.reasoning" not in attrs
