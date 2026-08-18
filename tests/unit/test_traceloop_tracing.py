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
    monkeypatch.setattr(traceloop_init, "_init_attempted", False)

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
