"""Real Langfuse OTLP export with public SDK entry points and deterministic local graphs.

No model service or Langfuse storage backend is used. The prepared-tool cases run
CUGA's real graph and providers with a scripted model and deterministic guard.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = pytest.mark.unit


def test_public_sdk_export_matrix(tmp_path):
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
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr


class MissingCompletedError(AssertionError):
    """A verified exported-outcome mismatch, not fixture setup failure."""


@pytest.mark.parametrize("route", ["registry", "mcp"])
@pytest.mark.parametrize("outcome", ["error", "cancelled"])
@pytest.mark.xfail(
    strict=True,
    raises=MissingCompletedError,
    reason="Caller-owned provider failures lack a completed ERROR observation",
)
def test_caller_provider_exports_completed_error(tmp_path, route, outcome):
    result = subprocess.run(
        [sys.executable, __file__, route, outcome],
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
    assert "PROBE matrix_probe_" in result.stdout, result.stdout + result.stderr
    if result.returncode:
        assert "Missing truthful terminal provider outcome" in result.stderr, result.stdout + result.stderr
        raise MissingCompletedError(result.stdout + result.stderr)


@pytest.mark.parametrize("route", ["registry", "mcp"])
@pytest.mark.parametrize("outcome", ["error", "cancelled"])
def test_opted_in_handler_has_one_terminal_error_tool(tmp_path, route, outcome):
    result = subprocess.run(
        [sys.executable, __file__, route, outcome, "managed"],
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
    assert "MANAGED verified" in result.stdout


def _exercise():
    import asyncio
    import gzip
    from contextlib import aclosing
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from types import MethodType, SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
    from opentelemetry.trace import get_current_span

    packets = []

    class Collector(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            assert self.path == "/api/public/otel/v1/traces"
            data = self.rfile.read(int(self.headers["Content-Length"]))
            if self.headers.get("Content-Encoding") == "gzip":
                data = gzip.decompress(data)
            body = ExportTraceServiceRequest.FromString(data)
            packets.extend(span for r in body.resource_spans for s in r.scope_spans for span in s.spans)
            self.send_response(200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.end_headers()

    with ThreadingHTTPServer(("127.0.0.1", 0), Collector) as server:
        Thread(target=server.serve_forever, daemon=True).start()
        os.environ.update(
            LANGFUSE_PUBLIC_KEY="pk-lf-matrix-synthetic",
            LANGFUSE_SECRET_KEY="sk-lf-matrix-synthetic",
            LANGFUSE_BASE_URL=f"http://127.0.0.1:{server.server_port}",
            LANGFUSE_MEDIA_UPLOAD_ENABLED="false",
            LANGFUSE_FLUSH_INTERVAL="60",
            LANGFUSE_TIMEOUT="2",
            OPENAI_API_KEY="test",
            OPENAI_BASE_URL=f"http://127.0.0.1:{server.server_port}/v1",
            DYNACONF_ADVANCED_FEATURES__LANGFUSE_TRACING="true",
        )
        # A failed fixture must never fall through to a real model/telemetry endpoint.
        import socket

        original_connect = socket.socket.connect

        def loopback_connect(sock, address):
            if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "::1", "localhost"}:
                raise AssertionError(f"Non-loopback network blocked: {address[0]}")
            return original_connect(sock, address)

        socket.socket.connect = loopback_connect
        from langfuse import get_client
        from langfuse.langchain import CallbackHandler
        from langchain_core.messages import AIMessage
        from langchain_core.tools import StructuredTool

        from cuga.backend.cuga_graph.nodes.cuga_lite.bind_tools.tool_names import provider_safe_tool_name
        from cuga.backend.cuga_graph.nodes.cuga_lite.providers.toolguard import ToolGuardingToolProvider
        from cuga.backend.cuga_graph.utils.langfuse_tracing import sync_langfuse_callbacks_from_config
        from cuga.backend.observability import langfuse_sdk as lf
        from cuga.config import settings
        from cuga.sdk import CugaAgent, CugaSupervisor, InvokeResult
        from test_langfuse_prepare_matrix import ScriptedModel

        client = get_client()
        assert lf.enabled()
        agent = CugaAgent.__new__(CugaAgent)
        agent._callbacks = []

        async def invoke_inner(self, message, *, thread_id, config, **kwargs):
            sync_langfuse_callbacks_from_config(config)
            if message == "cancel":
                raise asyncio.CancelledError()
            if message == "raise":
                raise ValueError("synthetic exception")
            if message == "returned-error":
                return InvokeResult(answer="partial", thread_id=thread_id, error="x" * 25000)
            lf.record_tool_call("matrix.inner.tool", {}, "ok")
            return InvokeResult(answer="done", thread_id=thread_id)

        async def stream_inner(self, message, *, thread_id, config, **kwargs):
            sync_langfuse_callbacks_from_config(config)
            yield {"final_answer": "partial"}
            if message == "cancel":
                raise asyncio.CancelledError()
            yield {"final_answer": "done"}

        agent._invoke_untraced = MethodType(invoke_inner, agent)
        agent._stream_untraced = MethodType(stream_inner, agent)
        supervisor = CugaSupervisor.__new__(CugaSupervisor)
        supervisor._callbacks = []

        async def supervisor_inner(self, message, *, thread_id, _callbacks, **kwargs):
            # Preserve production callback inheritance; child must not create another root.
            return await agent.invoke(message, thread_id=thread_id, config={"callbacks": _callbacks})

        supervisor._invoke_untraced = MethodType(supervisor_inner, supervisor)
        expected_tools = {}
        expected_trace_ids = {}
        caller_trace_id = "a" * 32

        async def prepared(mode, owner, outcome, delivery="per_call", roots="active", shared_handler=None):
            name = f"matrix_{mode}_{owner}_{outcome}_{delivery}_{roots}_lookup_with_a_long_provider_aliased_tool_name"
            executions = []

            async def lookup(value: int):
                """Return a deterministic local integer."""
                executions.append(value)
                if outcome == "raise":
                    raise ValueError("synthetic tool error")
                return value + 1

            tool = StructuredTool.from_function(coroutine=lookup, name=name, description=lookup.__doc__)
            first = (
                AIMessage(
                    content="",
                    tool_calls=[{"name": provider_safe_tool_name(name), "args": {"value": 7}, "id": "c1"}],
                )
                if mode == "function_calling"
                else AIMessage(content=f"```python\nprint(await {name}(value=7))\n```")
            )
            verdict = AsyncMock(return_value="synthetic denial" if outcome == "denied" else None)
            with (
                patch.object(settings.policy, "enabled", False),
                patch.object(settings.evolve, "enabled", False),
                patch.object(
                    ToolGuardingToolProvider,
                    "_get_or_create_toolguard_runtime",
                    AsyncMock(return_value=SimpleNamespace(guard_tool_call=verdict)),
                ),
            ):
                callback = shared_handler or (
                    lf.create_sdk_tool_handler() if owner == "managed" else CallbackHandler()
                )
                defaults = [callback] if delivery == "constructor" else None
                real_agent = CugaAgent(
                    tools=[tool], model=ScriptedModel(first), execution_mode=mode, callbacks=defaults
                )
                real_agent.tool_provider.set_enabled(True)
                config = {"callbacks": [callback]} if owner != "sdk" and delivery != "constructor" else {}
                if roots == "trace_id":
                    from uuid import uuid4

                    trace_id = uuid4().hex
                    config["configurable"] = {"langfuse_trace_id": trace_id}
                    expected_trace_ids[name] = trace_id
                    result = await real_agent.invoke("Look up seven.", config=config, track_tool_calls=True)
                elif owner != "sdk":
                    with client.start_as_current_observation(name=f"parent.{name}", as_type="agent"):
                        result = await real_agent.invoke(
                            "Look up seven.", config=config, track_tool_calls=True
                        )
                else:
                    result = await real_agent.invoke("Look up seven.", track_tool_calls=True)
            assert executions == ([] if outcome == "denied" else [7])
            assert len(result.tool_calls) == 1, (name, result.tool_calls)
            assert result.tool_calls[0]["succeeded"] == (outcome == "ok")
            expected_tools[name] = outcome

        async def provider_probe(route, outcome, mode=None):
            from langchain_core.runnables.config import var_child_runnable_config
            from cuga.backend.cuga_graph.nodes.cuga_lite.providers import combined, registry
            from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import ToolCallTracker

            events = []

            class ObservedHandler(CallbackHandler):
                # Observe public callbacks while delegating unchanged to real Langfuse.
                def on_tool_start(self, *args, **kwargs):
                    events.append("start")
                    return super().on_tool_start(*args, **kwargs)

                def on_tool_end(self, *args, **kwargs):
                    events.append("end")
                    return super().on_tool_end(*args, **kwargs)

                def on_tool_error(self, *args, **kwargs):
                    events.append("error")
                    return super().on_tool_error(*args, **kwargs)

            async def fail():
                if outcome == "cancelled":
                    raise asyncio.CancelledError()
                raise ValueError("synthetic provider failure")

            async def post(session, host, payload, timeout):
                return await fail()

            async def invoke(app, name, args):
                return await fail()

            name = f"matrix_probe_{route}_{outcome}"
            tool_def = {
                "description": "Synthetic lookup.",
                "parameters": {"properties": {"value": {"type": "integer"}}, "required": ["value"]},
            }
            with (
                patch.object(registry, "post_registry_call", post),
                patch.object(combined.tracker, "invoke_tool", invoke),
            ):
                raw = (
                    registry.create_tool_from_api_dict(name, tool_def, "shop")
                    if route == "registry"
                    else combined.create_tool_from_tracker(name, tool_def, "shop")
                )
                provider = ToolGuardingToolProvider(None)
                provider._get_or_create_toolguard_runtime = AsyncMock(
                    return_value=SimpleNamespace(guard_tool_call=AsyncMock(return_value=None))
                )
                guarded = provider._wrap_tool(raw, "shop").coroutine
                with client.start_as_current_observation(name=f"parent.{name}", as_type="agent"):
                    callback = lf.create_sdk_tool_handler() if mode == "managed" else ObservedHandler()
                    token = var_child_runnable_config.set({"callbacks": [callback]})
                    ToolCallTracker.start_tracking()
                    try:
                        if outcome == "cancelled":
                            with pytest.raises(asyncio.CancelledError):
                                await guarded(value=3)
                        else:
                            response = await guarded(value=3)
                            assert "synthetic provider failure" in response["error"]
                    finally:
                        local = ToolCallTracker.stop_tracking()
                        var_child_runnable_config.reset(token)
            assert len(local) == 1 and local[0]["succeeded"] is False
            return name, events

        async def run():
            # Actual enablement/key checks, not a stubbed enabled() predicate.
            with patch.dict(os.environ, {"LANGFUSE_SECRET_KEY": ""}):
                assert not lf.enabled()
                await agent.invoke("off", thread_id="no-key")
            with patch.object(settings.advanced_features, "langfuse_tracing", False):
                assert not lf.enabled()
                await agent.invoke("off", thread_id="setting-off")
            lf.record_tool_call("no-parent", {}, "must-not-export")
            await agent.invoke("ok", thread_id="invoke-ok")
            await agent.invoke("returned-error", thread_id="invoke-returned-error")
            with pytest.raises(ValueError, match="synthetic exception"):
                await agent.invoke("raise", thread_id="invoke-raise")
            with pytest.raises(asyncio.CancelledError):
                await agent.invoke("cancel", thread_id="invoke-cancel")
            assert [x async for x in agent.stream("ok", thread_id="stream-ok")][-1] == {
                "final_answer": "done"
            }
            async with aclosing(agent.stream("ok", thread_id="stream-close")) as stream:
                assert await anext(stream) == {"final_answer": "partial"}
            with pytest.raises(asyncio.CancelledError):
                async for _ in agent.stream("cancel", thread_id="stream-cancel"):
                    pass
            with client.start_as_current_observation(name="caller.root", as_type="agent"):
                callbacks = [CallbackHandler()]
                await agent.invoke("ok", config={"callbacks": callbacks}, thread_id="caller-invoke")
                assert [x async for x in agent.stream("ok", config={"callbacks": callbacks})]
            await agent.invoke(
                "ok", config={"configurable": {"langfuse_trace_id": caller_trace_id}}, thread_id="caller-id"
            )
            sync_langfuse_callbacks_from_config({"configurable": {}})
            await supervisor.invoke("ok", thread_id="supervisor")
            # Real public inherited callback paths, not private run-map inspection.
            from uuid import uuid4
            from langchain_core.outputs import Generation, LLMResult

            handler = lf.create_sdk_tool_handler()
            chain_id, generation_id = uuid4(), uuid4()
            with client.start_as_current_observation(name="managed.external.root", as_type="agent"):
                handler.on_chain_start({"name": "managed.chain"}, {"synthetic": True}, run_id=chain_id)
                handler.on_llm_start(
                    {"name": "managed.generation"},
                    ["synthetic prompt"],
                    run_id=generation_id,
                    parent_run_id=chain_id,
                    invocation_params={"_type": "fake", "model_name": "synthetic"},
                )
                handler.on_llm_end(
                    LLMResult(generations=[[Generation(text="synthetic answer")]]),
                    run_id=generation_id,
                    parent_run_id=chain_id,
                )
                handler.on_chain_end({"synthetic": True}, run_id=chain_id)

            assert not get_current_span().get_span_context().is_valid
            for mode in ("codeact", "function_calling"):
                for owner in ("sdk", "caller", "managed"):
                    for outcome in ("ok", "denied", "raise"):
                        await prepared(mode, owner, outcome)
            for mode in ("codeact", "function_calling"):
                await prepared(mode, "managed", "ok", delivery="constructor")
                await prepared(mode, "managed", "raise", roots="trace_id")
            shared_handler = lf.create_sdk_tool_handler()
            await asyncio.gather(
                prepared("codeact", "managed", "ok", delivery="concurrent_a", shared_handler=shared_handler),
                prepared(
                    "function_calling",
                    "managed",
                    "raise",
                    delivery="concurrent_b",
                    shared_handler=shared_handler,
                ),
            )

        def attrs(span):
            return {a.key: a.value.string_value for a in span.attributes}

        try:
            if len(sys.argv) in (3, 4):
                name, events = asyncio.run(provider_probe(*sys.argv[1:]))
                lf.flush()
                spans = [s for s in packets if s.name == name]
                levels = [attrs(s).get("langfuse.observation.level") for s in spans]
                print(
                    f"PROBE {name}: callbacks={events}; completed={len(spans)}; levels={levels}; "
                    f"unmatched_starts={events.count('start') - events.count('end') - events.count('error')}",
                    flush=True,
                )
                if len(sys.argv) == 4 and sys.argv[3] == "managed":
                    assert len(spans) == 1 and levels == ["ERROR"]
                    assert events == [], events
                    assert not any(s.name == "cuga.tool.outcome" for s in packets)
                    parent = next(s for s in packets if s.name == f"parent.{name}")
                    assert spans[0].trace_id == parent.trace_id
                    assert spans[0].parent_span_id == parent.span_id
                    print("MANAGED verified: caller root preserved, exactly one complete ERROR tool")
                    return
                assert len(spans) == 1 and levels == ["ERROR"], "Missing truthful terminal provider outcome"
                return
            asyncio.run(run())
            lf.flush()
            assert packets
            generation = next(s for s in packets if s.name == "managed.generation")
            chain = next(s for s in packets if s.name == "managed.chain")
            external = next(s for s in packets if s.name == "managed.external.root")
            assert attrs(generation)["langfuse.observation.type"] == "generation"
            assert generation.parent_span_id == chain.span_id and chain.parent_span_id == external.span_id
            assert generation.trace_id == chain.trace_id == external.trace_id
            assert all(s.end_time_unix_nano >= s.start_time_unix_nano > 0 for s in packets)
            assert not any(
                attrs(s).get("session.id") in {"no-key", "setting-off", "caller-invoke", "caller-id"}
                and s.name == "cuga.invoke"
                for s in packets
            )
            assert not any(s.name == "no-parent" for s in packets)
            for session in ("invoke-raise", "invoke-cancel", "invoke-returned-error", "stream-cancel"):
                matches = [
                    s
                    for s in packets
                    if attrs(s).get("session.id") == session and s.name in {"cuga.invoke", "cuga.stream"}
                ]
                assert len(matches) == 1, session
                assert attrs(matches[0])["langfuse.observation.level"] == "ERROR"
                assert len(attrs(matches[0])["langfuse.observation.status_message"]) < 20100
            for session, output in (("stream-ok", "done"), ("stream-close", "partial")):
                span = next(
                    s for s in packets if attrs(s).get("session.id") == session and s.name == "cuga.stream"
                )
                assert attrs(span)["langfuse.observation.output"] == output
                assert attrs(span).get("langfuse.observation.level") != "ERROR"
            parent = next(s for s in packets if s.name == "cuga.supervisor")
            nested = [
                s for s in packets if s.name == "matrix.inner.tool" and s.parent_span_id == parent.span_id
            ]
            assert len(nested) == 1 and nested[0].trace_id == parent.trace_id
            assert any(s.name == "matrix.inner.tool" and s.trace_id.hex() == caller_trace_id for s in packets)
            for name, outcome in expected_tools.items():
                spans = [
                    s
                    for s in packets
                    if s.name == name and attrs(s).get("langfuse.observation.type") == "tool"
                ]
                assert len(spans) == 1, (name, [(s.name, attrs(s)) for s in spans])
                if name in expected_trace_ids:
                    assert spans[0].trace_id.hex() == expected_trace_ids[name]
                elif "_managed_" in name:
                    parent = next(s for s in packets if s.name == f"parent.{name}")
                    assert spans[0].trace_id == parent.trace_id
                    assert spans[0].parent_span_id == parent.span_id
                assert (attrs(spans[0]).get("langfuse.observation.level") == "ERROR") == (outcome != "ok"), (
                    name,
                    attrs(spans[0]),
                )
            print(
                f"Validated {len(expected_tools)} prepared route cases; {len(packets)} completed OTLP observations."
            )
        finally:
            client.shutdown()
            server.shutdown()


if __name__ == "__main__":
    _exercise()
