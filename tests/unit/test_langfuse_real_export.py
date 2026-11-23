"""Exercise the real Langfuse exporter with a synthetic loopback OTLP receiver.

This checks client transport and payloads, not Langfuse backend ingestion or API readback.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = pytest.mark.unit


def test_sdk_traces_export_after_explicit_flush(tmp_path):
    # Isolate the Langfuse client singleton and never inherit actual tracing/model credentials.
    result = subprocess.run(
        [sys.executable, __file__],
        cwd=tmp_path,
        env={
            "HOME": str(tmp_path),
            "PATH": os.defpath,
            "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
            "LANG": "C.UTF-8",
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _exercise_loopback():
    import asyncio
    import gzip
    import logging
    from collections import Counter
    from contextlib import aclosing
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from types import MethodType, SimpleNamespace

    from langchain_core.runnables.config import var_child_runnable_config
    from langchain_core.tools import tool
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
    from opentelemetry.trace import get_current_span

    packets = []

    class Collector(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            if self.path != "/api/public/otel/v1/traces":
                self.send_error(404)
                return
            data = self.rfile.read(int(self.headers["Content-Length"]))
            if self.headers.get("Content-Encoding") == "gzip":
                data = gzip.decompress(data)
            body = ExportTraceServiceRequest.FromString(data)
            packets.append([span for r in body.resource_spans for s in r.scope_spans for span in s.spans])
            self.send_response(200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.end_headers()

    warnings = []

    class WarningRecorder(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.WARNING:
                warnings.append(record.getMessage())

    with ThreadingHTTPServer(("127.0.0.1", 0), Collector) as server:
        Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{server.server_port}"
        os.environ.update(
            LANGFUSE_PUBLIC_KEY="pk-lf-cuga-synthetic-loopback",
            LANGFUSE_SECRET_KEY="sk-lf-cuga-synthetic-loopback",
            LANGFUSE_BASE_URL=url,
            LANGFUSE_MEDIA_UPLOAD_ENABLED="false",
            LANGFUSE_FLUSH_INTERVAL="60",
            LANGFUSE_TIMEOUT="2",
        )
        from langfuse import get_client
        from langfuse.langchain import CallbackHandler

        from cuga.backend.cuga_graph.nodes.cuga_lite.providers.toolguard import ToolGuardingToolProvider
        from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import (
            ToolCallTracker,
            make_recording_awaitable,
        )

        from cuga.backend.observability import langfuse_sdk as lf
        from cuga.sdk import CugaAgent, InvokeResult

        client = get_client()
        logging.getLogger("langfuse").addHandler(WarningRecorder())
        lf.enabled = lambda: True  # Isolate the setting, not the client, callbacks, spans, or exporter.
        agent = CugaAgent.__new__(CugaAgent)
        agent._callbacks = []

        async def fake_invoke(self, message, *, thread_id, config, **kwargs):
            if message == "synthetic cancel":
                await asyncio.Event().wait()
            lf.record_tool_call("synthetic.invoke.tool", {"test": "fake"}, "ok")
            return InvokeResult(answer="synthetic reply", thread_id=thread_id)

        async def fake_stream(self, message, *, thread_id, config, **kwargs):
            lf.record_tool_call("synthetic.stream.tool", {"test": "fake"}, "ok")
            yield {"final_answer": "synthetic first output"}
            raise AssertionError("unexpected stream prefetch")

        agent._invoke_untraced = MethodType(fake_invoke, agent)
        agent._stream_untraced = MethodType(fake_stream, agent)

        @tool
        async def synthetic_guarded_tool(value: int):
            """Return synthetic input."""
            return {"value": value}

        async def allow_tool_call(*args, **kwargs):
            return None

        async def guard_runtime(*args, **kwargs):
            return SimpleNamespace(guard_tool_call=allow_tool_call)

        guard = ToolGuardingToolProvider(None)
        guard._get_or_create_toolguard_runtime = guard_runtime
        guarded_call = make_recording_awaitable(
            guard._wrap_tool(synthetic_guarded_tool, "synthetic").coroutine,
            "synthetic_guarded_tool",
            app_name="synthetic",
            param_names=["value"],
        )

        async def guarded_run(config, value):
            token = var_child_runnable_config.set(config)
            ToolCallTracker.start_tracking()
            try:
                assert await guarded_call(value=value) == {"value": value}
            finally:
                local = ToolCallTracker.stop_tracking()
                var_child_runnable_config.reset(token)
            assert len(local) == 1 and local[0]["succeeded"]
            assert local[0]["result"] == {"value": value}

        async def exercise():
            result = await agent.invoke("synthetic prompt", thread_id="synthetic-invoke")
            assert result.answer == "synthetic reply"
            assert not packets, "invoke exported synchronously"
            async with aclosing(agent.stream("synthetic stream", thread_id="synthetic-stream")) as stream:
                assert await anext(stream) == {"final_answer": "synthetic first output"}
                assert not get_current_span().get_span_context().is_valid
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(agent.invoke("synthetic cancel", thread_id="synthetic-cancel"), 0.05)
            async with lf.run_trace("synthetic.parent", "synthetic parent", "synthetic-parent", {}):
                async with lf.run_trace("synthetic.child", "synthetic child", "synthetic-child", {}) as trace:
                    trace.end("synthetic result")
            sdk_config = {}
            async with lf.run_trace("synthetic.guard.sdk", "synthetic", "synthetic-sdk", sdk_config) as trace:
                assert trace.span is not None
                await guarded_run(sdk_config, 10)

            caller_config = {"callbacks": [CallbackHandler()]}
            with client.start_as_current_observation(name="synthetic.guard.caller", as_type="agent"):
                async with lf.run_trace(
                    "synthetic.guard.no-sdk-root", "synthetic", None, caller_config
                ) as trace:
                    assert trace.span is None
                    await guarded_run(caller_config, 20)

        try:
            asyncio.run(exercise())
            assert not packets, "asyncio.run shutdown awaited an export"
            lf.flush()
            assert len(packets) == 1
            spans = packets[0]
            assert Counter(span.name for span in spans) == {
                "cuga.invoke": 2,
                "synthetic.invoke.tool": 1,
                "cuga.stream": 1,
                "synthetic.stream.tool": 1,
                "synthetic.parent": 1,
                "synthetic.child": 1,
                "synthetic.guard.sdk": 1,
                "synthetic.guard.caller": 1,
                "synthetic_guarded_tool": 2,
            }

            def attrs(span):
                return {a.key: a.value.string_value for a in span.attributes}

            def find(name):
                return next(span for span in spans if span.name == name)

            invoke = next(
                span
                for span in spans
                if attrs(span).get("session.id") == "synthetic-invoke" and span.name == "cuga.invoke"
            )
            cancelled = next(span for span in spans if attrs(span).get("session.id") == "synthetic-cancel")
            for child, parent in [
                (find("synthetic.invoke.tool"), invoke),
                (find("synthetic.stream.tool"), find("cuga.stream")),
                (find("synthetic.child"), find("synthetic.parent")),
            ]:
                assert child.trace_id == parent.trace_id
                assert child.parent_span_id == parent.span_id
            guard_span = [span for span in spans if span.name == "synthetic_guarded_tool"]
            assert {span.parent_span_id for span in guard_span} == {
                find("synthetic.guard.sdk").span_id,
                find("synthetic.guard.caller").span_id,
            }
            assert attrs(find("cuga.stream"))["langfuse.observation.output"] == "synthetic first output"
            assert attrs(cancelled)["langfuse.observation.level"] == "ERROR"
            assert attrs(cancelled)["langfuse.observation.status_message"] == "cancelled"
            assert not warnings
        finally:
            client.shutdown()
            server.shutdown()


if __name__ == "__main__":
    _exercise_loopback()
