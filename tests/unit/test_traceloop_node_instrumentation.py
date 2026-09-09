"""Phase 7 (DP7 per-node audit) tests: browser and cuga_lite subsystems.

(The task_decomposition_planning and api-subsystem sections were removed when
main retired the full CugaAgent graph - #580/#581; those nodes no longer exist.)

Each test wraps the exact qualifying code path in a real, recording OTel span
(no Traceloop.init() needed - a bare TracerProvider + InMemorySpanExporter is
enough to capture attributes set via opentelemetry.trace.get_current_span())
and asserts the expected attribute/value lands on it. See
docs/traceloop-instrumentation-plan.md Phase 7 and docs/traceloop-instrumentation-spec.md
DP7 for the rationale: only code that parses/validates, post-filters,
retries/falls back, branches/routes, or aggregates multiple LLM calls on top
of an LLM's output needs manual instrumentation - "one LLM call, act on it
directly" nodes need nothing (verified empirically that node identity and
ambient span context are already free from LangGraph's own auto-instrumentation).
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

pytestmark = pytest.mark.unit


def _start_recording_span(monkeypatch):
    """Install a bare OTel SDK TracerProvider (no Traceloop involved) and start
    a real, recording root span, mirroring what LangGraph's own auto-instrumentation
    already does around every node call in production (verified empirically -
    see docs/phase7-handoff-prompt.md's DP7 node-context check)."""
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


# ---------------------------------------------------------------------------
# browser_planner_agent.py - cuga.browser_planner.vision_retry (retry/fallback)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_browser_planner_vision_rejection_sets_retry_attribute(monkeypatch):
    from cuga.backend.cuga_graph.nodes.browser.browser_planner_agent import (
        browser_planner_agent as bpa,
    )
    from cuga.backend.cuga_graph.nodes.browser.browser_planner_agent.browser_planner_agent import (
        BrowserPlannerAgent,
    )
    from cuga.backend.cuga_graph.state.agent_state import AgentState

    tracer, exporter = _start_recording_span(monkeypatch)
    monkeypatch.setattr(bpa.tracker, "images", ["data:image/png;base64,REAL"])

    class _RejectingChain:
        def __init__(self):
            self.calls = 0

        async def ainvoke(self, data):
            self.calls += 1
            if self.calls == 1:
                raise ValueError("model does not support vision")
            return AIMessage(content="ok", name="BrowserPlannerAgent")

    agent = object.__new__(BrowserPlannerAgent)
    agent.name = "BrowserPlannerAgent"
    agent.chain = _RejectingChain()
    agent.use_vision_effective = True
    agent._template_requires_img = True

    with tracer.start_as_current_span("test-node-span"):
        await agent.run(AgentState(input="do a task", url=""))

    attrs = _attrs(exporter)
    assert attrs["cuga.browser_planner.vision_retry"] is True
    assert attrs["cuga.browser_planner.vision_rejection_error_type"] == "ValueError"


# ---------------------------------------------------------------------------
# cuga_lite_node.py - cuga.cuga_lite.answer_has_error / fallback_answer_used
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cuga_lite_node_detects_error_in_answer(monkeypatch):
    from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_node import CugaLiteNode
    from cuga.backend.cuga_graph.state.agent_state import AgentState

    tracer, exporter = _start_recording_span(monkeypatch)

    node = object.__new__(CugaLiteNode)
    node.name = "CugaLite"
    node._background_tasks = set()

    state = AgentState(
        input="do something",
        url="",
        elements="",
        sub_task="do it",
        sub_task_app="myapp",
        sub_task_type="api",
        final_answer="Error during execution: sandbox exploded",
        api_planner_history=[],
    )

    with tracer.start_as_current_span("test-node-span"):
        command = await node._process_results(
            state=state, answer=state.final_answer, initial_var_names=[], is_autonomous_subtask=True
        )

    assert command.goto == "FinalAnswerAgent"
    assert _attrs(exporter)["cuga.cuga_lite.answer_has_error"] is True


@pytest.mark.asyncio
async def test_cuga_lite_node_no_error_no_fallback_needed(monkeypatch):
    from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_node import CugaLiteNode
    from cuga.backend.cuga_graph.state.agent_state import AgentState

    tracer, exporter = _start_recording_span(monkeypatch)

    node = object.__new__(CugaLiteNode)
    node.name = "CugaLite"
    node._background_tasks = set()

    state = AgentState(
        input="do something",
        url="",
        elements="",
        sub_task="do it",
        sub_task_app="myapp",
        sub_task_type="api",
        final_answer="All done, here is the result.",
        api_planner_history=[],
    )

    with tracer.start_as_current_span("test-node-span"):
        command = await node._process_results(
            state=state, answer=state.final_answer, initial_var_names=[], is_autonomous_subtask=True
        )

    assert command.goto == "FinalAnswerAgent"
    attrs = _attrs(exporter)
    assert attrs["cuga.cuga_lite.answer_has_error"] is False
    assert attrs["cuga.cuga_lite.fallback_answer_used"] is False


@pytest.mark.asyncio
async def test_cuga_lite_node_empty_answer_uses_fallback(monkeypatch):
    from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_node import CugaLiteNode
    from cuga.backend.cuga_graph.state.agent_state import AgentState

    tracer, exporter = _start_recording_span(monkeypatch)

    node = object.__new__(CugaLiteNode)
    node.name = "CugaLite"
    node._background_tasks = set()

    state = AgentState(
        input="do something",
        url="",
        elements="",
        sub_task="do it",
        sub_task_app="myapp",
        sub_task_type="api",
        final_answer="   ",
        api_planner_history=[],
    )

    with tracer.start_as_current_span("test-node-span"):
        command = await node._process_results(
            state=state, answer=state.final_answer, initial_var_names=[], is_autonomous_subtask=True
        )

    assert command.goto == "FinalAnswerAgent"
    attrs = _attrs(exporter)
    assert attrs["cuga.cuga_lite.answer_has_error"] is False
    assert attrs["cuga.cuga_lite.fallback_answer_used"] is True


# ---------------------------------------------------------------------------
# nl_auto_continue_classifier.py - cuga.nl_auto_continue.decision_path / auto_continue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nl_auto_continue_fast_path(monkeypatch):
    from cuga.backend.cuga_graph.nodes.cuga_lite import nl_auto_continue_classifier as nlac_module
    from cuga.backend.cuga_graph.nodes.cuga_lite.nl_auto_continue_classifier import (
        classify_nl_auto_continue_decision,
    )

    tracer, exporter = _start_recording_span(monkeypatch)
    # Patch via the module's own `settings` reference - see the comment in
    # test_task_analyzer_routes_to_location_resolver for why a freshly
    # re-imported `settings` can silently be the wrong object.
    monkeypatch.setattr(nlac_module.settings.advanced_features, "cuga_lite_nl_auto_continue", True)

    with tracer.start_as_current_span("test-node-span"):
        decision = await classify_nl_auto_continue_decision(
            llm=None, assistant_visible="We need to search the student_loan app.", reasoning_excerpt=None
        )

    assert decision.auto_continue is True
    attrs = _attrs(exporter)
    assert attrs["cuga.nl_auto_continue.decision_path"] == "fast_path"
    assert attrs["cuga.nl_auto_continue.auto_continue"] is True


@pytest.mark.asyncio
async def test_nl_auto_continue_llm_classifies_true(monkeypatch):
    from cuga.backend.cuga_graph.nodes.cuga_lite import nl_auto_continue_classifier as nlac_module
    from cuga.backend.cuga_graph.nodes.cuga_lite.nl_auto_continue_classifier import (
        classify_nl_auto_continue_decision,
    )

    tracer, exporter = _start_recording_span(monkeypatch)
    # Patch via the module's own `settings` reference - see the comment in
    # test_task_analyzer_routes_to_location_resolver for why a freshly
    # re-imported `settings` can silently be the wrong object.
    monkeypatch.setattr(nlac_module.settings.advanced_features, "cuga_lite_nl_auto_continue", True)

    class _FakeLLM:
        async def ainvoke(self, messages, config=None):
            return AIMessage(content='{"auto_continue": true}')

    with tracer.start_as_current_span("test-node-span"):
        decision = await classify_nl_auto_continue_decision(
            llm=_FakeLLM(),
            assistant_visible="Something ambiguous that isn't a clean plan sentence and isn't empty either.",
            reasoning_excerpt=None,
        )

    assert decision.auto_continue is True
    attrs = _attrs(exporter)
    assert attrs["cuga.nl_auto_continue.decision_path"] == "llm_classify_true"


@pytest.mark.asyncio
async def test_nl_auto_continue_llm_classifies_false(monkeypatch):
    from cuga.backend.cuga_graph.nodes.cuga_lite import nl_auto_continue_classifier as nlac_module
    from cuga.backend.cuga_graph.nodes.cuga_lite.nl_auto_continue_classifier import (
        classify_nl_auto_continue_decision,
    )

    tracer, exporter = _start_recording_span(monkeypatch)
    # Patch via the module's own `settings` reference - see the comment in
    # test_task_analyzer_routes_to_location_resolver for why a freshly
    # re-imported `settings` can silently be the wrong object.
    monkeypatch.setattr(nlac_module.settings.advanced_features, "cuga_lite_nl_auto_continue", True)

    class _FakeLLM:
        async def ainvoke(self, messages, config=None):
            return AIMessage(content='{"auto_continue": false}')

    with tracer.start_as_current_span("test-node-span"):
        decision = await classify_nl_auto_continue_decision(
            llm=_FakeLLM(),
            assistant_visible="Done. All 15 artists are followed on Spotify.",
            reasoning_excerpt=None,
        )

    assert decision.auto_continue is False
    assert decision.blocked_override is False
    attrs = _attrs(exporter)
    assert attrs["cuga.nl_auto_continue.decision_path"] == "llm_classify_false"
    assert attrs["cuga.nl_auto_continue.auto_continue"] is False


@pytest.mark.asyncio
async def test_nl_auto_continue_llm_unparsable_output(monkeypatch):
    from cuga.backend.cuga_graph.nodes.cuga_lite import nl_auto_continue_classifier as nlac_module
    from cuga.backend.cuga_graph.nodes.cuga_lite.nl_auto_continue_classifier import (
        classify_nl_auto_continue_decision,
    )

    tracer, exporter = _start_recording_span(monkeypatch)
    # Patch via the module's own `settings` reference - see the comment in
    # test_task_analyzer_routes_to_location_resolver for why a freshly
    # re-imported `settings` can silently be the wrong object.
    monkeypatch.setattr(nlac_module.settings.advanced_features, "cuga_lite_nl_auto_continue", True)

    class _FakeLLM:
        async def ainvoke(self, messages, config=None):
            return AIMessage(content="not json at all")

    with tracer.start_as_current_span("test-node-span"):
        decision = await classify_nl_auto_continue_decision(
            llm=_FakeLLM(),
            assistant_visible="Something ambiguous that isn't a clean plan sentence and isn't empty either.",
            reasoning_excerpt=None,
        )

    assert decision.auto_continue is False
    assert _attrs(exporter)["cuga.nl_auto_continue.decision_path"] == "llm_unparsable"


@pytest.mark.asyncio
async def test_nl_auto_continue_blocked_claim_override(monkeypatch):
    from cuga.backend.cuga_graph.nodes.cuga_lite import nl_auto_continue_classifier as nlac_module
    from cuga.backend.cuga_graph.nodes.cuga_lite.nl_auto_continue_classifier import (
        classify_nl_auto_continue_decision,
        BlockedClaimEvidence,
    )

    tracer, exporter = _start_recording_span(monkeypatch)
    monkeypatch.setattr(nlac_module.settings.advanced_features, "cuga_lite_nl_auto_continue", True)

    class _FakeLLM:
        async def ainvoke(self, messages, config=None):
            return AIMessage(content='{"auto_continue": false}')

    evidence = BlockedClaimEvidence(tools_available=True, code_executed=False, retry_used=False)

    with tracer.start_as_current_span("test-node-span"):
        decision = await classify_nl_auto_continue_decision(
            llm=_FakeLLM(),
            assistant_visible="I'm unable to access the required tools to complete this task.",
            reasoning_excerpt=None,
            evidence=evidence,
        )

    assert decision.auto_continue is True
    assert decision.blocked_override is True
    attrs = _attrs(exporter)
    assert attrs["cuga.nl_auto_continue.decision_path"] == "blocked_override"
    assert attrs["cuga.nl_auto_continue.auto_continue"] is True


# ---------------------------------------------------------------------------
# providers/toolguard.py - cuga.toolguard.blocked_reason
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_toolguard_unexpected_arguments_blocked_reason(monkeypatch):
    from cuga.backend.cuga_graph.nodes.cuga_lite.providers.toolguard import ToolGuardingToolProvider
    from tests.unit.test_toolguard_provider import DummyProvider, _make_recording_tool

    tracer, exporter = _start_recording_span(monkeypatch)

    calls = []
    raw_tool = _make_recording_tool(calls)
    provider = ToolGuardingToolProvider(DummyProvider([raw_tool]), policy_storage=None)
    guarded_tool = (await provider.get_tools("runtime_tools"))[0]

    # The sandbox/CodeAct caller invokes the raw coroutine directly (bypassing
    # StructuredTool.ainvoke()'s own schema validation, which would otherwise
    # silently strip an unknown field before guarded_tool_func ever saw it).
    with tracer.start_as_current_span("test-node-span"):
        result = await guarded_tool.coroutine(
            user_id="uid_1", flight_id="AB12", passengers=2, extra_bad_field="x"
        )

    assert "error" in result
    assert calls == []
    assert _attrs(exporter)["cuga.toolguard.blocked_reason"] == "unexpected_arguments"


@pytest.mark.asyncio
async def test_toolguard_policy_violation_blocked_reason(monkeypatch):
    from cuga.backend.cuga_graph.nodes.cuga_lite.providers.toolguard import ToolGuardingToolProvider
    from tests.unit.test_toolguard_provider import DummyProvider, FakeRuntime, _make_recording_tool

    tracer, exporter = _start_recording_span(monkeypatch)

    calls = []
    raw_tool = _make_recording_tool(calls)
    provider = ToolGuardingToolProvider(DummyProvider([raw_tool]), policy_storage=object())
    runtime = FakeRuntime(error="regular members cannot book more than 3 passengers")

    async def fake_get_runtime():
        return runtime

    provider._get_or_create_toolguard_runtime = fake_get_runtime

    guarded_tool = (await provider.get_tools("runtime_tools"))[0]

    with tracer.start_as_current_span("test-node-span"):
        result = await guarded_tool.ainvoke({"user_id": "uid_1", "flight_id": "AB12", "passengers": 4})

    assert result["blocked_by_policy"] is True
    assert calls == []
    assert _attrs(exporter)["cuga.toolguard.blocked_reason"] == "policy_violation"


# ---------------------------------------------------------------------------
# prompt_utils.py - cuga.shortlister_name_validation.attempts_used / dropped_invalid_count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shortlister_name_validation_succeeds_first_attempt(monkeypatch):
    from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import PromptUtils

    tracer, exporter = _start_recording_span(monkeypatch)

    class _Detail:
        def __init__(self, name):
            self.name = name

    class _Response:
        def __init__(self, names):
            self.result = [_Detail(n) for n in names]

    class _FakeChain:
        async def ainvoke(self, payload, config=None):
            return _Response(["real_tool"])

    with tracer.start_as_current_span("test-node-span"):
        details, invalid = await PromptUtils._ainvoke_shortlister_with_name_validation(
            chain=_FakeChain(),
            query="q",
            apps_as_dict={},
            tools_as_dict={},
            base_instructions="",
            valid_names={"real_tool"},
        )

    assert [d.name for d in details] == ["real_tool"]
    assert invalid == []
    attrs = _attrs(exporter)
    assert attrs["cuga.shortlister_name_validation.attempts_used"] == 1
    assert attrs["cuga.shortlister_name_validation.dropped_invalid_count"] == 0


@pytest.mark.asyncio
async def test_shortlister_name_validation_drops_after_retries_exhausted(monkeypatch):
    from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import PromptUtils

    tracer, exporter = _start_recording_span(monkeypatch)

    class _Detail:
        def __init__(self, name):
            self.name = name

    class _Response:
        def __init__(self, names):
            self.result = [_Detail(n) for n in names]

    class _AlwaysHallucinatingChain:
        async def ainvoke(self, payload, config=None):
            # Every attempt invents a name that is never in valid_names.
            return _Response(["hallucinated_tool"])

    with tracer.start_as_current_span("test-node-span"):
        details, invalid = await PromptUtils._ainvoke_shortlister_with_name_validation(
            chain=_AlwaysHallucinatingChain(),
            query="q",
            apps_as_dict={},
            tools_as_dict={},
            base_instructions="",
            valid_names={"real_tool"},
            max_retries=2,
        )

    assert details == []
    assert invalid == ["hallucinated_tool"]
    attrs = _attrs(exporter)
    assert attrs["cuga.shortlister_name_validation.attempts_used"] == 3
    assert attrs["cuga.shortlister_name_validation.dropped_invalid_count"] == 1


# ---------------------------------------------------------------------------
# shortlister/hybrid.py - cuga.hybrid_shortlister.embedding_unavailable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hybrid_shortlister_embedding_unavailable_fallback(monkeypatch):
    from unittest.mock import AsyncMock, patch as mock_patch

    from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister import (
        ShortlistCandidate,
        ShortlistRequest,
        ShortlistResult,
        ShortlisterUnavailableError,
    )
    from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.embedding import EmbeddingShortlister
    from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.hybrid import HybridShortlister
    from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.llm import LLMShortlister
    from langchain_core.tools import StructuredTool

    tracer, exporter = _start_recording_span(monkeypatch)

    def _tool(name):
        def fn(**kwargs):
            return name

        fn.__name__ = name
        return StructuredTool.from_function(func=fn, name=name, description=name)

    tools = [_tool(f"tool_{i}") for i in range(50)]

    class _RecordingLLM(LLMShortlister):
        async def shortlist(self, request):
            return ShortlistResult(candidates=[ShortlistCandidate(name=request.tools[0].name)])

    hybrid = HybridShortlister(embedding=EmbeddingShortlister("model"), llm=_RecordingLLM())
    request = ShortlistRequest(query="find contacts", tools=tools, apps=[], top_k=10)

    unavailable = AsyncMock(side_effect=ShortlisterUnavailableError("still downloading"))
    with mock_patch.object(EmbeddingShortlister, "shortlist", unavailable):
        with tracer.start_as_current_span("test-node-span"):
            result = await hybrid.shortlist(request)

    assert result.candidates
    assert _attrs(exporter)["cuga.hybrid_shortlister.embedding_unavailable"] is True


@pytest.mark.asyncio
async def test_hybrid_shortlister_embedding_available_no_fallback(monkeypatch):
    from unittest.mock import AsyncMock, patch as mock_patch

    from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister import (
        ShortlistCandidate,
        ShortlistRequest,
        ShortlistResult,
    )
    from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.embedding import EmbeddingShortlister
    from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.hybrid import HybridShortlister
    from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister.llm import LLMShortlister
    from langchain_core.tools import StructuredTool

    tracer, exporter = _start_recording_span(monkeypatch)

    def _tool(name):
        def fn(**kwargs):
            return name

        fn.__name__ = name
        return StructuredTool.from_function(func=fn, name=name, description=name)

    tools = [_tool(f"tool_{i}") for i in range(50)]

    class _RecordingLLM(LLMShortlister):
        async def shortlist(self, request):
            return ShortlistResult(candidates=[ShortlistCandidate(name=request.tools[0].name)])

    hybrid = HybridShortlister(embedding=EmbeddingShortlister("model"), llm=_RecordingLLM())
    request = ShortlistRequest(query="find contacts", tools=tools, apps=[], top_k=10)

    prefiltered = ShortlistResult(candidates=[ShortlistCandidate(name=f"tool_{i}") for i in range(10)])
    with mock_patch.object(EmbeddingShortlister, "shortlist", AsyncMock(return_value=prefiltered)):
        with tracer.start_as_current_span("test-node-span"):
            await hybrid.shortlist(request)

    assert _attrs(exporter)["cuga.hybrid_shortlister.embedding_unavailable"] is False


# ---------------------------------------------------------------------------
# bind_tools/cap.py - cuga.bind_tools_cap.triggered / *_count
# ---------------------------------------------------------------------------


def _bt_stub_tool(name: str):
    from langchain_core.tools import StructuredTool

    return StructuredTool.from_function(func=lambda: None, name=name, description="d")


@pytest.mark.asyncio
async def test_bind_tools_cap_triggered_records_counts(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock, patch as mock_patch

    from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.bind_tools import resolve_model_with_bind_tools

    tracer, exporter = _start_recording_span(monkeypatch)

    tools = [_bt_stub_tool(f"tool_{i:03d}") for i in range(10)]
    provider = AsyncMock()
    provider.get_all_tools = AsyncMock(return_value=tools)
    provider.get_apps = AsyncMock(return_value=[])
    model = MagicMock()

    async def fake_shortlist(
        query, all_tools, all_apps, llm=None, top_k=4, instructions=None, run_config=None
    ):
        return [t.name for t in all_tools[: min(top_k, 3)]]

    with (
        mock_patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.helpers.bind_tools.bind_tools_max_count_from_settings",
            return_value=3,
        ),
        mock_patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools.cap.PromptUtils.shortlist_tool_names",
            side_effect=fake_shortlist,
        ),
    ):
        with tracer.start_as_current_span("test-node-span"):
            await resolve_model_with_bind_tools(
                model,
                configurable={"cuga_lite_bind_tools_mode": "all"},
                tools_context_ref={},
                tool_provider=provider,
                query="find me a hockey scorer",
            )

    attrs = _attrs(exporter)
    assert attrs["cuga.bind_tools_cap.triggered"] is True
    assert attrs["cuga.bind_tools_cap.bound_count"] == 10
    assert attrs["cuga.bind_tools_cap.shortlisted_count"] == 3
    assert attrs["cuga.bind_tools_cap.padded_count"] == 0


@pytest.mark.asyncio
async def test_bind_tools_cap_not_triggered_under_threshold(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock, patch as mock_patch

    from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.bind_tools import resolve_model_with_bind_tools

    tracer, exporter = _start_recording_span(monkeypatch)

    tools = [_bt_stub_tool(f"tool_{i}") for i in range(3)]
    provider = AsyncMock()
    provider.get_all_tools = AsyncMock(return_value=tools)
    provider.get_apps = AsyncMock(return_value=[])
    model = MagicMock()

    with mock_patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.helpers.bind_tools.bind_tools_max_count_from_settings",
        return_value=128,
    ):
        with tracer.start_as_current_span("test-node-span"):
            await resolve_model_with_bind_tools(
                model,
                configurable={"cuga_lite_bind_tools_mode": "all"},
                tools_context_ref={},
                tool_provider=provider,
                query="anything",
            )

    assert _attrs(exporter)["cuga.bind_tools_cap.triggered"] is False


# ---------------------------------------------------------------------------
# helpers/bind_tools.py - cuga.bind_tools.degraded / degraded_reason
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bind_tools_degraded_attribute_on_unsupported_model(monkeypatch):
    from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.bind_tools import _safe_bind
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    tracer, exporter = _start_recording_span(monkeypatch)

    class _NoBindModel(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "no-bind"

        def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])

    with tracer.start_as_current_span("test-node-span"):
        result = _safe_bind(_NoBindModel(), ["tool_a"])

    assert isinstance(result, BaseChatModel)
    attrs = _attrs(exporter)
    assert attrs["cuga.bind_tools.degraded"] is True
    assert "does not support bind_tools" in attrs["cuga.bind_tools.degraded_reason"]


# ---------------------------------------------------------------------------
# adapter/graph_adapter.py - tool_use_failed and empty-content-tool_calls recovery
# ---------------------------------------------------------------------------


def _make_graph_adapter():
    from unittest.mock import MagicMock

    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.graph_adapter import AgentGraphAdapter

    tracker = MagicMock()
    tracker.collect_step = MagicMock()
    return AgentGraphAdapter(
        tracker=tracker,
        base_callbacks=[],
        task_todos_ref=[],
        tools_context_ref={},
        base_tool_provider=None,
    )


@pytest.mark.asyncio
async def test_ainvoke_model_recovers_tool_use_failed(monkeypatch):
    tracer, exporter = _start_recording_span(monkeypatch)
    adapter = _make_graph_adapter()

    err = (
        "Error code: 400 - {'error': {'message': 'Failed to call a function. "
        "tool_use_failed', 'type': 'invalid_request_error', "
        "'failed_generation': '{\"name\": \"python\", \"arguments\": \"print(42)\"}'}}"
    )

    class _RaisingBound:
        async def ainvoke(self, messages, config=None):
            raise Exception(err)

    with tracer.start_as_current_span("test-node-span"):
        result = await adapter.ainvoke_model(_RaisingBound(), [], {})

    assert "print(42)" in result.content
    assert _attrs(exporter)["cuga.graph_adapter.tool_use_failed_recovered"] is True


def test_normalize_response_recovers_empty_content_from_tool_calls(monkeypatch):
    from types import SimpleNamespace

    tracer, exporter = _start_recording_span(monkeypatch)
    adapter = _make_graph_adapter()

    response = SimpleNamespace(
        content="",
        additional_kwargs={},
        tool_calls=[{"name": "python", "args": {"code": "print(1)"}, "id": "call-1"}],
    )

    with tracer.start_as_current_span("test-node-span"):
        content, _reasoning = adapter.normalize_response(response)

    assert content
    assert _attrs(exporter)["cuga.graph_adapter.empty_content_tool_calls_recovered"] is True


# ---------------------------------------------------------------------------
# adapter/sandbox_node.py - execution_error / reflection_failed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sandbox_node_execution_error_attribute(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock, patch as mock_patch

    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter import sandbox_node as sandbox_node_module
    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node import create_sandbox_node

    tracer, exporter = _start_recording_span(monkeypatch)
    monkeypatch.setattr(sandbox_node_module.settings.policy, "enabled", False)

    adapter = _make_graph_adapter()

    state = MagicMock()
    state.variables_manager.get_variable_names.return_value = []
    state.tool_calls = []
    state.thread_id = None
    state.script = "raise ValueError('boom')"
    state.step_count = 0
    state.cuga_lite_max_steps = None
    state.tool_calls_used_run = 0
    state.tool_calls_used_thread = 0

    sandbox = create_sandbox_node(adapter, base_thread_id="t1", base_apps_list=[])

    with mock_patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node.CodeExecutor.eval_with_tools_async",
        new=AsyncMock(side_effect=RuntimeError("sandbox exploded")),
    ):
        with tracer.start_as_current_span("test-node-span"):
            result = await sandbox(state, config=None)

    assert result["error"]
    attrs = _attrs(exporter)
    assert attrs["cuga.sandbox_node.execution_error"] is True
    assert attrs["cuga.sandbox_node.execution_error_type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_sandbox_node_reflection_failure_attribute(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock, patch as mock_patch

    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter import sandbox_node as sandbox_node_module
    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node import create_sandbox_node

    tracer, exporter = _start_recording_span(monkeypatch)
    # Patch via the module's own `settings` reference - see the comment in
    # test_task_analyzer_routes_to_location_resolver for why a freshly
    # re-imported `settings` can silently be the wrong object.
    settings = sandbox_node_module.settings
    monkeypatch.setattr(settings.policy, "enabled", False)
    monkeypatch.setattr(settings.advanced_features, "reflection_enabled", True)

    adapter = _make_graph_adapter()

    state = MagicMock()
    state.variables_manager.get_variable_names.return_value = []
    state.tool_calls = []
    state.thread_id = None
    state.script = "print('hi')"
    state.chat_messages = []
    state.step_count = 0
    state.cuga_lite_max_steps = None
    state.tool_calls_used_run = 0
    state.tool_calls_used_thread = 0

    sandbox = create_sandbox_node(adapter, base_thread_id="t1", base_apps_list=[])

    async def _fake_reflection_agent_ainvoke(*args, **kwargs):
        raise RuntimeError("reflection LLM call failed")

    with (
        mock_patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node.CodeExecutor.eval_with_tools_async",
            new=AsyncMock(return_value=("output text", {})),
        ),
        mock_patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node.reflection_task",
            return_value=MagicMock(ainvoke=AsyncMock(side_effect=RuntimeError("reflection LLM call failed"))),
        ),
        mock_patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node.core_append_with_step_limit",
            return_value=([], None),
        ),
    ):
        with tracer.start_as_current_span("test-node-span"):
            await sandbox(state, config=None)

    attrs = _attrs(exporter)
    assert attrs["cuga.sandbox_node.execution_error"] is False
    assert attrs["cuga.sandbox_node.reflection_failed"] is True


# ---------------------------------------------------------------------------
# adapter/prepare_node.py - cuga.prepare_node.find_tools_enabled / total_tool_count
# ---------------------------------------------------------------------------


def _prepare_node_build_mock_adapter(tool_count: int):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    adapter = MagicMock()
    adapter._task_todos_ref = []
    adapter._tools_context = {}
    adapter._instructions = ""
    adapter._special_instructions = None
    adapter._static_prompt = None
    adapter._thread_id = "test-thread"
    adapter._model = MagicMock()
    adapter.set_metadata = MagicMock()

    tools = []
    for i in range(tool_count):
        t = SimpleNamespace(name=f"tool_{i}", coroutine=None, func=None, args_schema=None)
        tools.append(t)

    adapter._base_tool_provider = MagicMock()
    adapter._base_tool_provider.get_all_tools = AsyncMock(return_value=tools)
    adapter._base_tool_provider.get_apps = AsyncMock(return_value=[])
    adapter._base_tool_provider.get_tools = AsyncMock(return_value=[])

    rendered = MagicMock()
    rendered.to_string = MagicMock(return_value="")
    adapter._prompt_template = MagicMock()
    adapter._prompt_template.invoke = MagicMock(return_value=rendered)
    return adapter


def _prepare_node_make_state(*, chat_messages):
    from types import SimpleNamespace

    return SimpleNamespace(
        chat_messages=chat_messages,
        task_todos=None,
        sub_task=None,
        sub_task_app=None,
        api_intent_relevant_apps=None,
        cuga_lite_metadata=None,
        thread_id="test-thread",
    )


@pytest.mark.asyncio
async def test_prepare_node_find_tools_not_enabled_under_threshold(monkeypatch):
    from unittest.mock import patch as mock_patch

    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.prepare_node import (
        create_prepare_tools_and_apps_node,
    )
    from langchain_core.messages import HumanMessage

    tracer, exporter = _start_recording_span(monkeypatch)

    adapter = _prepare_node_build_mock_adapter(tool_count=3)
    state = _prepare_node_make_state(chat_messages=[HumanMessage(content="hi")])
    configurable = {"enable_todos": False, "shortlisting_tool_threshold": 35}

    with mock_patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.prepare_node.settings.policy.enabled",
        new=False,
    ):
        node = create_prepare_tools_and_apps_node(adapter, lc_bind_tools_meta={})
        with tracer.start_as_current_span("test-node-span"):
            await node(state, config={"configurable": configurable})

    attrs = _attrs(exporter)
    assert attrs["cuga.prepare_node.find_tools_enabled"] is False
    assert attrs["cuga.prepare_node.total_tool_count"] == 3


@pytest.mark.asyncio
async def test_prepare_node_find_tools_enabled_over_threshold(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock, patch as mock_patch

    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.prepare_node import (
        create_prepare_tools_and_apps_node,
    )
    from langchain_core.messages import HumanMessage

    tracer, exporter = _start_recording_span(monkeypatch)

    adapter = _prepare_node_build_mock_adapter(tool_count=50)
    state = _prepare_node_make_state(chat_messages=[HumanMessage(content="hi")])
    configurable = {"enable_todos": False, "shortlisting_tool_threshold": 35}

    stub_find_tool = MagicMock()
    stub_find_tool.name = "find_tools"
    stub_find_tool.coroutine = AsyncMock(return_value="ok")
    stub_find_tool.func = None

    with (
        mock_patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.prepare_node.settings.policy.enabled",
            new=False,
        ),
        mock_patch(
            "cuga.backend.cuga_graph.nodes.cuga_lite.adapter.prepare_node.create_find_tools_tool",
            new=AsyncMock(return_value=stub_find_tool),
        ),
    ):
        node = create_prepare_tools_and_apps_node(adapter, lc_bind_tools_meta={})
        with tracer.start_as_current_span("test-node-span"):
            await node(state, config={"configurable": configurable})

    attrs = _attrs(exporter)
    assert attrs["cuga.prepare_node.find_tools_enabled"] is True
    assert attrs["cuga.prepare_node.total_tool_count"] == 50


# ---------------------------------------------------------------------------
# helpers/find_tools.py - cuga.find_tools.shortlist_failed / failure_type
# ---------------------------------------------------------------------------


async def _get_find_tools_func():
    from unittest.mock import MagicMock

    from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.find_tools import create_find_tools_tool

    tool = MagicMock()
    tool.name = "test_tool"
    tool.description = "A test tool"
    app = MagicMock()
    app.name = "test_app"

    created = await create_find_tools_tool(
        all_tools=[tool],
        all_apps=[app],
        app_to_tools_map={"test_app": [tool]},
    )
    return created.coroutine or created.func


@pytest.mark.asyncio
async def test_find_tools_parser_exception_sets_failure_attrs(monkeypatch):
    from unittest.mock import AsyncMock, patch as mock_patch

    from langchain_core.exceptions import OutputParserException

    tracer, exporter = _start_recording_span(monkeypatch)
    func = await _get_find_tools_func()

    with mock_patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.helpers.find_tools.PromptUtils.find_tools",
        new_callable=AsyncMock,
        side_effect=OutputParserException("Invalid json output: "),
    ):
        with tracer.start_as_current_span("test-node-span"):
            await func(query="find contacts", app_name="test_app")

    attrs = _attrs(exporter)
    assert attrs["cuga.find_tools.shortlist_failed"] is True
    assert attrs["cuga.find_tools.failure_type"] == "parser_error"


@pytest.mark.asyncio
async def test_find_tools_success_sets_failed_false(monkeypatch):
    from unittest.mock import AsyncMock, patch as mock_patch

    tracer, exporter = _start_recording_span(monkeypatch)
    func = await _get_find_tools_func()

    with mock_patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.helpers.find_tools.PromptUtils.find_tools",
        new_callable=AsyncMock,
        return_value="## tools",
    ):
        with tracer.start_as_current_span("test-node-span"):
            await func(query="find contacts", app_name="test_app")

    assert _attrs(exporter)["cuga.find_tools.shortlist_failed"] is False


# ---------------------------------------------------------------------------
# providers/registry.py - cuga.tool_call.blocked_reason
# ---------------------------------------------------------------------------


def _place_order_tool():
    from cuga.backend.cuga_graph.nodes.cuga_lite.providers.registry import create_tool_from_api_dict

    return create_tool_from_api_dict(
        tool_name="place_order",
        tool_def={
            "description": "place an order",
            "parameters": {
                "properties": {
                    "product_id": {"type": "integer"},
                    "quantity": {"type": "integer"},
                },
                "required": ["product_id", "quantity"],
            },
        },
        app_name="shop",
    )


@pytest.mark.asyncio
async def test_registry_tool_unexpected_argument_blocked_reason(monkeypatch):
    from unittest.mock import AsyncMock, patch as mock_patch

    tracer, exporter = _start_recording_span(monkeypatch)
    tool = _place_order_tool()

    with mock_patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.providers.registry.call_api", new_callable=AsyncMock
    ):
        with tracer.start_as_current_span("test-node-span"):
            result = await tool.coroutine({"product_id": 1, "quantity": 2, "currency": "USD"})

    assert "error" in result
    assert _attrs(exporter)["cuga.tool_call.blocked_reason"] == "unexpected_arguments"


@pytest.mark.asyncio
async def test_registry_tool_validation_error_blocked_reason(monkeypatch):
    from unittest.mock import AsyncMock, patch as mock_patch

    tracer, exporter = _start_recording_span(monkeypatch)
    tool = _place_order_tool()

    with mock_patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.providers.registry.call_api", new_callable=AsyncMock
    ):
        with tracer.start_as_current_span("test-node-span"):
            result = await tool.coroutine({"product_id": 1, "quantity": "two"})

    assert "error" in result
    assert _attrs(exporter)["cuga.tool_call.blocked_reason"] == "validation_error"


# ---------------------------------------------------------------------------
# providers/combined.py - cuga.tool_call.blocked_reason (incl. timeout)
# ---------------------------------------------------------------------------


def _tracker_tool():
    from cuga.backend.cuga_graph.nodes.cuga_lite.providers.combined import create_tool_from_tracker

    return create_tool_from_tracker(
        tool_name="place_order",
        tool_def={
            "description": "place an order",
            "parameters": {
                "properties": {
                    "product_id": {"type": "integer"},
                    "quantity": {"type": "integer"},
                },
                "required": ["product_id", "quantity"],
            },
        },
        app_name="shop",
    )


@pytest.mark.asyncio
async def test_combined_tool_unexpected_argument_blocked_reason(monkeypatch):
    tracer, exporter = _start_recording_span(monkeypatch)
    tool = _tracker_tool()

    with tracer.start_as_current_span("test-node-span"):
        result = await tool.coroutine({"product_id": 1, "quantity": 2, "currency": "USD"})

    assert "error" in result
    assert _attrs(exporter)["cuga.tool_call.blocked_reason"] == "unexpected_arguments"


@pytest.mark.asyncio
async def test_combined_tool_timeout_blocked_reason(monkeypatch):
    import asyncio as _asyncio
    from unittest.mock import patch as mock_patch

    tracer, exporter = _start_recording_span(monkeypatch)
    tool = _tracker_tool()

    async def _fake_wait_for(awaitable, timeout):
        awaitable.close()  # avoid a "coroutine was never awaited" leak warning
        raise _asyncio.TimeoutError()

    with mock_patch(
        "cuga.backend.cuga_graph.nodes.cuga_lite.providers.combined.asyncio.wait_for",
        side_effect=_fake_wait_for,
    ):
        with tracer.start_as_current_span("test-node-span"):
            with pytest.raises(TimeoutError):
                await tool.coroutine({"product_id": 1, "quantity": 2})

    assert _attrs(exporter)["cuga.tool_call.blocked_reason"] == "timeout"
