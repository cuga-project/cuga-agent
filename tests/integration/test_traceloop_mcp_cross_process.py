"""End-to-end MCP cross-process trace-context propagation (Phase 5 / DP4).

Verifies the save-and-reuse loop's generated `saved_flows.py` (a FastMCP/SSE
server) and a raw `mcp` client (mirroring chat_agent.py's own connection
code: `mcp.client.sse.sse_client` + `mcp.ClientSession`) produce one shared
`trace_id` spanning both processes, once each side has its own real
`Traceloop.init()` — this is the "each demo server needs its own init in a
separate OS process" requirement from DP4/Phase 5, not something that falls
out of `Instruments.MCP` alone. See docs/traceloop-instrumentation-plan.md
Phase 5 and docs/traceloop-instrumentation-spec.md's "MCP trace-context
propagation" subsection under DP4.

Both the generated server and the client run as **separate real OS
subprocesses** (not in-process substitutes) — this is what actually proves
cross-process propagation, and it also sidesteps the singleton-collision
issue that forced test_traceloop_a2a_propagation.py into its own colocated
`tests/` dir (Phase 4 finding): this test file's own pytest process never
calls a real, un-mocked `Traceloop.init()` itself, only two child processes
do, each in their own interpreter — so it coexists safely with any other
test file here, and needs no special CI placement.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _trace_ids_from_file(path: Path) -> set[str]:
    """Decode trace_ids out of a LocalOtlpFileSpanExporter JSON-lines file.

    traceId is base64-encoded (protobuf-JSON mapping for a `bytes` field),
    not hex — unlike a `traceparent` header's hex string. Decode to compare.
    """
    ids: set[str] = set()
    if not path.exists():
        return ids
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        for resource_span in payload["resourceSpans"]:
            for scope_span in resource_span.get("scopeSpans", []):
                for span in scope_span.get("spans", []):
                    ids.add(base64.b64decode(span["traceId"]).hex())
    return ids


_CLIENT_SCRIPT = textwrap.dedent(
    """
    import asyncio
    import json
    import sys

    # Real, un-mocked process-level init — triggered at import time, same
    # as chat_agent.py's own process. Instruments.MCP (DP4) patches
    # mcp.client.sse.sse_client / mcp.ClientSession's send/receive hooks.
    import cuga.backend.observability.traceloop_init as _traceloop_init  # noqa: F401

    from opentelemetry import trace as otel_trace
    from mcp.client.sse import sse_client
    from mcp import ClientSession


    async def main(url: str, result_path: str) -> None:
        tracer = otel_trace.get_tracer(__name__)
        with tracer.start_as_current_span("client-outer") as span:
            expected_trace_id = format(span.get_span_context().trace_id, "032x")
            async with sse_client(url) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool("double", {"x": 21})

        result_data = {
            "expected_trace_id": expected_trace_id,
            "is_error": bool(result.isError),
        }
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(result_data, f)


    asyncio.run(main(sys.argv[1], sys.argv[2]))
    """
)


@pytest.fixture
def _port():
    return _free_port()


@pytest.fixture
def _generated_flows_server(tmp_path, _port, monkeypatch):
    """Generate a real saved_flows.py via export_mcp.py's own template
    (the exact code path the save-and-reuse feature uses), with one
    deterministic tool — proving the *actual* generated-server template
    carries the instrumentation import, not a hand-written stand-in.

    The port is baked into the template as a literal at generation time
    (`port={settings.server_ports.saved_flows}`), not read at runtime by
    the generated script — so it must be patched before generating, not
    passed via the subprocess env.
    """
    from cuga.config import settings
    from cuga.backend.cuga_graph.nodes.save_reuse.save_reuse_agent.utils.export_mcp import (
        generate_or_update_server,
    )

    monkeypatch.setattr(settings.server_ports, "saved_flows", _port)

    server_path = tmp_path / "saved_flows.py"
    funcs = [{"name": "double", "source": "def double(x: int) -> int:\n    return x * 2"}]
    ok = generate_or_update_server(funcs, [], server_path, "create")
    assert ok, "export_mcp.py failed to generate saved_flows.py"
    content = server_path.read_text(encoding="utf-8")
    assert "cuga.backend.observability.traceloop_init" in content, (
        "generated server template is missing the Phase 5 instrumentation import"
    )
    return server_path


async def test_mcp_cross_process_trace_id_shared(_generated_flows_server, _port, tmp_path):
    port = _port
    server_trace_file = tmp_path / "server_spans.jsonl"
    client_trace_file = tmp_path / "client_spans.jsonl"
    client_result_file = tmp_path / "client_result.json"
    client_script = tmp_path / "mcp_client.py"
    client_script.write_text(_CLIENT_SCRIPT, encoding="utf-8")

    base_env = os.environ.copy()
    base_env["DYNACONF_OBSERVABILITY__TRACELOOP"] = "true"
    base_env["DYNACONF_OBSERVABILITY__TRACELOOP_EXPORTER"] = "file"

    server_env = dict(base_env, DYNACONF_OBSERVABILITY__TRACELOOP_FILE_PATH=str(server_trace_file))
    server_proc = subprocess.Popen(
        [sys.executable, str(_generated_flows_server)],
        env=server_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        url = f"http://127.0.0.1:{port}/sse"
        _wait_tcp(port, timeout=30.0)

        client_env = dict(base_env, DYNACONF_OBSERVABILITY__TRACELOOP_FILE_PATH=str(client_trace_file))
        client_proc = subprocess.run(
            [sys.executable, str(client_script), url, str(client_result_file)],
            env=client_env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert client_proc.returncode == 0, (
            f"MCP client subprocess failed:\nstdout={client_proc.stdout}\nstderr={client_proc.stderr}"
        )
    finally:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server_proc.kill()
            server_proc.wait(timeout=10)

    assert client_result_file.exists(), "client subprocess did not write its result file"
    client_result = json.loads(client_result_file.read_text(encoding="utf-8"))
    assert not client_result["is_error"], "MCP tool call reported an error"
    expected_trace_id = client_result["expected_trace_id"]

    server_trace_ids = _trace_ids_from_file(server_trace_file)
    assert server_trace_ids, (
        f"generated server produced no spans (server output:\n{server_proc.stdout.read().decode() if server_proc.stdout else ''})"
    )
    assert expected_trace_id in server_trace_ids, (
        f"client's trace_id {expected_trace_id} not found among server-side trace_ids "
        f"{server_trace_ids} — MCP cross-process propagation did not link the two processes"
    )


def _wait_tcp(port: int, timeout: float) -> None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            try:
                s.connect(("127.0.0.1", port))
                return
            except OSError:
                time.sleep(0.2)
    raise TimeoutError(f"generated saved_flows.py server did not start listening on port {port}")
