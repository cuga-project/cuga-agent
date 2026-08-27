"""Phase 9 sandbox span tests: E2B sandbox creation instrumentation.

See docs/traceloop-instrumentation-plan.md Phase 9: sandbox creation at the
two Langfuse-instrumented sites (E2BSandboxCache._create_sandbox and
execute_code_in_e2b's ephemeral/per-call branch) must also be visible as
OTel spans, additively alongside the existing Langfuse spans. Uses a bare
OTel SDK TracerProvider + InMemorySpanExporter (Phase 6's pattern) since
this task's code opens its own span via tracer.start_as_current_span
rather than relying on a decorator that reads Traceloop-SDK-specific state.
"""

from types import SimpleNamespace

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from cuga.backend.tools_env.code_sandbox import e2b_sandbox

pytestmark = pytest.mark.unit


def _start_recording_span(monkeypatch):
    import opentelemetry.trace as otel_trace_module

    monkeypatch.setattr(otel_trace_module, "_TRACER_PROVIDER", None)
    monkeypatch.setattr(otel_trace_module._TRACER_PROVIDER_SET_ONCE, "_done", False)
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    otel_trace_module.set_tracer_provider(provider)
    tracer = provider.get_tracer("test")
    return tracer, exporter


def _spans_named(exporter, name):
    return [s for s in exporter.get_finished_spans() if s.name == name]


class FakeExecution:
    def __init__(self):
        self.error = None
        self.logs = SimpleNamespace(stdout=["ok"], stderr=[])


class FakeSandbox:
    def __init__(self, sandbox_id="fake-sandbox-id"):
        self.sandbox_id = sandbox_id

    def run_code(self, code, **kwargs):
        return FakeExecution()

    def kill(self):
        pass


class FakeSandboxCreate:
    @staticmethod
    def create(timeout=None):
        return FakeSandbox()


@pytest.fixture
def fresh_cache(monkeypatch):
    """A cache instance with isolated `_sandboxes` state (the class attribute
    is a shared mutable default, so tests must not leak entries into it)."""
    cache = e2b_sandbox.E2BSandboxCache()
    monkeypatch.setattr(cache, "_sandboxes", {})
    return cache


class TestCreateSandboxSpan:
    def test_span_exists_with_attributes(self, monkeypatch, fresh_cache):
        tracer, exporter = _start_recording_span(monkeypatch)
        monkeypatch.setattr(e2b_sandbox, "Sandbox", FakeSandboxCreate)

        fresh_cache._create_sandbox("thread-1")

        spans = _spans_named(exporter, "create-e2b-sandbox")
        assert len(spans) == 1
        attrs = dict(spans[0].attributes)
        assert attrs["cuga.sandbox.mode"] == fresh_cache._mode
        assert attrs["cuga.sandbox.idle_ttl_s"] == fresh_cache._idle_ttl
        assert attrs["cuga.sandbox.ttl_buffer_s"] == fresh_cache._ttl_buffer
        assert attrs["cuga.sandbox.timeout_s"] == fresh_cache._idle_ttl + fresh_cache._ttl_buffer
        assert attrs["cuga.sandbox.id"] == "fake-sandbox-id"

    def test_langfuse_span_also_fires_additively(self, monkeypatch, fresh_cache):
        """This task is additive: the pre-existing Langfuse span must still
        fire alongside the new OTel span, unmodified and without needing to
        mock or disable Langfuse (it runs no-op-safe without credentials)."""
        _start_recording_span(monkeypatch)
        monkeypatch.setattr(e2b_sandbox, "Sandbox", FakeSandboxCreate)

        captured = {}
        real_start = e2b_sandbox.langfuse.start_as_current_observation

        def _wrapped(*args, **kwargs):
            captured["called"] = True
            return real_start(*args, **kwargs)

        monkeypatch.setattr(e2b_sandbox.langfuse, "start_as_current_observation", _wrapped)

        sandbox = fresh_cache._create_sandbox("thread-2")

        assert captured.get("called") is True
        assert sandbox.sandbox_id == "fake-sandbox-id"

    def test_error_path_marks_span_as_error(self, monkeypatch, fresh_cache):
        tracer, exporter = _start_recording_span(monkeypatch)

        class FailingSandboxCreate:
            @staticmethod
            def create(timeout=None):
                raise RuntimeError("boom")

        monkeypatch.setattr(e2b_sandbox, "Sandbox", FailingSandboxCreate)
        monkeypatch.setattr(e2b_sandbox.time, "sleep", lambda *_a, **_kw: None)

        with pytest.raises(RuntimeError):
            fresh_cache._create_sandbox("thread-3")

        spans = _spans_named(exporter, "create-e2b-sandbox")
        assert len(spans) == 1
        assert spans[0].status.status_code == StatusCode.ERROR

    def test_parent_child_linkage_across_sync_call_stack(self, monkeypatch, fresh_cache):
        tracer, exporter = _start_recording_span(monkeypatch)
        monkeypatch.setattr(e2b_sandbox, "Sandbox", FakeSandboxCreate)

        with tracer.start_as_current_span("test-node-span") as parent:
            parent_span_id = parent.get_span_context().span_id
            fresh_cache._create_sandbox("thread-4")

        spans = _spans_named(exporter, "create-e2b-sandbox")
        assert len(spans) == 1
        assert spans[0].parent.span_id == parent_span_id


class TestExecuteCodeInE2BSpan:
    @pytest.mark.asyncio
    async def test_span_exists_with_attributes(self, monkeypatch):
        tracer, exporter = _start_recording_span(monkeypatch)
        monkeypatch.setattr(e2b_sandbox, "E2B_AVAILABLE", True)
        monkeypatch.setattr(e2b_sandbox, "Sandbox", FakeSandboxCreate)
        monkeypatch.setattr(e2b_sandbox.settings.advanced_features, "e2b_sandbox_mode", "per-call")

        result = await e2b_sandbox.execute_code_in_e2b(code_content="print(1)", thread_id=None)

        assert result == "ok"
        spans = _spans_named(exporter, "create-e2b-sandbox")
        assert len(spans) == 1
        attrs = dict(spans[0].attributes)
        assert attrs["cuga.sandbox.mode"] == "per-call"
        expected_ttl = (
            e2b_sandbox.settings.advanced_features.e2b_sandbox_idle_ttl
            + e2b_sandbox.settings.advanced_features.e2b_sandbox_ttl_buffer
        )
        assert attrs["cuga.sandbox.timeout_s"] == expected_ttl
        assert attrs["cuga.sandbox.id"] == "fake-sandbox-id"
