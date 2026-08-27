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


def _install_credentialed_langfuse_client(monkeypatch, public_key):
    """Construct a real (non-no-op) Langfuse client bound to the currently
    installed global TracerProvider, so `langfuse.start_as_current_observation`
    opens an actual recording OTel span instead of being a disabled no-op (the
    default in this test environment, which has no Langfuse credentials
    configured). Passing a throwaway in-memory `span_exporter` means the
    client's own LangfuseSpanProcessor never attempts a real OTLP network
    export. Installs the client as `e2b_sandbox.langfuse` and returns it; the
    caller must tear it down via `_teardown_credentialed_langfuse_client`."""
    from langfuse import Langfuse
    import opentelemetry.trace as otel_trace_module

    provider = otel_trace_module.get_tracer_provider()
    client = Langfuse(
        public_key=public_key,
        secret_key=public_key,
        tracer_provider=provider,
        span_exporter=InMemorySpanExporter(),
    )
    monkeypatch.setattr(e2b_sandbox, "langfuse", client)
    return client


def _teardown_credentialed_langfuse_client(client, public_key):
    """Stop the client's background threads and drop it from Langfuse's
    process-wide resource-manager singleton registry (keyed by public_key)
    so it doesn't leak across tests or shadow a later client with the same
    key."""
    from langfuse._client.resource_manager import LangfuseResourceManager

    client.shutdown()
    LangfuseResourceManager._instances.pop(public_key, None)


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
    monkeypatch.setattr(cache, "_create_count", 0)
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

    def test_langfuse_span_stays_current_for_its_own_updates(self, monkeypatch, fresh_cache):
        """Regression test for the `with (A, B):` ordering bug: the Langfuse
        observation must be innermost (entered last) so it, not the new OTel
        span, is OTel-"current" when the pre-existing `langfuse.update_current_span`
        calls inside `_create_sandbox` run. Those calls write to whatever OTel
        span is current, so if the OTel span were innermost instead, Langfuse's
        own metadata/output would silently land on the OTel span - corrupting
        the existing Langfuse-native instrumentation this task must leave
        untouched.

        This is invisible with a no-credentials Langfuse client (the default
        elsewhere in this test file) because `start_as_current_observation` is
        then a no-op. Here we install a real, recording Langfuse client (fake
        credentials + the current global TracerProvider + a throwaway
        span_exporter so nothing is sent over the network) so
        `update_current_span` actually writes observable attributes."""
        tracer, exporter = _start_recording_span(monkeypatch)
        monkeypatch.setattr(e2b_sandbox, "Sandbox", FakeSandboxCreate)

        public_key = "test-e2b-regression-pk"
        client = _install_credentialed_langfuse_client(monkeypatch, public_key)
        try:
            fresh_cache._create_sandbox("thread-regression")
        finally:
            _teardown_credentialed_langfuse_client(client, public_key)

        spans = _spans_named(exporter, "create-e2b-sandbox")
        assert len(spans) == 2

        otel_span = next(s for s in spans if "cuga.sandbox.mode" in s.attributes)
        langfuse_span = next(s for s in spans if s is not otel_span)

        # The OTel span must be outer/root and must NOT carry Langfuse's own
        # metadata/output - if it did, that would mean the bug regressed.
        assert otel_span.parent is None
        assert "langfuse.observation.output" not in otel_span.attributes
        assert not any(k.startswith("langfuse.observation.metadata") for k in otel_span.attributes)

        # Langfuse's own span must be inner/child of the OTel span, and must
        # be the one carrying its own metadata/output written by the
        # existing `update_current_span` calls.
        assert langfuse_span.parent is not None
        assert langfuse_span.parent.span_id == otel_span.context.span_id
        assert "langfuse.observation.output" in langfuse_span.attributes
        assert any(k.startswith("langfuse.observation.metadata") for k in langfuse_span.attributes)

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
