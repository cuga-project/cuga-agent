"""Phase 7 (DP7 per-node audit) tests: task_decomposition_planning, browser, and api subsystems.

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


# ---------------------------------------------------------------------------
# api_planner.py - cuga.api_planner.parse_fallback_used
# ---------------------------------------------------------------------------


def _api_planner_conclude_output():
    from cuga.backend.cuga_graph.nodes.api.api_planner_agent.prompts.load_prompt import (
        APIPlannerOutput,
        ActionName,
        ConcludeTaskInput,
        ConcludeTaskStatus,
    )

    return APIPlannerOutput(
        thoughts=[],
        action=ActionName.CONCLUDE_TASK,
        action_input_conclude_task=ConcludeTaskInput(
            status=ConcludeTaskStatus.SUCCESS, final_response="done"
        ),
    )


@pytest.mark.asyncio
async def test_api_planner_parse_fallback_used_on_malformed_json(monkeypatch):
    from cuga.backend.cuga_graph.nodes.api.api_planner import ApiPlanner
    from cuga.backend.cuga_graph.state.agent_state import AgentState
    from cuga.config import settings

    tracer, exporter = _start_recording_span(monkeypatch)
    monkeypatch.setattr(settings.advanced_features, "lite_mode", False)
    monkeypatch.setattr(settings.advanced_features, "api_planner_hitl", False)
    monkeypatch.setattr(settings.features, "code_output_reflection", False)

    output = _api_planner_conclude_output()

    class _FakeAgent:
        async def run(self, state):
            # Code-fenced JSON fails a strict json.loads() and needs the tolerant fallback parser.
            return AIMessage(content="```json\n" + output.model_dump_json() + "\n```")

    state = AgentState(input="do something", url="", elements="", sub_task_app="myapp", sub_task_type="api")

    with tracer.start_as_current_span("test-node-span"):
        command = await ApiPlanner.node_handler(
            state, _FakeAgent(), strategic_agent=None, name="APIPlannerAgent"
        )

    assert command.goto == "PlanControllerAgent"
    assert _attrs(exporter)["cuga.api_planner.parse_fallback_used"] is True


@pytest.mark.asyncio
async def test_api_planner_no_parse_fallback_needed_for_clean_json(monkeypatch):
    from cuga.backend.cuga_graph.nodes.api.api_planner import ApiPlanner
    from cuga.backend.cuga_graph.state.agent_state import AgentState
    from cuga.config import settings

    tracer, exporter = _start_recording_span(monkeypatch)
    monkeypatch.setattr(settings.advanced_features, "lite_mode", False)
    monkeypatch.setattr(settings.advanced_features, "api_planner_hitl", False)
    monkeypatch.setattr(settings.features, "code_output_reflection", False)

    output = _api_planner_conclude_output()

    class _FakeAgent:
        async def run(self, state):
            return AIMessage(content=output.model_dump_json())

    state = AgentState(input="do something", url="", elements="", sub_task_app="myapp", sub_task_type="api")

    with tracer.start_as_current_span("test-node-span"):
        command = await ApiPlanner.node_handler(
            state, _FakeAgent(), strategic_agent=None, name="APIPlannerAgent"
        )

    assert command.goto == "PlanControllerAgent"
    assert _attrs(exporter)["cuga.api_planner.parse_fallback_used"] is False


# ---------------------------------------------------------------------------
# api_shortlister.py - cuga.api_shortlister.suggested_count / resolved_count (post-filtering)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_shortlister_post_filter_counts(monkeypatch):
    from cuga.backend.cuga_graph.nodes.api.api_shortlister import ApiShortlister
    from cuga.backend.cuga_graph.nodes.api.shortlister_agent.prompts.load_prompt import (
        ShortListerOutput,
        APIDetails,
    )
    from cuga.backend.cuga_graph.state.agent_state import AgentState
    from cuga.backend.cuga_graph.state.api_planner_history import HistoricalAction

    tracer, exporter = _start_recording_span(monkeypatch)

    shortlisted = ShortListerOutput(
        thoughts=[],
        result=[
            APIDetails(name="get_user", relevance_score=0.9, reasoning="matches intent"),
            APIDetails(name="nonexistent_api", relevance_score=0.5, reasoning="hallucinated"),
        ],
    )

    class _FakeAgent:
        async def run(self, state):
            return AIMessage(content=shortlisted.model_dump_json())

    state = AgentState(
        input="do something",
        url="",
        elements="",
        sub_task_app="myapp",
        api_shortlister_all_filtered_apis={
            "myapp": {
                "api1": {"app_name": "myapp", "api_name": "get_user", "description": "desc"},
                "api2": {"app_name": "myapp", "api_name": "unused_api", "description": "desc2"},
            }
        },
        api_planner_history=[HistoricalAction(action_taken="ApiShortlistingAgent")],
    )

    with tracer.start_as_current_span("test-node-span"):
        command = await ApiShortlister.node_handler(state, _FakeAgent(), "ShortlisterAgent")

    assert command.goto == "APIPlannerAgent"
    attrs = _attrs(exporter)
    assert attrs["cuga.api_shortlister.suggested_count"] == 2
    assert attrs["cuga.api_shortlister.resolved_count"] == 1


# ---------------------------------------------------------------------------
# api_code_planner.py - cuga.api_code_planner.missing_api_reported
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_code_planner_missing_api_reported_attribute(monkeypatch):
    from cuga.backend.cuga_graph.nodes.api.api_code_planner import ApiCodePlanner
    from cuga.backend.cuga_graph.state.agent_state import AgentState
    from cuga.backend.cuga_graph.state.api_planner_history import HistoricalAction

    tracer, exporter = _start_recording_span(monkeypatch)

    class _FakeAgent:
        async def run(self, state):
            msg = AIMessage(content="")
            msg.tool_calls = [
                {"name": "report_missing_api", "args": {"message": "no API for this"}, "id": "call-1"}
            ]
            return msg

    state = AgentState(
        input="do something",
        url="",
        elements="",
        api_planner_history=[HistoricalAction(action_taken="CoderAgent")],
    )

    with tracer.start_as_current_span("test-node-span"):
        command = await ApiCodePlanner.node_handler(state, _FakeAgent(), "APICodePlannerAgent")

    assert command.goto == "APIPlannerAgent"
    assert _attrs(exporter)["cuga.api_code_planner.missing_api_reported"] is True


# ---------------------------------------------------------------------------
# code_agent.py - code_blocks_found / execution_error / output_parse_fallback_used
# ---------------------------------------------------------------------------


def _make_code_agent(chain):
    from cuga.backend.cuga_graph.nodes.api.code_agent.code_agent import CodeAgent

    agent = object.__new__(CodeAgent)
    agent.name = "CodeAgent"
    agent.chain = chain
    agent.instructions = ""
    agent.summary_task = None
    return agent


@pytest.mark.asyncio
async def test_code_agent_execution_error_and_output_fallback_attributes(monkeypatch):
    from cuga.backend.cuga_graph.nodes.api.code_agent import code_agent as ca_module
    from cuga.backend.cuga_graph.state.agent_state import AgentState
    from cuga.config import settings

    tracer, exporter = _start_recording_span(monkeypatch)
    monkeypatch.setattr(settings.features, "code_output_summary", False)

    class _FakeChain:
        async def ainvoke(self, input):
            return AIMessage(content="```python\nprint('hello')\n```")

    async def _boom(cls_or_code=None, **kwargs):
        raise RuntimeError("sandbox crashed")

    monkeypatch.setattr(ca_module.CodeExecutor, "eval_for_code_agent", classmethod(_boom))

    agent = _make_code_agent(_FakeChain())
    state = AgentState(input="do something", url="", elements="", api_planner_codeagent_plan="the plan")

    with tracer.start_as_current_span("test-node-span"):
        await agent.run(state)

    attrs = _attrs(exporter)
    assert attrs["cuga.code_agent.code_blocks_found"] is True
    assert attrs["cuga.code_agent.execution_error"] is True
    assert attrs["cuga.code_agent.execution_error_type"] == "RuntimeError"
    # Execution raised, so execution_output became str(e) - never valid trailing JSON.
    assert attrs["cuga.code_agent.output_parse_fallback_used"] is True


@pytest.mark.asyncio
async def test_code_agent_no_code_blocks_and_clean_json_output(monkeypatch):
    from cuga.backend.cuga_graph.nodes.api.code_agent import code_agent as ca_module
    from cuga.backend.cuga_graph.state.agent_state import AgentState
    from cuga.config import settings

    tracer, exporter = _start_recording_span(monkeypatch)
    monkeypatch.setattr(settings.features, "code_output_summary", False)

    class _FakeChain:
        async def ainvoke(self, input):
            # No triple-backtick code block at all.
            return AIMessage(content="just plain text, no code fences")

    async def _clean_run(cls_or_code=None, **kwargs):
        return '{"variable_name": "result", "description": "d", "value": 42}', {}

    monkeypatch.setattr(ca_module.CodeExecutor, "eval_for_code_agent", classmethod(_clean_run))

    agent = _make_code_agent(_FakeChain())
    state = AgentState(input="do something", url="", elements="", api_planner_codeagent_plan="the plan")

    with tracer.start_as_current_span("test-node-span"):
        await agent.run(state)

    attrs = _attrs(exporter)
    assert attrs["cuga.code_agent.code_blocks_found"] is False
    assert attrs["cuga.code_agent.execution_error"] is False
    assert attrs["cuga.code_agent.output_parse_fallback_used"] is False


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

    assert command.goto == "PlanControllerAgent"
    assert _attrs(exporter)["cuga.cuga_lite.answer_has_error"] is True


@pytest.mark.asyncio
async def test_cuga_lite_node_no_error_no_fallback_needed(monkeypatch):
    from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_node import CugaLiteNode
    from cuga.backend.cuga_graph.state.agent_state import AgentState
    from cuga.config import settings

    tracer, exporter = _start_recording_span(monkeypatch)
    monkeypatch.setattr(settings.advanced_features, "sub_task_keep_last_n", 100)

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

    assert command.goto == "PlanControllerAgent"
    attrs = _attrs(exporter)
    assert attrs["cuga.cuga_lite.answer_has_error"] is False
    assert attrs["cuga.cuga_lite.fallback_answer_used"] is False


@pytest.mark.asyncio
async def test_cuga_lite_node_empty_answer_uses_fallback(monkeypatch):
    from cuga.backend.cuga_graph.nodes.cuga_lite.cuga_lite_node import CugaLiteNode
    from cuga.backend.cuga_graph.state.agent_state import AgentState
    from cuga.config import settings

    tracer, exporter = _start_recording_span(monkeypatch)
    monkeypatch.setattr(settings.advanced_features, "sub_task_keep_last_n", 100)

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

    assert command.goto == "PlanControllerAgent"
    attrs = _attrs(exporter)
    assert attrs["cuga.cuga_lite.answer_has_error"] is False
    assert attrs["cuga.cuga_lite.fallback_answer_used"] is True


# ---------------------------------------------------------------------------
# nl_auto_continue_classifier.py - cuga.nl_auto_continue.decision_path / auto_continue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nl_auto_continue_fast_path(monkeypatch):
    from cuga.backend.cuga_graph.nodes.cuga_lite.nl_auto_continue_classifier import (
        classify_nl_auto_continue_decision,
    )
    from cuga.config import settings

    tracer, exporter = _start_recording_span(monkeypatch)
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_nl_auto_continue", True)

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
    from cuga.backend.cuga_graph.nodes.cuga_lite.nl_auto_continue_classifier import (
        classify_nl_auto_continue_decision,
    )
    from cuga.config import settings

    tracer, exporter = _start_recording_span(monkeypatch)
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_nl_auto_continue", True)

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
    from cuga.backend.cuga_graph.nodes.cuga_lite.nl_auto_continue_classifier import (
        classify_nl_auto_continue_decision,
    )
    from cuga.config import settings

    tracer, exporter = _start_recording_span(monkeypatch)
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_nl_auto_continue", True)

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
    from cuga.backend.cuga_graph.nodes.cuga_lite.nl_auto_continue_classifier import (
        classify_nl_auto_continue_decision,
    )
    from cuga.config import settings

    tracer, exporter = _start_recording_span(monkeypatch)
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_nl_auto_continue", True)

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
    from cuga.backend.cuga_graph.nodes.cuga_lite.nl_auto_continue_classifier import (
        classify_nl_auto_continue_decision,
        BlockedClaimEvidence,
    )
    from cuga.config import settings

    tracer, exporter = _start_recording_span(monkeypatch)
    monkeypatch.setattr(settings.advanced_features, "cuga_lite_nl_auto_continue", True)

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

    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node import create_sandbox_node
    from cuga.config import settings

    tracer, exporter = _start_recording_span(monkeypatch)
    monkeypatch.setattr(settings.policy, "enabled", False)

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

    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.sandbox_node import create_sandbox_node
    from cuga.config import settings

    tracer, exporter = _start_recording_span(monkeypatch)
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
