"""Phase 7 (DP7 per-node audit) tests: task_decomposition_planning subsystem.

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
# task_decomposition_agent.py - cuga.decomposition_strategy
# ---------------------------------------------------------------------------


class _FakeSingleChain:
    def __init__(self, message: AIMessage):
        self._message = message

    async def ainvoke(self, data):
        return self._message


class _FakeMultiChain:
    def __init__(self, output):
        self._output = output

    async def ainvoke(self, data):
        return self._output


def _make_task_decomposition_agent(single_message=None, multi_output=None):
    from cuga.backend.cuga_graph.nodes.task_decomposition_planning.task_decomposition_agent.task_decomposition_agent import (
        TaskDecompositionAgent,
    )

    agent = object.__new__(TaskDecompositionAgent)
    agent.name = "TaskDecompositionAgent"
    agent.chain = _FakeSingleChain(single_message) if single_message is not None else None
    agent.chain_multi = _FakeMultiChain(multi_output) if multi_output is not None else None
    return agent


@pytest.mark.asyncio
async def test_decomposition_strategy_attribute_single_site(monkeypatch):
    from cuga.backend.cuga_graph.state.agent_state import AgentState

    tracer, exporter = _start_recording_span(monkeypatch)
    monkeypatch.setattr(
        "cuga.backend.cuga_graph.nodes.task_decomposition_planning.task_decomposition_agent.task_decomposition_agent.instructions_manager.get_instructions",
        lambda name: "",
    )

    agent = _make_task_decomposition_agent(single_message=AIMessage(content="{}"))
    state = AgentState(input="do something", url="", elements="", sites=["only-one-site"])

    with tracer.start_as_current_span("test-node-span"):
        await agent.run(state)

    assert _attrs(exporter)["cuga.decomposition_strategy"] == "single"


@pytest.mark.asyncio
async def test_decomposition_strategy_attribute_multi_site(monkeypatch):
    from cuga.backend.cuga_graph.state.agent_state import AgentState
    from cuga.backend.cuga_graph.nodes.task_decomposition_planning.task_decomposition_agent.prompts.load_prompt import (
        TaskDecompositionMultiOutput,
    )

    tracer, exporter = _start_recording_span(monkeypatch)
    monkeypatch.setattr(
        "cuga.backend.cuga_graph.nodes.task_decomposition_planning.task_decomposition_agent.task_decomposition_agent.instructions_manager.get_instructions",
        lambda name: "",
    )

    multi_output = TaskDecompositionMultiOutput(
        thoughts=["t"],
        app_1="reddit",
        task_1_description="task one",
        app_2="gitlab",
        task_2_description="task two",
    )
    agent = _make_task_decomposition_agent(multi_output=multi_output)
    state = AgentState(input="do something", url="", elements="", sites=["site-a", "site-b"])

    with tracer.start_as_current_span("test-node-span"):
        await agent.run(state)

    assert _attrs(exporter)["cuga.decomposition_strategy"] == "multi"


# ---------------------------------------------------------------------------
# analyze_task.py - cuga.app_match.* (post-filtering) and cuga.task_analyzer.routed_to
# ---------------------------------------------------------------------------


def test_resolve_relevant_apps_sets_app_match_attributes(monkeypatch):
    from cuga.backend.cuga_graph.nodes.task_decomposition_planning.analyze_task import TaskAnalyzer
    from cuga.backend.tools_env.registry.utils.types import AppDefinition

    tracer, exporter = _start_recording_span(monkeypatch)
    apps = [AppDefinition(name="venmo", description="Payments app", url="https://venmo.com")]

    with tracer.start_as_current_span("test-node-span"):
        resolved = TaskAnalyzer.resolve_relevant_apps(["venom", "not_a_real_app"], apps)

    assert resolved == ["venmo"]
    attrs = _attrs(exporter)
    assert attrs["cuga.app_match.requested_count"] == 2
    assert attrs["cuga.app_match.resolved_count"] == 1
    assert attrs["cuga.app_match.dropped_count"] == 1


@pytest.mark.asyncio
async def test_task_analyzer_routes_to_location_resolver(monkeypatch):
    from cuga.backend.cuga_graph.nodes.task_decomposition_planning.analyze_task import TaskAnalyzer
    from cuga.backend.cuga_graph.nodes.task_decomposition_planning.task_analyzer_agent.task_analyzer_agent import (
        AnalyzeTaskOutput,
    )
    from cuga.backend.cuga_graph.nodes.task_decomposition_planning.task_analyzer_agent.tasks.classify_task import (
        Attributes,
    )
    from cuga.backend.cuga_graph.state.agent_state import AgentState
    from cuga.config import settings

    tracer, exporter = _start_recording_span(monkeypatch)
    monkeypatch.setattr(settings.advanced_features, "mode", "web")
    monkeypatch.setattr(settings.advanced_features, "use_location_resolver", True)
    monkeypatch.setattr(settings.advanced_features, "benchmark", "not-appworld")
    monkeypatch.setattr(settings.supervisor, "enabled", False)
    monkeypatch.setattr(settings.advanced_features, "lite_mode", False)

    analyze_output = AnalyzeTaskOutput(
        attrs=Attributes(
            thoughts=[],
            performs_update=False,
            requires_memory=False,
            requires_loop=False,
            requires_location_search=True,
        )
    )

    class _FakeAgent:
        async def run(self, state):
            return AIMessage(content=analyze_output.model_dump_json())

    state = AgentState(
        input="go to the place near GCG",
        url="",
        elements="",
        current_app="map",
        current_app_description="OpenStreetMap",
        sites=["map-site"],
        sender=None,
    )

    with tracer.start_as_current_span("test-node-span"):
        command = await TaskAnalyzer.node_handler(state, _FakeAgent(), "TaskAnalyzerAgent")

    assert command.goto == "LocationResolver"
    assert _attrs(exporter)["cuga.task_analyzer.routed_to"] == "LocationResolver"


# ---------------------------------------------------------------------------
# task_analyzer_agent.py - cuga.task_analyzer.read_only_deep_analysis
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_analyzer_agent_skips_read_only_deep_analysis(monkeypatch):
    from cuga.backend.cuga_graph.nodes.task_decomposition_planning.task_analyzer_agent.task_analyzer_agent import (
        TaskAnalyzerAgent,
    )
    from cuga.backend.cuga_graph.state.agent_state import AgentState
    from cuga.config import settings

    tracer, exporter = _start_recording_span(monkeypatch)
    monkeypatch.setattr(settings.advanced_features, "benchmark", "not-webarena")

    agent = object.__new__(TaskAnalyzerAgent)
    agent.name = "TaskAnalyzerAgent"
    state = AgentState(input="do something", url="", elements="", current_app="shopping")

    with tracer.start_as_current_span("test-node-span"):
        await agent.run(state)

    assert _attrs(exporter)["cuga.task_analyzer.read_only_deep_analysis"] is False


@pytest.mark.asyncio
async def test_task_analyzer_agent_takes_read_only_deep_analysis_branch(monkeypatch):
    from cuga.backend.cuga_graph.nodes.task_decomposition_planning.task_analyzer_agent.task_analyzer_agent import (
        TaskAnalyzerAgent,
    )
    from cuga.backend.cuga_graph.nodes.task_decomposition_planning.task_analyzer_agent.tasks.navigation_paths_task import (
        Approaches,
    )
    from cuga.backend.cuga_graph.state.agent_state import AgentState
    from cuga.config import settings

    tracer, exporter = _start_recording_span(monkeypatch)
    monkeypatch.setattr(settings.advanced_features, "benchmark", "not-webarena")
    monkeypatch.setattr(settings.advanced_features, "use_paraphrase", False)

    class _FakeBoundRunnable:
        def __init__(self, result):
            self._result = result

        async def ainvoke(self, data):
            return self._result

    class _FakeNavigationTask:
        def __init__(self, result):
            self._result = result

        def with_config(self, **kwargs):
            return _FakeBoundRunnable(self._result)

    agent = object.__new__(TaskAnalyzerAgent)
    agent.name = "TaskAnalyzerAgent"
    agent.navigation_paths_task = _FakeNavigationTask(Approaches(thoughts=[], approaches=[]))
    # gitlab/shopping_admin is a read-only app -> the deep-analysis branch is taken
    state = AgentState(input="read something", url="", elements="", current_app="gitlab")

    with tracer.start_as_current_span("test-node-span"):
        await agent.run(state)

    assert _attrs(exporter)["cuga.task_analyzer.read_only_deep_analysis"] is True


# ---------------------------------------------------------------------------
# plan_controller.py - cuga.plan_controller.llm_call_skipped and open_app_*
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plan_controller_llm_call_skipped_attribute(monkeypatch):
    from cuga.backend.cuga_graph.nodes.task_decomposition_planning.plan_controller import PlanControllerNode
    from cuga.backend.cuga_graph.nodes.task_decomposition_planning.task_decomposition_agent.prompts.load_prompt import (
        TaskDecompositionPlan,
        DecomposedTask,
    )
    from cuga.backend.cuga_graph.state.agent_state import AgentState

    tracer, exporter = _start_recording_span(monkeypatch)

    state = AgentState(
        input="do something",
        url="",
        elements="",
        sender="BrowserPlannerAgent",
        last_planner_answer="the final answer",
        sub_task="do the thing",
        sub_task_app="reddit",
        sub_task_type="api",
        task_decomposition=TaskDecompositionPlan(
            thoughts="t", task_decomposition=[DecomposedTask(task="do the thing", app="reddit", type="api")]
        ),
    )

    with tracer.start_as_current_span("test-node-span"):
        command = await PlanControllerNode.node_handler(state, None, "PlanControllerAgent", config={})

    assert command.goto == "FinalAnswerAgent"
    assert _attrs(exporter)["cuga.plan_controller.llm_call_skipped"] is True


@pytest.mark.asyncio
async def test_plan_controller_open_app_detected_attribute(monkeypatch):
    from cuga.backend.cuga_graph.nodes.task_decomposition_planning.plan_controller import PlanControllerNode
    from cuga.backend.cuga_graph.nodes.task_decomposition_planning.plan_controller_agent.prompts.load_prompt import (
        PlanControllerOutput,
    )
    from cuga.backend.cuga_graph.nodes.task_decomposition_planning.task_decomposition_agent.prompts.load_prompt import (
        TaskDecompositionPlan,
        DecomposedTask,
    )
    from cuga.backend.cuga_graph.state.agent_state import AgentState

    tracer, exporter = _start_recording_span(monkeypatch)

    plan_controller_output = PlanControllerOutput(
        thoughts=[],
        subtasks_progress=["in-progress"],
        next_subtask="please open application reddit now",
        next_subtask_type=None,
        next_subtask_app="",
        conclude_task=False,
        conclude_final_answer="",
    )

    class _FakeAgent:
        async def run(self, state):
            return AIMessage(content=plan_controller_output.model_dump_json())

    state = AgentState(
        input="do something",
        url="",
        elements="",
        sender="BrowserPlannerAgent",
        last_planner_answer=None,
        task_decomposition=TaskDecompositionPlan(
            thoughts="t", task_decomposition=[DecomposedTask(task="do the thing", app="reddit", type="api")]
        ),
    )

    with tracer.start_as_current_span("test-node-span"):
        command = await PlanControllerNode.node_handler(state, _FakeAgent(), "PlanControllerAgent", config={})

    assert command.goto == "InterruptToolNode"
    attrs = _attrs(exporter)
    assert attrs["cuga.plan_controller.open_app_detected"] is True
    assert attrs["cuga.plan_controller.open_app_matched"] is True


# ---------------------------------------------------------------------------
# task_decomposition.py - cuga.task_decomposition.appworld_type_rewrites
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_decomposition_appworld_type_rewrite_attribute(monkeypatch):
    from cuga.backend.cuga_graph.nodes.task_decomposition_planning.task_decomposition import (
        TaskDecompositionNode,
    )
    from cuga.backend.cuga_graph.nodes.task_decomposition_planning.task_decomposition_agent.prompts.load_prompt import (
        TaskDecompositionPlan,
        DecomposedTask,
    )
    from cuga.backend.cuga_graph.state.agent_state import AgentState
    from cuga.config import settings

    tracer, exporter = _start_recording_span(monkeypatch)
    monkeypatch.setattr(settings.features, "task_decomposition", True)
    monkeypatch.setattr(settings.advanced_features, "benchmark", "appworld")

    task_decomposition_plan = TaskDecompositionPlan(
        thoughts="t",
        task_decomposition=[
            DecomposedTask(task="task one", app="app1", type="web"),
            DecomposedTask(task="task two", app="app2", type="api"),
        ],
    )

    class _FakeAgent:
        async def run(self, state):
            return AIMessage(content=task_decomposition_plan.model_dump_json())

    state = AgentState(input="do something", url="", elements="")

    with tracer.start_as_current_span("test-node-span"):
        result_state = await TaskDecompositionNode.node_handler(state, _FakeAgent(), "TaskDecompositionAgent")

    assert all(k.type == "api" for k in result_state.task_decomposition.task_decomposition)
    assert _attrs(exporter)["cuga.task_decomposition.appworld_type_rewrites"] == 1


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
