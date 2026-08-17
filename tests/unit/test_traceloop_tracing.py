"""Traceloop observability: init_traceloop() + the cuga.run root span (Phase 1).

See docs/traceloop-instrumentation-plan.md — this file grows in later phases.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit


class _StubGraph:
    """Minimal stand-in for the compiled LangGraph graph in invoke().

    Mirrors tests/unit/test_sdk_citations.py's _StubGraph.
    """

    def __init__(self, result: dict):
        self._result = result

    async def ainvoke(self, *_args, **_kwargs):
        return self._result

    def get_state(self, *_args, **_kwargs):
        # values=None -> invoke() finds no existing state; next=() -> not interrupted.
        return SimpleNamespace(values=None, next=())


@pytest.mark.unit
def test_init_traceloop_is_idempotent(monkeypatch, tmp_path):
    """Calling init_traceloop() twice must only initialize Traceloop once."""
    from cuga.backend.observability import traceloop_init
    from cuga.config import settings as real_settings

    monkeypatch.setattr(traceloop_init, "_initialized", False)
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
    monkeypatch.setattr(real_settings.observability, "traceloop", False)

    with patch("traceloop.sdk.Traceloop.init") as mock_init:
        traceloop_init.init_traceloop()

    mock_init.assert_not_called()
    assert traceloop_init._initialized is False


@pytest.mark.unit
def test_invoke_produces_real_otlp_file_with_cuga_run_span(monkeypatch, tmp_path):
    """End-to-end: with traceloop_exporter='file', a trivial CugaAgent.invoke() call
    must produce a real trace file with at least one valid-JSON OTLP line, and the
    cuga.run span on it must carry cuga.entry_point/gen_ai.task.input/gen_ai.task.output.

    Exercises the real span-export path (LocalOtlpFileSpanExporter writing to a
    tmp_path file) end to end — nothing about the OTel/Traceloop plumbing is mocked.
    """
    import opentelemetry.trace as otel_trace_module

    from cuga.backend.observability import traceloop_init
    from cuga.config import settings as real_settings
    from cuga.sdk import CugaAgent

    # opentelemetry.trace.set_tracer_provider() is a process-wide one-shot
    # (guarded by _TRACER_PROVIDER_SET_ONCE) — reset it so Traceloop.init() below
    # can actually install its own provider regardless of what ran earlier in this
    # test session, and so this test's state doesn't leak into other tests either
    # (monkeypatch restores both attributes on teardown).
    monkeypatch.setattr(otel_trace_module, "_TRACER_PROVIDER", None)
    monkeypatch.setattr(otel_trace_module._TRACER_PROVIDER_SET_ONCE, "_done", False)
    monkeypatch.setattr(traceloop_init, "_initialized", False)

    trace_file = tmp_path / "spans.jsonl"
    monkeypatch.setattr(real_settings.observability, "traceloop", True)
    monkeypatch.setattr(real_settings.observability, "traceloop_exporter", "file")
    monkeypatch.setattr(real_settings.observability, "traceloop_file_path", str(trace_file))

    agent = CugaAgent(auto_load_policies=False)

    async def _noop_initialized():
        return None

    monkeypatch.setattr(agent, "_ensure_initialized", _noop_initialized)
    agent._compiled_graph = _StubGraph({"final_answer": "the answer"})

    result = asyncio.run(agent.invoke("hello world", thread_id="traceloop-test-thread"))

    assert result.answer == "the answer"
    assert trace_file.exists(), "traceloop_exporter='file' must produce a trace file"

    lines = [line for line in trace_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) >= 1

    cuga_run_spans = []
    for line in lines:
        payload = json.loads(line)  # each line must be valid JSON on its own
        assert "resourceSpans" in payload, "line must parse as an ExportTraceServiceRequest"
        for resource_span in payload["resourceSpans"]:
            for scope_span in resource_span.get("scopeSpans", []):
                for span in scope_span.get("spans", []):
                    if span.get("name") == "cuga.run":
                        cuga_run_spans.append(span)

    assert len(cuga_run_spans) == 1, f"expected exactly one cuga.run span, found {len(cuga_run_spans)}"

    attrs = {a["key"]: a["value"] for a in cuga_run_spans[0].get("attributes", [])}
    assert attrs["cuga.entry_point"]["stringValue"] == "sdk"
    assert attrs["gen_ai.task.input"]["stringValue"] == "hello world"
    assert attrs["gen_ai.task.output"]["stringValue"] == "the answer"
