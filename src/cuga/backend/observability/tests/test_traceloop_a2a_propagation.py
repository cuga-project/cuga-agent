"""A2A cross-process trace-context propagation (Phase 4 / DP4b).

Verifies init_traceloop()'s A2A instrumentors (traceloop_init.py:
instrument_fastapi_app() for inbound, HTTPXClientInstrumentor/
AioHttpClientInstrumentor for outbound) produce linked traces across the
/a2a HTTP boundary, using a real Traceloop pipeline and real OTel contrib
instrumentors — not stubs. See docs/traceloop-instrumentation-plan.md
Phase 4 and docs/traceloop-instrumentation-spec.md DP4b.

Lives in its own colocated `tests/` dir, run as its own pytest process by
the `unit-a` CI job (see AGENTS.md "CI discovers tests by directory"), not
under `tests/unit/`: traceloop-sdk's TracerWrapper and
opentelemetry-instrumentation-langchain are both process-wide singletons —
a real `Traceloop.init()` call permanently binds LangChain/LangGraph
tracing to whichever TracerProvider was active at the *first* such call in
the process, regardless of later resets. Confirmed empirically: sharing a
process with test_traceloop_tracing.py's
test_nested_graph_call_produces_one_coherent_trace (also a real
Traceloop.init()) breaks whichever test runs second.

For the same reason, Traceloop is initialized exactly ONCE for this whole
module (see `_traceloop_a2a_setup`), not per-test — httpx/aiohttp/FastAPI
instrumentation is one-shot too (each BaseInstrumentor no-ops on a second
`.instrument()` and keeps its first captured tracer). Confirmed
empirically: every test below passes in isolation; the flakiness was
purely from repeated re-init within one process, not a real bug.
"""

from __future__ import annotations

import asyncio
import base64
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, List, Optional

import pytest

pytest.importorskip("cuga.backend.server.a2a")
httpx = pytest.importorskip("httpx")

pytestmark = pytest.mark.unit

# Imported at module level, not inside the test function, so that FastAPI's
# get_type_hints() can resolve the `request: Request` annotation on the
# nested route handler below. With `from __future__ import annotations`
# (above), annotations are strings resolved lazily against the *enclosing
# function's* __globals__ — which for a nested `def` is this module's
# globals, not a local import inside the outer test function. A locally
# imported `Request` silently fails to resolve, and FastAPI falls back to
# treating `request` as a query parameter — a 422 "Field required" that has
# nothing to do with tracing. Found empirically chasing exactly that 422.
from fastapi import FastAPI, Request  # noqa: E402


@dataclass
class _FakeEvent:
    name: str
    data: Any = None
    final: bool = False


@dataclass
class _ScriptedRunner:
    """Minimal GraphRunner stub — mirrors tests/integration/a2a/conftest.py's
    ScriptedGraphRunner, duplicated here rather than shared so this file has
    no cross-directory conftest dependency."""

    received: List[tuple] = field(default_factory=list)

    async def run(
        self, message: str, context_id: Optional[str] = None, approval: Optional[dict] = None
    ) -> AsyncIterator[_FakeEvent]:
        self.received.append((message, context_id))
        yield _FakeEvent("final_answer", {"text": "the answer is 42"}, final=True)


def _trace_id_hex(span: dict) -> str:
    """The LocalOtlpFileSpanExporter's JSON lines encode traceId as base64
    (the protobuf-JSON mapping for a `bytes` field), not hex — unlike the
    hex string a `traceparent` header uses. Decode for comparison."""
    return base64.b64decode(span["traceId"]).hex()


def _all_spans(trace_file) -> list[dict]:
    lines = [line for line in trace_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    spans = []
    for line in lines:
        payload = json.loads(line)
        for resource_span in payload["resourceSpans"]:
            for scope_span in resource_span.get("scopeSpans", []):
                spans.extend(scope_span.get("spans", []))
    return spans


def _reset_traceloop_tracer_wrapper_singleton():
    """TracerWrapper (traceloop.sdk.tracing.tracing) is a
    `if not hasattr(cls, "instance")` singleton: once created, a second
    Traceloop.init() silently reuses the first call's exporter/processor —
    resetting OTel's own TracerProvider tracking isn't enough on its own.
    Still needed for isolation within this file's own tests."""
    from traceloop.sdk.tracing.tracing import TracerWrapper

    if hasattr(TracerWrapper, "instance"):
        del TracerWrapper.instance


@pytest.fixture(scope="module", autouse=True)
def _traceloop_a2a_setup(tmp_path_factory):
    """One-time, module-scoped real Traceloop init — see module docstring
    for why this must not be repeated per-test. Uses a real MonkeyPatch
    (not the function-scoped `monkeypatch` fixture) so the reset survives
    for the module's lifetime; undone at module teardown."""
    import pytest as _pytest

    mp = _pytest.MonkeyPatch()
    try:
        import opentelemetry.trace as otel_trace_module

        from cuga.backend.observability import traceloop_init
        from cuga.config import settings as real_settings

        _reset_traceloop_tracer_wrapper_singleton()
        mp.setattr(otel_trace_module, "_TRACER_PROVIDER", None)
        mp.setattr(otel_trace_module._TRACER_PROVIDER_SET_ONCE, "_done", False)
        mp.setattr(traceloop_init, "_initialized", False)
        mp.setattr(traceloop_init, "_init_attempted", False)

        throwaway_file = tmp_path_factory.mktemp("traceloop-a2a-setup") / "unused.jsonl"
        mp.setattr(real_settings.observability, "traceloop", True)
        mp.setattr(real_settings.observability, "traceloop_exporter", "file")
        mp.setattr(real_settings.observability, "traceloop_file_path", str(throwaway_file))

        traceloop_init.init_traceloop()
        assert traceloop_init._initialized, "test setup: real Traceloop init must succeed"
        yield
    finally:
        mp.undo()
        _reset_traceloop_tracer_wrapper_singleton()


@asynccontextmanager
async def _capture_spans_to(tmp_path):
    """Attach an extra SimpleSpanProcessor (own file, own exporter) to the
    module's already-live TracerProvider, instead of re-running
    Traceloop.init() (see module docstring). The processor is never
    removed, so later tests' spans land here too — reading the file right
    after this test's own request, before any other test runs, keeps the
    read scoped to this test's spans."""
    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    from cuga.backend.observability.local_otlp_file_exporter import LocalOtlpFileSpanExporter

    trace_file = tmp_path / "spans.jsonl"
    exporter = LocalOtlpFileSpanExporter(str(trace_file))
    processor = SimpleSpanProcessor(exporter)
    provider = otel_trace.get_tracer_provider()
    provider.add_span_processor(processor)
    try:
        yield trace_file
    finally:
        processor.shutdown()


@asynccontextmanager
async def _real_server(app):
    """Serve `app` on a real local TCP port via uvicorn.

    Needed for testing *outbound* httpx instrumentation: HTTPXClientInstrumentor
    patches httpx's real transport classes, which `httpx.ASGITransport` (the
    in-process substitute used elsewhere in this suite) doesn't inherit from
    or invoke — an ASGITransport call reaches the ASGI app directly, in the
    same task, so contextvars flow through even with zero real header
    injection (a false positive here). A real socket avoids that.
    """
    import uvicorn

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            await asyncio.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task


def _build_instrumented_a2a_app(runner):
    """A fresh FastAPI app with the A2A router mounted, instrumented the
    same way server/main.py instruments its real app (instrument_fastapi_app,
    not the global FastAPIInstrumentor().instrument() — see that function's
    docstring for why the global form doesn't reach a real app)."""
    from fastapi import FastAPI

    from cuga.backend.observability.traceloop_init import instrument_fastapi_app
    from cuga.backend.server.a2a import build_router

    app = FastAPI()
    instrument_fastapi_app(app)
    app.include_router(
        build_router(runner=runner, settings={"skill_ids": ["delegate_task"], "agent_name": "cuga-test"})
    )
    return app


# A W3C traceparent header naming a fixed, recognizable trace_id.
_SYNTHETIC_TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
_SYNTHETIC_TRACEPARENT = f"00-{_SYNTHETIC_TRACE_ID}-b7ad6b7169203331-01"


async def test_inbound_traceparent_links_message_send(tmp_path):
    """POST /a2a message/send carrying a synthetic inbound traceparent must
    produce a CUGA server span sharing that trace_id — today, without this
    phase's instrumentation, it's silently dropped and CUGA starts a
    disconnected trace instead (DP4b)."""
    app = _build_instrumented_a2a_app(_ScriptedRunner())

    async with _capture_spans_to(tmp_path) as trace_file:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test.local") as client:
            payload = {
                "jsonrpc": "2.0",
                "id": "1",
                "method": "message/send",
                "params": {
                    "message": {"role": "user", "parts": [{"kind": "text", "text": "hi"}], "messageId": "m1"}
                },
            }
            resp = await client.post("/a2a", json=payload, headers={"traceparent": _SYNTHETIC_TRACEPARENT})
        assert resp.status_code == 200

        spans = _all_spans(trace_file)

    server_spans = [s for s in spans if s.get("name") == "POST /a2a"]
    assert server_spans, (
        f"expected a FastAPI server span for POST /a2a, got: {[s.get('name') for s in spans]}"
    )
    assert all(_trace_id_hex(s) == _SYNTHETIC_TRACE_ID for s in server_spans), (
        f"server span trace_id(s) {[_trace_id_hex(s) for s in server_spans]} "
        f"must match the inbound traceparent's {_SYNTHETIC_TRACE_ID}"
    )


async def test_inbound_traceparent_links_message_stream(tmp_path):
    """Same check as message/send, but for the SSE (message/stream) path —
    checked independently per DP4b, since sse_starlette's generator
    scheduling might not inherit contextvars the same way message/send's
    plain request/response cycle does. Empirically it does, but this is the
    regression test for that, not an assumption."""
    app = _build_instrumented_a2a_app(_ScriptedRunner())

    async with _capture_spans_to(tmp_path) as trace_file:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test.local") as client:
            payload = {
                "jsonrpc": "2.0",
                "id": "2",
                "method": "message/stream",
                "params": {
                    "message": {"role": "user", "parts": [{"kind": "text", "text": "hi"}], "messageId": "m2"}
                },
            }
            async with client.stream(
                "POST", "/a2a", json=payload, headers={"traceparent": _SYNTHETIC_TRACEPARENT}
            ) as resp:
                assert resp.status_code == 200
                async for _line in resp.aiter_lines():
                    pass

        spans = _all_spans(trace_file)

    server_spans = [s for s in spans if s.get("name") == "POST /a2a"]
    assert server_spans, (
        f"expected a FastAPI server span for POST /a2a, got: {[s.get('name') for s in spans]}"
    )
    assert all(_trace_id_hex(s) == _SYNTHETIC_TRACE_ID for s in server_spans), (
        f"server span trace_id(s) {[_trace_id_hex(s) for s in server_spans]} "
        f"must match the inbound traceparent's {_SYNTHETIC_TRACE_ID}"
    )


async def test_outbound_httpx_delegate_injects_traceparent():
    """delegate_task_via_a2a_sdk() must inject the current span's trace_id
    into its outbound httpx request (DP4b) — the outbound half of Option C."""
    from opentelemetry import trace as otel_trace

    from cuga.backend.cuga_graph.nodes.cuga_supervisor.a2a_protocol import delegate_task_via_a2a_sdk

    captured: dict = {}
    capture_app = FastAPI()

    @capture_app.post("/a2a")
    async def _capture(request: Request):
        captured["traceparent"] = request.headers.get("traceparent")
        return {
            "jsonrpc": "2.0",
            "id": "x",
            "result": {
                "id": "t1",
                "contextId": "c1",
                "status": {"state": "completed"},
                "history": [
                    {"role": "agent", "parts": [{"kind": "text", "text": "ok"}], "messageId": "m-final"}
                ],
            },
        }

    tracer = otel_trace.get_tracer(__name__)
    async with _real_server(capture_app) as base_url:

        class _Card:
            url = base_url

        with tracer.start_as_current_span("outer") as span:
            expected_trace_id = format(span.get_span_context().trace_id, "032x")
            result = await delegate_task_via_a2a_sdk(_Card(), "please summarize")

    assert result["status"] == "success"
    assert captured.get("traceparent"), "outbound request must carry a traceparent header"
    assert captured["traceparent"].split("-")[1] == expected_trace_id


async def test_outbound_fetch_agent_card_injects_traceparent():
    """fetch_agent_card() must also inject the current span's trace_id into
    its outbound request — a separate call site from delegate_task_via_a2a_sdk,
    scoped in explicitly by DP4b/the plan rather than assumed to follow from
    the delegate test above."""
    from opentelemetry import trace as otel_trace

    from cuga.backend.cuga_graph.nodes.cuga_supervisor.a2a_protocol import fetch_agent_card

    captured: dict = {}
    app = _build_instrumented_a2a_app(_ScriptedRunner())

    @app.middleware("http")
    async def _capture_traceparent(request, call_next):
        captured["traceparent"] = request.headers.get("traceparent")
        return await call_next(request)

    tracer = otel_trace.get_tracer(__name__)
    async with _real_server(app) as base_url:
        with tracer.start_as_current_span("outer") as span:
            expected_trace_id = format(span.get_span_context().trace_id, "032x")
            card = await fetch_agent_card(base_url)

    assert card is not None
    assert captured.get("traceparent"), "fetch_agent_card must carry a traceparent header outbound"
    assert captured["traceparent"].split("-")[1] == expected_trace_id


async def test_outbound_legacy_a2a_protocol_aiohttp_injects_traceparent():
    """The legacy A2AProtocol class's http transport (plain aiohttp, not the
    a2a-sdk) must also inject a traceparent — this is the third and last
    outbound call site DP4b scopes in. Uses a real local aiohttp server
    since aiohttp has no ASGITransport-equivalent in-process substitution."""
    pytest.importorskip("aiohttp")
    from aiohttp import web
    from opentelemetry import trace as otel_trace

    from cuga.backend.cuga_graph.nodes.cuga_supervisor.a2a_protocol import A2AProtocol

    captured: dict = {}

    async def _handler(request):
        captured["traceparent"] = request.headers.get("traceparent")
        return web.json_response({"result": "ok", "variables": {}, "status": "success"})

    app = web.Application()
    app.router.add_post("/delegate", _handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    try:
        tracer = otel_trace.get_tracer(__name__)
        with tracer.start_as_current_span("outer") as span:
            expected_trace_id = format(span.get_span_context().trace_id, "032x")
            protocol = A2AProtocol(endpoint=f"http://127.0.0.1:{port}", transport="http")
            await protocol.connect()
            try:
                await protocol.delegate_task("peer-agent", "please summarize", context={})
            finally:
                await protocol.disconnect()
    finally:
        await runner.cleanup()

    assert captured.get("traceparent"), "A2AProtocol's aiohttp transport must carry a traceparent header"
    assert captured["traceparent"].split("-")[1] == expected_trace_id


# --- End-to-end CUGA-to-CUGA (the real acceptance bar per DP4b/the plan) ---
#
# The tests above check each half in isolation (synthetic header in,
# uninstrumented capture route out) — neither proves the two halves
# actually compose across a real network hop. These two do: a real client
# span drives a request against a real, instrumented server CUGA app on a
# real socket, and the server's own exported spans are asserted to share
# the client's trace_id.


async def test_end_to_end_cuga_to_cuga_message_send_shares_trace_id(tmp_path):
    """delegate_task_via_a2a_sdk(), driven from an active client span, must
    produce a shared trace_id with the real server CUGA app's own exported
    spans for message/send — the outbound and inbound halves composed."""
    from opentelemetry import trace as otel_trace

    from cuga.backend.cuga_graph.nodes.cuga_supervisor.a2a_protocol import delegate_task_via_a2a_sdk

    server_app = _build_instrumented_a2a_app(_ScriptedRunner())

    tracer = otel_trace.get_tracer(__name__)
    async with _capture_spans_to(tmp_path) as trace_file:
        async with _real_server(server_app) as base_url:

            class _Card:
                url = base_url

            with tracer.start_as_current_span("client-outer") as span:
                expected_trace_id = format(span.get_span_context().trace_id, "032x")
                result = await delegate_task_via_a2a_sdk(_Card(), "please summarize")

        spans = _all_spans(trace_file)

    assert result["status"] == "success"
    server_spans = [s for s in spans if s.get("name") == "POST /a2a"]
    assert server_spans, (
        f"expected a FastAPI server span for POST /a2a, got: {[s.get('name') for s in spans]}"
    )
    assert all(_trace_id_hex(s) == expected_trace_id for s in server_spans), (
        f"server span trace_id(s) {[_trace_id_hex(s) for s in server_spans]} "
        f"must match the client's own trace_id {expected_trace_id} — the CUGA-to-CUGA "
        f"acceptance bar for message/send"
    )


async def test_end_to_end_cuga_to_cuga_message_stream_shares_trace_id(tmp_path):
    """Same composition check as message/send, for message/stream — driven
    as a raw httpx POST (delegate_task_via_a2a_sdk doesn't support
    streaming) from an active client span, against the same real,
    instrumented server CUGA app."""
    from opentelemetry import trace as otel_trace

    server_app = _build_instrumented_a2a_app(_ScriptedRunner())

    tracer = otel_trace.get_tracer(__name__)
    async with _capture_spans_to(tmp_path) as trace_file:
        async with _real_server(server_app) as base_url:
            with tracer.start_as_current_span("client-outer") as span:
                expected_trace_id = format(span.get_span_context().trace_id, "032x")
                payload = {
                    "jsonrpc": "2.0",
                    "id": "3",
                    "method": "message/stream",
                    "params": {
                        "message": {
                            "role": "user",
                            "parts": [{"kind": "text", "text": "stream please"}],
                            "messageId": "m3",
                        }
                    },
                }
                async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
                    async with client.stream("POST", "/a2a", json=payload) as resp:
                        assert resp.status_code == 200
                        async for _line in resp.aiter_lines():
                            pass

        spans = _all_spans(trace_file)

    server_spans = [s for s in spans if s.get("name") == "POST /a2a"]
    assert server_spans, (
        f"expected a FastAPI server span for POST /a2a, got: {[s.get('name') for s in spans]}"
    )
    assert all(_trace_id_hex(s) == expected_trace_id for s in server_spans), (
        f"server span trace_id(s) {[_trace_id_hex(s) for s in server_spans]} "
        f"must match the client's own trace_id {expected_trace_id} — the CUGA-to-CUGA "
        f"acceptance bar for message/stream"
    )
