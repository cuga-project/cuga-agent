"""Traceloop observability: init_traceloop() (Phase 1) + ensuring it's called
before every graph invocation, everywhere (Phase 2).

See docs/traceloop-instrumentation-plan.md — this file grows in later phases.
"""

from __future__ import annotations

import asyncio
import base64
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
    either (monkeypatch restores both attributes on teardown).

    Also resets traceloop-sdk's own TracerWrapper singleton (a separate,
    harder one-shot): TracerWrapper.__new__ caches `cls.instance` on the
    class itself, and every later Traceloop.init() call just returns that
    cached instance, silently ignoring its new exporter/config. Without this
    reset, a second real (non-mocked) Traceloop.init() anywhere later in the
    same pytest session binds to the FIRST test's already-closed exporter
    instead of its own, and its own trace file is never written."""
    import opentelemetry.trace as otel_trace_module
    from traceloop.sdk.tracing.tracing import TracerWrapper

    monkeypatch.setattr(otel_trace_module, "_TRACER_PROVIDER", None)
    monkeypatch.setattr(otel_trace_module._TRACER_PROVIDER_SET_ONCE, "_done", False)
    if hasattr(TracerWrapper, "instance"):
        monkeypatch.delattr(TracerWrapper, "instance")


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


def _reset_langchain_instrumentation(monkeypatch):
    """Make a second real Traceloop.init() in the same process actually rebind
    LangChain/LangGraph instrumentation to the new TracerProvider.

    LangchainInstrumentor is a process-wide singleton that captures its tracer
    (and provider) once, at instrument() time, and refuses to re-instrument
    while its flag is set. Its own uninstrument() clears the flag but leaves
    the patched functions in place: OTel's unwrap() helper can't resolve a
    dotted attribute name like "BaseCallbackManager.__init__", so it silently
    does nothing. The next instrument() then stacks a second wrapper on the
    first, and the stale inner handler wins the "is a Traceloop handler already
    registered?" check inside _BaseCallbackManagerInitWrapper — so every span
    keeps going to the previous test's already-closed exporter.
    """
    from langchain_core.callbacks import BaseCallbackManager
    from langgraph.pregel import Pregel
    from opentelemetry.instrumentation.langchain import LangchainInstrumentor

    instrumentor = LangchainInstrumentor()
    if instrumentor.is_instrumented_by_opentelemetry:
        instrumentor.uninstrument()

    for owner, attr in ((BaseCallbackManager, "__init__"), (Pregel, "stream"), (Pregel, "astream")):
        original = getattr(owner, attr)
        while hasattr(original, "__wrapped__"):
            original = original.__wrapped__
        monkeypatch.setattr(owner, attr, original)


def _init_traceloop_to_file(monkeypatch, tmp_path, app_name: str):
    """Real (non-mocked) Traceloop.init() writing to a per-test trace file.

    Returns the trace file path. Callers get the same setup the Phase 2
    nested-graph test does inline: provider/singleton reset first, settings
    pointed at a file exporter, then a real init with all auto-instrumentation
    enabled (instruments=None) so LangChain/LangGraph spans are actually
    produced.
    """
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

    _reset_langchain_instrumentation(monkeypatch)

    from traceloop.sdk import Traceloop

    Traceloop.init(
        app_name=app_name,
        exporter=LocalOtlpFileSpanExporter(str(trace_file)),
        disable_batch=True,
        instruments=None,
        block_instruments=None,
    )
    return trace_file


def _run_single_node_graph(node_fn, graph_name: str):
    """Compile and fully drive a one-node LangGraph graph around ``node_fn``.

    Same shape as the Phase 2 nested-graph test's outer graph, and the same
    astream(stream_mode="updates") call shape CugaAgent.stream() uses — the
    node runs under a real LangGraph-instrumented parent span, which is the
    whole point of the DP14 checks below.
    """
    from typing import TypedDict

    from langgraph.graph import END, StateGraph

    class _State(TypedDict):
        result: str

    builder = StateGraph(_State)
    builder.add_node("call_nested_site", node_fn)
    builder.set_entry_point("call_nested_site")
    builder.add_edge("call_nested_site", END)
    graph = builder.compile()
    graph.name = graph_name

    async def _run():
        async for _ in graph.astream({"result": ""}, stream_mode="updates"):
            pass

    asyncio.run(_run())


def _assert_one_trace_covering_nested_llm_call(spans: list[dict], site: str) -> None:
    """The DP14 assertion: the nested LLM call produced a real span, and every
    span in the run shares one trace_id with the outer graph/node.

    The LLM-span check is load-bearing, not decoration: without a chat span
    there is nothing whose propagation could have failed, so a single trace_id
    would prove nothing about the nested call site.
    """
    span_names = [span.get("name", "") for span in spans]
    assert any(name.endswith(".chat") for name in span_names), (
        f"{site}: expected a real LLM span from the nested ainvoke() "
        f"(without one this test proves nothing), got names: {span_names}"
    )
    assert any(name.endswith("call_nested_site") for name in span_names), (
        f"{site}: expected the outer graph node's own span, got names: {span_names}"
    )

    trace_ids = {span["traceId"] for span in spans}
    assert len(trace_ids) == 1, (
        f"{site}: nested ainvoke() must stay in the outer graph's trace; "
        f"found {len(trace_ids)} trace_ids: {trace_ids} across names {span_names}"
    )


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

    # Get ActivityTracker singleton and register the tool. `tools` is a class
    # attribute shared process-wide, so use monkeypatch.setitem to revert this
    # entry on teardown instead of permanently mutating the singleton.
    tracker = ActivityTracker()
    monkeypatch.setitem(ActivityTracker.tools, "test_server", [tool])

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
    assert tool_output_attr == "7", f"expected tool.output='7', got {tool_output_attr}"

    # gen_ai.operation.name should be "tool"
    assert attrs.get("gen_ai.operation.name", {}).get("stringValue") == "tool", (
        f"expected gen_ai.operation.name='tool', got {attrs.get('gen_ai.operation.name')}"
    )


@pytest.mark.unit
def test_invoke_tool_sync_produces_span_with_dp9_attributes(monkeypatch, tmp_path):
    """Phase 6 test: invoke_tool_sync() produces a span with DP9 attributes
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
        app_name="cuga-test-invoke-tool-sync",
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

    # Get ActivityTracker singleton and register the tool. `tools` is a class
    # attribute shared process-wide, so use monkeypatch.setitem to revert this
    # entry on teardown instead of permanently mutating the singleton.
    tracker = ActivityTracker()
    monkeypatch.setitem(ActivityTracker.tools, "test_server", [tool])

    # Call invoke_tool_sync and verify return value
    result = tracker.invoke_tool_sync("test_server", tool.name, {"a": 3, "b": 4})
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

    # Find the tool span (should be named "invoke_tool_sync.tool" based on decorator config)
    tool_spans = [span for span in spans if span.get("name", "") == "invoke_tool_sync.tool"]
    assert len(tool_spans) > 0, (
        f"expected to find a tool span named 'invoke_tool_sync.tool', got span names: "
        f"{[span.get('name', '') for span in spans]}"
    )

    tool_span = tool_spans[0]
    attrs = {a["key"]: a["value"] for a in tool_span.get("attributes", [])}

    # Assert decorator's own attributes
    assert attrs.get("traceloop.span.kind", {}).get("stringValue") == "tool", (
        f"expected traceloop.span.kind='tool', got {attrs.get('traceloop.span.kind')}"
    )
    assert attrs.get("traceloop.entity.name", {}).get("stringValue") == "invoke_tool_sync", (
        f"expected traceloop.entity.name='invoke_tool_sync', got {attrs.get('traceloop.entity.name')}"
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
    assert tool_output_attr == "7", f"expected tool.output='7', got {tool_output_attr}"

    # gen_ai.operation.name should be "tool"
    assert attrs.get("gen_ai.operation.name", {}).get("stringValue") == "tool", (
        f"expected gen_ai.operation.name='tool', got {attrs.get('gen_ai.operation.name')}"
    )


@pytest.mark.unit
def test_invoke_tool_respects_trace_content_opt_out(monkeypatch, tmp_path):
    """TRACELOOP_TRACE_CONTENT=false must suppress tool.arguments/tool.output
    (the content-bearing attributes CUGA sets manually), mirroring Traceloop's
    own _should_send_prompts() gate on its traceloop.entity.input/output
    attributes. tool.name and gen_ai.operation.name are not content and must
    remain present even with content capture off."""
    from langchain_core.tools import StructuredTool

    from cuga.backend.activity_tracker.tracker import ActivityTracker
    from cuga.backend.observability import traceloop_init
    from cuga.backend.observability.local_otlp_file_exporter import LocalOtlpFileSpanExporter
    from cuga.config import settings as real_settings

    _reset_tracer_provider(monkeypatch)
    monkeypatch.setattr(traceloop_init, "_initialized", False)
    monkeypatch.setattr(traceloop_init, "_init_attempted", False)
    monkeypatch.setenv("TRACELOOP_TRACE_CONTENT", "false")

    trace_file = tmp_path / "spans.jsonl"
    monkeypatch.setattr(real_settings.observability, "traceloop", True)
    monkeypatch.setattr(real_settings.observability, "traceloop_exporter", "file")
    monkeypatch.setattr(real_settings.observability, "traceloop_file_path", str(trace_file))

    from traceloop.sdk import Traceloop

    exporter = LocalOtlpFileSpanExporter(str(trace_file))
    Traceloop.init(
        app_name="cuga-test-invoke-tool-content-opt-out",
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

    # Get ActivityTracker singleton and register the tool. `tools` is a class
    # attribute shared process-wide, so use monkeypatch.setitem to revert this
    # entry on teardown instead of permanently mutating the singleton.
    tracker = ActivityTracker()
    monkeypatch.setitem(ActivityTracker.tools, "test_server", [tool])

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

    tool_spans = [span for span in spans if span.get("name", "") == "invoke_tool.tool"]
    assert len(tool_spans) > 0, (
        f"expected to find a tool span named 'invoke_tool.tool', got span names: "
        f"{[span.get('name', '') for span in spans]}"
    )

    tool_span = tool_spans[0]
    attrs = {a["key"]: a["value"] for a in tool_span.get("attributes", [])}

    # Tool identity attributes must still be present with content capture off.
    assert attrs.get("tool.name", {}).get("stringValue") == tool.name, (
        f"expected tool.name={tool.name} even with content capture off, got {attrs.get('tool.name')}"
    )
    assert attrs.get("gen_ai.operation.name", {}).get("stringValue") == "tool", (
        f"expected gen_ai.operation.name='tool' even with content capture off, "
        f"got {attrs.get('gen_ai.operation.name')}"
    )

    # Content-bearing attributes must be absent when TRACELOOP_TRACE_CONTENT=false.
    assert "tool.arguments" not in attrs, (
        f"expected tool.arguments to be absent with TRACELOOP_TRACE_CONTENT=false, "
        f"got {attrs.get('tool.arguments')}"
    )
    assert "tool.output" not in attrs, (
        f"expected tool.output to be absent with TRACELOOP_TRACE_CONTENT=false, "
        f"got {attrs.get('tool.output')}"
    )


# ---------------------------------------------------------------------------
# Phase 10 / DP14 — the five call sites that needed a hand-built config shim
# under Langfuse's callback-list tracing (docs/issues/langfuse-nested-callback-
# propagation.md). Langfuse loses the trace there because LangGraph threads its
# CallbackHandler through config["callbacks"] and these sites call a nested
# ainvoke() without forwarding that config. OTel propagates through contextvars
# instead, which is an entirely separate mechanism — these tests settle
# empirically, per site, whether it actually holds.
#
# Each test drives the site's REAL function from inside a real one-node
# LangGraph graph, with a real LangChain fake chat model (not a MagicMock) so
# opentelemetry-instrumentation-langchain produces an actual LLM span to check
# propagation on.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_dp14_sandbox_node_reflection_ainvoke_stays_in_outer_trace(monkeypatch, tmp_path):
    """Site 1: sandbox_node's `reflection_agent.ainvoke(...)` (sandbox_node.py,
    inside create_sandbox_node's `sandbox` node, under `if reflection_enabled`)."""
    from unittest.mock import AsyncMock, MagicMock

    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter import sandbox_node as sandbox_node_module
    from cuga.backend.cuga_graph.nodes.cuga_lite.adapter.graph_adapter import AgentGraphAdapter

    trace_file = _init_traceloop_to_file(monkeypatch, tmp_path, "cuga-test-dp14-sandbox-reflection")

    settings = sandbox_node_module.settings
    monkeypatch.setattr(settings.policy, "enabled", False)
    monkeypatch.setattr(
        sandbox_node_module.CodeExecutor,
        "eval_with_tools_async",
        AsyncMock(return_value=("execution output", {})),
    )
    monkeypatch.setattr(
        sandbox_node_module, "core_append_with_step_limit", lambda *_args, **_kwargs: ([], None)
    )

    adapter = AgentGraphAdapter(
        tracker=MagicMock(),
        base_callbacks=[],
        task_todos_ref=[],
        tools_context_ref={},
        base_tool_provider=None,
    )

    state = MagicMock()
    state.variables_manager.get_variable_names.return_value = []
    state.chat_messages = []
    state.tool_calls = []
    state.thread_id = None
    state.script = "print('hi')"
    state.step_count = 0
    state.cuga_lite_max_steps = None
    state.tool_calls_used_run = 0
    state.tool_calls_used_thread = 0
    state.sub_task = "summarize the accounts"
    state.reflection_apps = []
    state.reflection_skills_prompt_section = ""
    state.reflection_enable_find_tools = False
    state.reflection_skills_enabled = False

    sandbox = sandbox_node_module.create_sandbox_node(adapter, base_thread_id="t1", base_apps_list=[])
    reflection_llm = GenericFakeChatModel(messages=iter([AIMessage(content="reflection summary")]))

    async def node(_state):
        await sandbox(
            state,
            config={
                "configurable": {
                    "llm": reflection_llm,
                    "reflection_enabled": True,
                    "thread_id": "t1",
                }
            },
        )
        return {"result": "done"}

    _run_single_node_graph(node, "Dp14SandboxReflectionGraph")

    spans = _all_spans(trace_file)
    _assert_one_trace_covering_nested_llm_call(spans, "sandbox_node reflection ainvoke")


@pytest.mark.unit
def test_dp14_shortlister_chain_ainvoke_stays_in_outer_trace(monkeypatch, tmp_path):
    """Site 2: `chain.ainvoke(...)` inside
    PromptUtils._ainvoke_shortlister_with_name_validation (prompt_utils.py)."""
    from types import SimpleNamespace

    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.runnables import RunnableLambda

    from cuga.backend.cuga_graph.nodes.cuga_lite.prompt_utils import PromptUtils

    trace_file = _init_traceloop_to_file(monkeypatch, tmp_path, "cuga-test-dp14-shortlister")

    llm = GenericFakeChatModel(messages=iter([AIMessage(content="tool_a")]))
    prompt = ChatPromptTemplate.from_messages(
        [("human", "{instructions}\n{input}\napps={all_apps}\ntools={all_tools}")]
    )
    # The shortlister's real chain returns a structured object with `.result`;
    # this tail mirrors that contract without needing a provider that can do
    # structured output.
    chain = prompt | llm | RunnableLambda(
        lambda message: SimpleNamespace(
            result=[SimpleNamespace(name=message.content, reasoning="because")]
        )
    )

    async def node(_state):
        details, invalid = await PromptUtils._ainvoke_shortlister_with_name_validation(
            chain=chain,
            query="list users",
            apps_as_dict={},
            tools_as_dict={},
            base_instructions="pick tools",
            valid_names={"tool_a"},
        )
        assert [d.name for d in details] == ["tool_a"]
        assert invalid == []
        return {"result": "done"}

    _run_single_node_graph(node, "Dp14ShortlisterGraph")

    spans = _all_spans(trace_file)
    _assert_one_trace_covering_nested_llm_call(spans, "shortlister chain ainvoke")


@pytest.mark.unit
def test_dp14_nl_auto_continue_ainvoke_stays_in_outer_trace(monkeypatch, tmp_path):
    """Site 3: `llm.ainvoke(...)` inside classify_nl_auto_continue_decision
    (nl_auto_continue_classifier.py)."""
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    from cuga.backend.cuga_graph.nodes.cuga_lite import nl_auto_continue_classifier as classifier_module

    trace_file = _init_traceloop_to_file(monkeypatch, tmp_path, "cuga-test-dp14-nl-auto-continue")

    # Off by default in settings.toml; the LLM branch is unreachable without it.
    monkeypatch.setattr(
        classifier_module.settings.advanced_features, "cuga_lite_nl_auto_continue", True
    )

    llm = GenericFakeChatModel(messages=iter([AIMessage(content='{"auto_continue": true}')]))

    async def node(_state):
        decision = await classifier_module.classify_nl_auto_continue_decision(
            llm,
            "The account balance table is shown above with all rows.",
            "checked every row before answering",
        )
        assert decision.auto_continue is True
        return {"result": "done"}

    _run_single_node_graph(node, "Dp14NlAutoContinueGraph")

    spans = _all_spans(trace_file)
    _assert_one_trace_covering_nested_llm_call(spans, "nl_auto_continue classifier ainvoke")


@pytest.mark.unit
def test_dp14_output_formatter_ainvoke_stays_in_outer_trace(monkeypatch, tmp_path):
    """Site 4: `llm.ainvoke(...)` inside PolicyEnactment._enact_format_output
    (enactment.py). format_type="markdown" is the branch that calls the LLM."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    from cuga.backend.cuga_graph.policy.enactment import PolicyEnactment
    from cuga.backend.cuga_graph.policy.models import OutputFormatter
    import cuga.backend.llm.models as llm_models_module

    trace_file = _init_traceloop_to_file(monkeypatch, tmp_path, "cuga-test-dp14-output-formatter")

    llm = GenericFakeChatModel(messages=iter([AIMessage(content="- answer is 42")]))
    monkeypatch.setattr(
        llm_models_module,
        "LLMManager",
        lambda *_args, **_kwargs: SimpleNamespace(get_model=lambda *_a, **_k: llm),
    )

    policy_match = MagicMock()
    policy_match.policy = OutputFormatter(
        id="test_fmt",
        name="Test Formatter",
        description="test",
        format_type="markdown",
        format_config="Use bullet points.",
        triggers=[],
    )
    policy_match.reasoning = "test"
    policy_match.confidence = 1.0

    state = MagicMock()
    state.chat_messages = [AIMessage(content="answer is 42")]
    state.final_answer = "answer is 42"

    context = MagicMock()
    context.agent_response = "answer is 42"
    context.chat_messages = []
    context.user_input = "what is the answer"

    async def node(_state):
        _cmd, metadata = await PolicyEnactment._enact_format_output(
            state, policy_match, MagicMock(), context
        )
        assert metadata is not None
        return {"result": "done"}

    _run_single_node_graph(node, "Dp14OutputFormatterGraph")

    spans = _all_spans(trace_file)
    _assert_one_trace_covering_nested_llm_call(spans, "output formatter ainvoke")


@pytest.mark.unit
def test_dp14_context_summarization_ainvoke_stays_in_outer_trace(monkeypatch, tmp_path):
    """Site 5: the summarizer's internal LLM call reached via
    apply_context_summarization (context_management_utils.py).

    The LLM call is gated behind ContextSummarizer's real trigger check, so the
    thresholds are lowered rather than bypassed: keep_last_n_messages=1 leaves
    older messages to summarize and a near-zero trigger_fraction puts any
    non-empty message list over the usage threshold. The trigger logic itself
    is untouched.
    """
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage, HumanMessage

    from cuga.backend.cuga_graph.state import agent_state as agent_state_module
    from cuga.backend.cuga_graph.utils import context_management_utils
    from cuga.backend.cuga_graph.utils import context_summarizer as context_summarizer_module

    trace_file = _init_traceloop_to_file(monkeypatch, tmp_path, "cuga-test-dp14-context-summarization")

    # Patch via each target module's own `settings` reference: another test in
    # the suite does importlib.reload(cuga.config), so a freshly imported
    # `settings` here can be a different object than these modules hold.
    for config in {
        id(cfg): cfg
        for cfg in (
            agent_state_module.settings.context_summarization,
            context_summarizer_module.settings.context_summarization,
        )
    }.values():
        monkeypatch.setattr(config, "enabled", True)
        monkeypatch.setattr(config, "keep_last_n_messages", 1)
        monkeypatch.setattr(config, "trigger_fraction", 1e-9)

    llm = GenericFakeChatModel(messages=iter([AIMessage(content="a summary of earlier turns")] * 8))
    messages = [
        HumanMessage(content="first user turn"),
        AIMessage(content="first assistant turn"),
        HumanMessage(content="second user turn"),
        AIMessage(content="second assistant turn"),
    ]

    async def node(_state):
        summarized = await context_management_utils.apply_context_summarization(
            messages, llm, message_list_name="chat_messages"
        )
        assert summarized
        return {"result": "done"}

    _run_single_node_graph(node, "Dp14ContextSummarizationGraph")

    spans = _all_spans(trace_file)
    _assert_one_trace_covering_nested_llm_call(spans, "context summarization ainvoke")


@pytest.mark.unit
def test_dp14_remote_call_api_code_sends_traceparent(monkeypatch, tmp_path):
    """The remote-sandbox `call_api` HTTP boundary (call_api_helper.py).

    Architecturally unlike sites 1-5: the generated code runs inside an
    E2B/Docker sandbox with no OTel SDK and no shared contextvars, so the only
    way the registry call can join the agent's trace is an explicit
    `traceparent` header baked in at code-generation time (which happens in the
    main process, where the real span context lives).
    """
    import json as json_module
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from opentelemetry import trace as otel_trace

    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.common import call_api_helper

    trace_file = _init_traceloop_to_file(monkeypatch, tmp_path, "cuga-test-dp14-remote-call-api")

    captured_headers: list[dict] = []

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            # urllib title-cases outgoing header names; HTTP headers are
            # case-insensitive, so compare on a lowercased view.
            captured_headers.append({k.lower(): v for k, v in self.headers.items()})
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            body = json_module.dumps({"ok": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        tracer = otel_trace.get_tracer(__name__)

        observed = {}

        async def _drive():
            with tracer.start_as_current_span("remote-sandbox-block"):
                code = call_api_helper.CallApiHelper.create_remote_call_api_code(
                    function_call_url=f"http://127.0.0.1:{port}"
                )
                namespace: dict = {}
                exec(code, namespace)
                observed["trace_id"] = format(
                    otel_trace.get_current_span().get_span_context().trace_id, "032x"
                )
                return await namespace["call_api"]("app", "op", {})

        result = asyncio.run(_drive())
    finally:
        server.shutdown()
        server.server_close()

    assert result == {"ok": True}
    assert captured_headers, "the generated call_api must have reached the local server"

    traceparent = captured_headers[0].get("traceparent")
    assert traceparent, (
        f"generated remote call_api must send a traceparent header, got: "
        f"{sorted(captured_headers[0])}"
    )
    # Format: 00-<32 hex trace_id>-<16 hex span_id>-<flags>
    parts = traceparent.split("-")
    assert len(parts) == 4, f"malformed traceparent: {traceparent!r}"
    assert parts[1] == observed["trace_id"], (
        f"traceparent trace_id {parts[1]} must match the span the code was "
        f"generated under ({observed['trace_id']})"
    )

    spans = _all_spans(trace_file)
    assert observed["trace_id"] in {
        format(int.from_bytes(base64.b64decode(span["traceId"]), "big"), "032x") for span in spans
    }, "the driving span must have been exported under that same trace_id"
