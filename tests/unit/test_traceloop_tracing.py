"""Traceloop observability: init_traceloop() (Phase 1) + ensuring it's called
before every graph invocation, everywhere (Phase 2).

See docs/traceloop-instrumentation-plan.md — this file grows in later phases.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.unit
def test_init_traceloop_is_idempotent(monkeypatch, tmp_path):
    """Calling init_traceloop() twice must only initialize Traceloop once."""
    from cuga.backend.observability import traceloop_init
    from cuga.config import settings as real_settings

    monkeypatch.setattr(traceloop_init, "_initialized", False)
    monkeypatch.setattr(traceloop_init, "_init_attempted", False)
    monkeypatch.setattr(real_settings.observability, "traceloop", True)
    monkeypatch.setattr(real_settings.observability, "traceloop_exporter", "file")
    monkeypatch.setattr(real_settings.observability, "traceloop_file_path", str(tmp_path / "spans.jsonl"))

    # Fully mock Traceloop.init so this test never touches the real (process-global)
    # OTel TracerProvider or triggers real auto-instrumentation.
    with patch("traceloop.sdk.Traceloop.init") as mock_init:
        traceloop_init.init_traceloop()
        traceloop_init.init_traceloop()

    mock_init.assert_called_once()
    assert traceloop_init._initialized is True


@pytest.mark.unit
def test_init_traceloop_noop_when_disabled(monkeypatch):
    """When [observability] traceloop is False (the default), init_traceloop() must not
    touch Traceloop at all."""
    from cuga.backend.observability import traceloop_init
    from cuga.config import settings as real_settings

    monkeypatch.setattr(traceloop_init, "_initialized", False)
    monkeypatch.setattr(traceloop_init, "_init_attempted", False)
    monkeypatch.setattr(real_settings.observability, "traceloop", False)

    with patch("traceloop.sdk.Traceloop.init") as mock_init:
        traceloop_init.init_traceloop()

    mock_init.assert_not_called()
    assert traceloop_init._initialized is False


@pytest.mark.unit
def test_init_traceloop_handles_bare_relative_filename(monkeypatch, tmp_path):
    """init_traceloop() with a bare relative filename (no directory component) must not
    raise FileNotFoundError when os.makedirs() tries to create directories.

    Regression test for: os.path.dirname("spans.jsonl") returns "", and
    os.makedirs("", exist_ok=True) raises FileNotFoundError. The fix is
    os.makedirs(dirname or ".", exist_ok=True).
    """
    from cuga.backend.observability import traceloop_init
    from cuga.config import settings as real_settings

    # Simulate user setting traceloop_file_path to a bare relative filename
    monkeypatch.setattr(traceloop_init, "_initialized", False)
    monkeypatch.setattr(traceloop_init, "_init_attempted", False)
    monkeypatch.setattr(real_settings.observability, "traceloop", True)
    monkeypatch.setattr(real_settings.observability, "traceloop_exporter", "file")
    monkeypatch.setattr(real_settings.observability, "traceloop_file_path", "spans.jsonl")

    # Mock Traceloop.init so we don't trigger real auto-instrumentation
    with patch("traceloop.sdk.Traceloop.init") as mock_init:
        # This must not raise FileNotFoundError
        traceloop_init.init_traceloop()

    mock_init.assert_called_once()
    assert traceloop_init._initialized is True


@pytest.mark.unit
def test_init_traceloop_survives_general_exporter_construction_failure(monkeypatch, tmp_path):
    """A general OS-level failure while constructing the exporter (e.g. PermissionError,
    NotADirectoryError — not just the already-covered bare-relative-filename edge case)
    must be caught inside init_traceloop() and must not propagate.

    init_traceloop() runs unguarded as the first thing in CugaAgent.invoke()/initialize()/
    stream() and CugaSupervisor.invoke() — an observability failure here must never break
    the main agent path.
    """
    from cuga.backend.observability import traceloop_init
    from cuga.config import settings as real_settings

    monkeypatch.setattr(traceloop_init, "_initialized", False)
    monkeypatch.setattr(traceloop_init, "_init_attempted", False)
    monkeypatch.setattr(real_settings.observability, "traceloop", True)
    monkeypatch.setattr(real_settings.observability, "traceloop_exporter", "file")
    monkeypatch.setattr(real_settings.observability, "traceloop_file_path", str(tmp_path / "spans.jsonl"))

    def _boom(*_args, **_kwargs):
        raise NotADirectoryError("simulated general OS failure constructing exporter directory")

    monkeypatch.setattr(traceloop_init.os, "makedirs", _boom)

    with patch("traceloop.sdk.Traceloop.init") as mock_init:
        # Must not raise out of init_traceloop()
        traceloop_init.init_traceloop()

    mock_init.assert_not_called()
    assert traceloop_init._initialized is False


@pytest.mark.unit
@pytest.mark.parametrize(
    ("exporter_kind", "expected_disable_batch"),
    [("file", True), ("otlp", False)],
)
def test_init_traceloop_disable_batch_matches_exporter_mode(
    monkeypatch, tmp_path, exporter_kind, expected_disable_batch
):
    """disable_batch must be True for 'file' mode (SimpleSpanProcessor — synchronous
    flush, cheap local write, matches Phase 1's "see the trace file immediately" bar)
    and False for 'otlp' mode (BatchSpanProcessor — a SimpleSpanProcessor would turn
    every span into a blocking HTTP POST on the agent's own event loop, with up to 6x
    retry/backoff on a slow/down collector)."""
    from cuga.backend.observability import traceloop_init
    from cuga.config import settings as real_settings

    monkeypatch.setattr(traceloop_init, "_initialized", False)
    monkeypatch.setattr(traceloop_init, "_init_attempted", False)
    monkeypatch.setattr(real_settings.observability, "traceloop", True)
    monkeypatch.setattr(real_settings.observability, "traceloop_exporter", exporter_kind)
    monkeypatch.setattr(real_settings.observability, "traceloop_file_path", str(tmp_path / "spans.jsonl"))

    with patch("traceloop.sdk.Traceloop.init") as mock_init:
        traceloop_init.init_traceloop()

    mock_init.assert_called_once()
    assert mock_init.call_args.kwargs["disable_batch"] is expected_disable_batch


@pytest.mark.unit
def test_init_traceloop_does_not_retry_after_failed_attempt(monkeypatch, tmp_path):
    """After one failed init_traceloop() attempt (exporter construction raises), a second
    call must return immediately without re-attempting settings read / directory creation /
    exporter construction — negative-caching for the failure path (Finding 3).

    Contrast with test_init_traceloop_is_idempotent above, which covers the *success*
    path staying idempotent — that behavior must remain unchanged; this test covers the
    new negative-caching added for the failure path only.
    """
    from cuga.backend.observability import traceloop_init
    from cuga.config import settings as real_settings

    monkeypatch.setattr(traceloop_init, "_initialized", False)
    monkeypatch.setattr(traceloop_init, "_init_attempted", False)
    monkeypatch.setattr(real_settings.observability, "traceloop", True)
    monkeypatch.setattr(real_settings.observability, "traceloop_exporter", "file")
    monkeypatch.setattr(real_settings.observability, "traceloop_file_path", str(tmp_path / "spans.jsonl"))

    call_count = {"n": 0}

    def _counting_boom(*_args, **_kwargs):
        call_count["n"] += 1
        raise PermissionError("simulated permanent OS failure")

    monkeypatch.setattr(traceloop_init.os, "makedirs", _counting_boom)

    with patch("traceloop.sdk.Traceloop.init") as mock_init:
        traceloop_init.init_traceloop()  # first attempt: fails, caught, cached as attempted
        traceloop_init.init_traceloop()  # second call: must NOT retry

    assert call_count["n"] == 1, "second call must not re-attempt exporter construction"
    mock_init.assert_not_called()
    assert traceloop_init._initialized is False
    assert traceloop_init._init_attempted is True


def _reset_tracer_provider(monkeypatch):
    """opentelemetry.trace.set_tracer_provider() is a process-wide one-shot
    (guarded by _TRACER_PROVIDER_SET_ONCE) — reset it so Traceloop.init() can
    actually install its own provider regardless of what ran earlier in this
    test session, and so this test's state doesn't leak into other tests
    either (monkeypatch restores both attributes on teardown)."""
    import opentelemetry.trace as otel_trace_module

    monkeypatch.setattr(otel_trace_module, "_TRACER_PROVIDER", None)
    monkeypatch.setattr(otel_trace_module._TRACER_PROVIDER_SET_ONCE, "_done", False)


def _all_spans(trace_file) -> list[dict]:
    lines = [line for line in trace_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    spans = []
    for line in lines:
        payload = json.loads(line)  # each line must be valid JSON on its own
        assert "resourceSpans" in payload, "line must parse as an ExportTraceServiceRequest"
        for resource_span in payload["resourceSpans"]:
            for scope_span in resource_span.get("scopeSpans", []):
                spans.extend(scope_span.get("spans", []))
    return spans


@pytest.mark.unit
def test_nested_graph_call_produces_one_coherent_trace(monkeypatch, tmp_path):
    """Phase 2 regression test (an early, narrower version of Phase 10's DP14
    check): a real, non-stubbed LangGraph graph whose one node calls a second,
    nested graph — mirroring delegation.py's supervisor -> sub-agent pattern —
    must produce ONE trace_id across the outer graph, its nodes, the nested
    sub-graph call, and the sub-graph's own nodes, entirely from LangGraph's
    own auto-instrumentation (opentelemetry-instrumentation-langchain, active
    since instruments=None). No manual span is involved anywhere in CUGA's own
    code for this to hold — see docs/traceloop-instrumentation-plan.md Phase 2.

    Drives astream(stream_mode="updates", subgraphs=True), the exact call
    shape CugaAgent.stream() uses, fully consumed via `async for`.
    """
    from typing import TypedDict

    from langgraph.graph import END, StateGraph

    from cuga.backend.observability import traceloop_init
    from cuga.backend.observability.local_otlp_file_exporter import LocalOtlpFileSpanExporter
    from cuga.config import settings as real_settings

    _reset_tracer_provider(monkeypatch)
    monkeypatch.setattr(traceloop_init, "_initialized", False)
    monkeypatch.setattr(traceloop_init, "_init_attempted", False)

    trace_file = tmp_path / "spans.jsonl"
    monkeypatch.setattr(real_settings.observability, "traceloop", True)
    monkeypatch.setattr(real_settings.observability, "traceloop_exporter", "file")
    monkeypatch.setattr(real_settings.observability, "traceloop_file_path", str(trace_file))

    from traceloop.sdk import Traceloop

    exporter = LocalOtlpFileSpanExporter(str(trace_file))
    Traceloop.init(
        app_name="cuga-test",
        exporter=exporter,
        disable_batch=True,
        instruments=None,
        block_instruments=None,
    )

    class SubState(TypedDict):
        task: str
        result: str

    async def sub_node(state: SubState) -> SubState:
        return {"result": f"sub-agent handled: {state['task']}"}

    sub_builder = StateGraph(SubState)
    sub_builder.add_node("do_work", sub_node)
    sub_builder.set_entry_point("do_work")
    sub_builder.add_edge("do_work", END)
    sub_graph = sub_builder.compile()
    sub_graph.name = "SubAgentGraph"

    class OuterState(TypedDict):
        goal: str
        delegated_answer: str

    async def delegate_node(state: OuterState) -> OuterState:
        # Mirrors delegation.py calling agent_or_config.invoke(task, ...), which
        # internally calls the sub-agent's OWN compiled graph.ainvoke().
        sub_result = await sub_graph.ainvoke({"task": state["goal"], "result": ""})
        return {"delegated_answer": sub_result["result"]}

    outer_builder = StateGraph(OuterState)
    outer_builder.add_node("delegate_to_subagent", delegate_node)
    outer_builder.set_entry_point("delegate_to_subagent")
    outer_builder.add_edge("delegate_to_subagent", END)
    outer_graph = outer_builder.compile()
    outer_graph.name = "SupervisorGraph"

    async def _run():
        collected = []
        async for update in outer_graph.astream(
            {"goal": "test goal", "delegated_answer": ""},
            stream_mode="updates",
            subgraphs=True,
        ):
            collected.append(update)
        return collected

    asyncio.run(_run())

    spans = _all_spans(trace_file)
    assert len(spans) > 0, "expected real spans from LangGraph auto-instrumentation"

    trace_ids = {span["traceId"] for span in spans}
    assert len(trace_ids) == 1, (
        f"expected one coherent trace_id across the outer graph and the nested "
        f"sub-graph call, found {len(trace_ids)}: {trace_ids}"
    )

    span_names = [span.get("name", "") for span in spans]
    assert any(name.startswith("invoke_agent") and "SupervisorGraph" in name for name in span_names), (
        f"expected an invoke_agent span for the outer graph, got names: {span_names}"
    )
    assert any(name.startswith("invoke_agent") and "SubAgentGraph" in name for name in span_names), (
        f"expected an invoke_agent span for the nested sub-graph call, got names: {span_names}"
    )

    delegate_spans = [span for span in spans if span.get("name", "").endswith("delegate_to_subagent")]
    assert delegate_spans, f"expected a span for the delegating node, got names: {span_names}"
    attrs = {a["key"]: a["value"] for a in delegate_spans[0].get("attributes", [])}
    assert "test goal" in attrs["gen_ai.task.input"]["stringValue"]
    assert "sub-agent handled: test goal" in attrs["gen_ai.task.output"]["stringValue"]


@pytest.mark.unit
def test_traceloop_init_module_self_initializes_on_import(monkeypatch, tmp_path):
    """traceloop_init.py must self-initialize at module import time, mirroring
    openlit_init.py's existing pattern — the web UI / A2A-simple / evaluate-CLI
    path never imports cuga.sdk (only cuga.backend.observability.traceloop_init
    directly, see main.py/evaluate_cuga.py), so nothing else would call
    init_traceloop() on that path otherwise (Phase 2)."""
    import importlib

    from cuga.backend.observability import traceloop_init
    from cuga.config import settings as real_settings

    _reset_tracer_provider(monkeypatch)
    monkeypatch.setattr(traceloop_init, "_initialized", False)
    monkeypatch.setattr(traceloop_init, "_init_attempted", False)
    monkeypatch.setattr(real_settings.observability, "traceloop", True)
    monkeypatch.setattr(real_settings.observability, "traceloop_exporter", "file")
    monkeypatch.setattr(real_settings.observability, "traceloop_file_path", str(tmp_path / "spans.jsonl"))

    with patch("traceloop.sdk.Traceloop.init") as mock_init:
        importlib.reload(traceloop_init)

    mock_init.assert_called_once()
    assert traceloop_init._initialized is True


@pytest.mark.unit
def test_invoke_tool_produces_span_with_dp9_attributes(monkeypatch, tmp_path):
    """Phase 6 test: invoke_tool() produces a span with DP9 attributes
    (tool.name, tool.arguments, tool.output, gen_ai.operation.name) set
    correctly via the @traceloop_tool_span decorator and explicit span code."""
    from langchain_core.tools import StructuredTool

    from cuga.backend.activity_tracker.tracker import ActivityTracker
    from cuga.backend.observability import traceloop_init
    from cuga.backend.observability.local_otlp_file_exporter import LocalOtlpFileSpanExporter
    from cuga.config import settings as real_settings

    _reset_tracer_provider(monkeypatch)
    monkeypatch.setattr(traceloop_init, "_initialized", False)
    monkeypatch.setattr(traceloop_init, "_init_attempted", False)

    trace_file = tmp_path / "spans.jsonl"
    monkeypatch.setattr(real_settings.observability, "traceloop", True)
    monkeypatch.setattr(real_settings.observability, "traceloop_exporter", "file")
    monkeypatch.setattr(real_settings.observability, "traceloop_file_path", str(trace_file))

    from traceloop.sdk import Traceloop

    exporter = LocalOtlpFileSpanExporter(str(trace_file))
    Traceloop.init(
        app_name="cuga-test-invoke-tool",
        exporter=exporter,
        disable_batch=True,
        instruments=None,
        block_instruments=None,
    )

    # Create a simple tool: add two integers
    def add_numbers(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    tool = StructuredTool.from_function(add_numbers)

    # Get ActivityTracker singleton and register the tool
    tracker = ActivityTracker()
    tracker.tools["test_server"] = [tool]

    # Call invoke_tool and verify return value
    result = asyncio.run(tracker.invoke_tool("test_server", tool.name, {"a": 3, "b": 4}))
    assert result == 7, f"expected result 7, got {result}"

    # Force flush of spans to file
    from opentelemetry import trace as otel_trace_module
    try:
        provider = otel_trace_module.get_tracer_provider()
        if hasattr(provider, 'force_flush'):
            provider.force_flush()
    except Exception:
        pass

    # Parse exported spans
    spans = _all_spans(trace_file)

    # Find the tool span (should be named "invoke_tool.tool" based on decorator config)
    tool_spans = [span for span in spans if span.get("name", "") == "invoke_tool.tool"]
    assert len(tool_spans) > 0, (
        f"expected to find a tool span named 'invoke_tool.tool', got span names: "
        f"{[span.get('name', '') for span in spans]}"
    )

    tool_span = tool_spans[0]
    attrs = {a["key"]: a["value"] for a in tool_span.get("attributes", [])}

    # Assert decorator's own attributes
    assert attrs.get("traceloop.span.kind", {}).get("stringValue") == "tool", (
        f"expected traceloop.span.kind='tool', got {attrs.get('traceloop.span.kind')}"
    )
    assert attrs.get("traceloop.entity.name", {}).get("stringValue") == "invoke_tool", (
        f"expected traceloop.entity.name='invoke_tool', got {attrs.get('traceloop.entity.name')}"
    )

    # Assert explicit DP9 attributes
    assert attrs.get("tool.name", {}).get("stringValue") == tool.name, (
        f"expected tool.name={tool.name}, got {attrs.get('tool.name')}"
    )

    # tool.arguments should be a JSON string that round-trips
    tool_arguments_attr = attrs.get("tool.arguments", {}).get("stringValue")
    assert tool_arguments_attr is not None, "expected tool.arguments attribute"
    tool_arguments_parsed = json.loads(tool_arguments_attr)
    assert tool_arguments_parsed == {"a": 3, "b": 4}, (
        f"expected tool.arguments to parse to {{'a': 3, 'b': 4}}, got {tool_arguments_parsed}"
    )

    # tool.output should be "7" (JSON-encoded int 7)
    tool_output_attr = attrs.get("tool.output", {}).get("stringValue")
    assert tool_output_attr == "7", (
        f"expected tool.output='7', got {tool_output_attr}"
    )

    # gen_ai.operation.name should be "tool"
    assert attrs.get("gen_ai.operation.name", {}).get("stringValue") == "tool", (
        f"expected gen_ai.operation.name='tool', got {attrs.get('gen_ai.operation.name')}"
    )
