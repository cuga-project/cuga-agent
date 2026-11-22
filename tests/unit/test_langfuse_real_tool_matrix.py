"""One trace per run and one tool observation per call, through the real SDK path.

Nothing between ``CugaAgent.invoke`` and the exported spans is stubbed: the real prepare node, ToolGuard wrapper,
``ActivityTracker.invoke_tool`` with a real ``StructuredTool`` (the MCP route), the real Langfuse 4 client and
LangChain ``CallbackHandler``, and OTLP export to a loopback collector. Only the model (scripted replies), the guard
verdict and the ``langfuse_tracing`` setting are fixed by the test.

Each ``(mode, route)`` pair runs in its own subprocess, so the Langfuse client singleton and the
``ActivityTracker`` singleton never leak between runs or into other tests.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = pytest.mark.unit

MODES = ["codeact", "function_calling"]
ROUTES = ["mcp", "direct"]
# owner: who owns the trace.
#   sdk            no caller handler; the SDK opens the root observation
#   stock_trace_id stock CallbackHandler(trace_context=...) plus configurable.langfuse_trace_id (the cuga-eval setup)
#   managed        create_sdk_tool_handler(trace_id=...) plus configurable.langfuse_trace_id
OWNERS = ["sdk", "stock_trace_id", "managed"]
# (guarded, outcome)
CASES = [(False, "allowed"), (False, "failed"), (True, "allowed"), (True, "failed"), (True, "denied")]
CANCEL_OWNERS = [
    "sdk",
    "managed",
]  # a stock handler gets no terminal callback on cancellation (documented limit)
# Documented stock-handler limit (docs/design/sdk-tool-observation-ownership.md): the caller's handler owns the
# observation, and a provider error that the wrapper returns as a value reaches it as a normal tool end. The call
# is still recorded once; only the level is DEFAULT instead of ERROR.
RETURNED_ERROR_LOOKS_SUCCESSFUL = {("mcp", "stock_trace_id", True, "failed")}


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("mode", MODES)
def test_one_trace_and_one_tool_observation_per_call(tmp_path, mode, route):
    result = subprocess.run(
        [sys.executable, __file__, mode, route],
        cwd=tmp_path,
        # Never read a developer's .env or real keys; the model is scripted, the OpenAI key only builds a client.
        env={
            "HOME": str(tmp_path),
            "PATH": os.defpath,
            "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
            "LANG": "C.UTF-8",
            "ENV_FILE": os.devnull,
            "OPENAI_API_KEY": "sk-test-dummy",  # pragma: allowlist secret
        },
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, result.stdout[-8000:] + result.stderr[-8000:]
    rows = json.loads(result.stdout.strip().splitlines()[-1])
    wrong = [row for row in rows if not row["ok"]]
    assert not wrong, json.dumps(wrong, indent=1)


def _run(mode: str, route: str) -> list[dict]:
    import asyncio
    import gzip
    import uuid
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from types import SimpleNamespace

    from langchain_core.messages import AIMessage
    from langchain_core.tools import StructuredTool
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

    packets: list = []

    class Collector(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            data = self.rfile.read(int(self.headers["Content-Length"]))
            if self.headers.get("Content-Encoding") == "gzip":
                data = gzip.decompress(data)
            body = ExportTraceServiceRequest.FromString(data)
            packets.extend(span for r in body.resource_spans for s in r.scope_spans for span in s.spans)
            self.send_response(200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Collector)
    Thread(target=server.serve_forever, daemon=True).start()
    os.environ.update(
        LANGFUSE_PUBLIC_KEY="pk-lf-cuga-synthetic-loopback",
        LANGFUSE_SECRET_KEY="sk-lf-cuga-synthetic-loopback",
        LANGFUSE_BASE_URL=f"http://127.0.0.1:{server.server_port}",
        LANGFUSE_MEDIA_UPLOAD_ENABLED="false",
        LANGFUSE_FLUSH_INTERVAL="60",
        LANGFUSE_TIMEOUT="2",
    )

    from langfuse import get_client
    from langfuse.langchain import CallbackHandler

    from cuga.backend.cuga_graph.nodes.cuga_lite.providers import combined
    from cuga.backend.cuga_graph.nodes.cuga_lite.providers.base import AppDefinition, ToolProviderInterface
    from cuga.backend.cuga_graph.nodes.cuga_lite.providers.toolguard import ToolGuardingToolProvider
    from cuga.backend.observability import langfuse_sdk as lf
    from cuga.config import settings
    from cuga.sdk import CugaAgent

    import cuga.backend.storage.facade as facade

    client = get_client()
    lf.enabled = lambda: True  # the setting only; client, callbacks, spans and exporter are real
    settings.policy.enabled = False
    settings.evolve.enabled = False
    # Like tests/unit/conftest.py: never open the real src/cuga/dbs/cuga.db.
    facade._local_db_path = lambda: os.path.join(os.getcwd(), "cuga.db")
    facade._storage_mode = lambda: "local"

    DENY, FAIL, HANG = 13, 66, 99

    async def lookup(value: int) -> int:
        """Look up a synthetic integer."""
        if value == FAIL:
            raise ValueError("synthetic tool failure")
        if value == HANG:
            await asyncio.Event().wait()
        return value + 1

    inner = StructuredTool.from_function(coroutine=lookup, name="lookup", description=lookup.__doc__)

    class ProviderFixture(ToolProviderInterface):
        """Hands the agent the real combined-provider wrapper, as an MCP server does."""

        def __init__(self, tool):
            self.tool = tool

        async def initialize(self):
            pass

        async def get_apps(self):
            return [AppDefinition(name="shop", description="Synthetic lookup service.")]

        async def get_tools(self, app_name):
            return [self.tool] if app_name == "shop" else []

        async def get_all_tools(self):
            return [self.tool]

    if route == "mcp":
        combined.tracker.tools = {"shop": [inner]}
        definition = {
            "description": lookup.__doc__,
            "parameters": {"properties": {"value": {"type": "integer"}}, "required": ["value"]},
        }
        wrapper = combined.create_tool_from_tracker("lookup", definition, "shop")

    async def verdict(*, app_name, function_name, arguments):
        return "synthetic denial" if arguments.get("value") == DENY else None

    async def runtime(_self):
        return SimpleNamespace(guard_tool_call=verdict)

    ToolGuardingToolProvider._get_or_create_toolguard_runtime = runtime

    class ScriptedModel:
        def __init__(self, value):
            if mode == "function_calling":
                first = AIMessage(
                    content="", tool_calls=[{"name": "lookup", "args": {"value": value}, "id": "c1"}]
                )
            else:
                first = AIMessage(content=f"```python\nprint(await lookup(value={value}))\n```")
            self.responses = [first, AIMessage(content="Synthetic answer.")]

        def bind_tools(self, tools, **kwargs):
            async def call(messages, config=None, **kw):
                return await self.ainvoke(messages, config=config, **kw)

            return SimpleNamespace(ainvoke=call)

        async def ainvoke(self, messages, config=None, **kwargs):
            return self.responses.pop(0)

    def attrs(span):
        return {a.key: a.value.string_value for a in span.attributes}

    async def one_run(owner, guarded, outcome):
        value = {"allowed": 7, "failed": FAIL, "denied": DENY, "cancelled": HANG}[outcome]
        model = ScriptedModel(value)
        if route == "mcp":
            agent = CugaAgent(tool_provider=ProviderFixture(wrapper), model=model, execution_mode=mode)
        else:
            agent = CugaAgent(tools=[inner], model=model, execution_mode=mode)
        agent.tool_provider.set_enabled(guarded)
        trace_id = uuid.uuid4().hex
        config = None
        if owner == "stock_trace_id":
            config = {
                "callbacks": [CallbackHandler(trace_context={"trace_id": trace_id})],
                "configurable": {"langfuse_trace_id": trace_id},
            }
        elif owner == "managed":
            config = {
                "callbacks": [lf.create_sdk_tool_handler(trace_id=trace_id)],
                "configurable": {"langfuse_trace_id": trace_id},
            }
        if outcome == "cancelled":
            try:
                await asyncio.wait_for(agent.invoke("Look up a number.", config=config), 2)
            except asyncio.TimeoutError:
                pass
        else:
            await agent.invoke("Look up a number.", config=config)

    rows = []

    async def exercise():
        combos = [(o, g, out) for o in OWNERS for g, out in CASES]
        combos += [(o, g, "cancelled") for o in CANCEL_OWNERS for g in (False, True)]
        for owner, guarded, outcome in combos:
            before = len(packets)
            error = None
            try:
                await one_run(owner, guarded, outcome)
            except Exception as e:  # report, do not stop the matrix
                import traceback

                error = f"{type(e).__name__}: {e} | " + " <- ".join(
                    f"{frame.filename.rsplit('/src/', 1)[-1]}:{frame.lineno}"
                    for frame in reversed(traceback.extract_tb(e.__traceback__)[-6:])
                )
            await asyncio.to_thread(lf.flush)
            spans = packets[before:]
            tools = [s for s in spans if attrs(s).get("langfuse.observation.type") == "tool"]
            levels = [attrs(s).get("langfuse.observation.level", "DEFAULT") for s in tools]
            want_error = (
                outcome != "allowed"
                and (route, owner, guarded, outcome) not in RETURNED_ERROR_LOOKS_SUCCESSFUL
            )
            ok = (
                error is None
                and len(tools) == 1
                and len({s.trace_id for s in spans}) == 1
                and (levels == ["ERROR"]) == want_error
            )
            rows.append(
                {
                    "mode": mode,
                    "route": route,
                    "owner": owner,
                    "guarded": guarded,
                    "outcome": outcome,
                    "ok": ok,
                    "tool_observations": len(tools),
                    "traces": len({s.trace_id for s in spans}),
                    "levels": levels,
                    "error": error,
                }
            )

    try:
        asyncio.run(exercise())
    finally:
        client.shutdown()
        server.shutdown()
    return rows


if __name__ == "__main__":
    print(json.dumps(_run(sys.argv[1], sys.argv[2])))
