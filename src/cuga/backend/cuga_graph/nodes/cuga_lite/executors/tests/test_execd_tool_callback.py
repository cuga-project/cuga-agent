"""Tool calls from execd sandbox code, answered by CUGA over /sandbox/tools/call.

The stubs are the code the sandbox runs, so they are exercised for real: the
generated source is exec'd here and its HTTP calls reach a live server that
routes them through ``handle_call``, the same function the FastAPI endpoint
uses. Only the sandbox itself is absent.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import decimal
import json
import re
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from pydantic import BaseModel

from cuga.backend.cuga_graph.nodes.cuga_lite.executors.execd import tool_callback
from cuga.backend.cuga_graph.nodes.cuga_lite.tracking.tracker import (
    BlockToolCallBudgetExceeded,
    ToolCallTracker,
    counted_tool_call,
)
from cuga.config import settings

pytestmark = pytest.mark.unit


@pytest.fixture
async def cuga_server():
    """An HTTP server standing in for CUGA's /sandbox/tools/call.

    It runs ``handle_call`` on the test's own event loop, as uvicorn would run
    the endpoint on the server's loop, so tools see the loop their state lives on.
    """
    loop = asyncio.get_running_loop()
    seen: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - http.server API
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(body)
            status, payload = asyncio.run_coroutine_threadsafe(tool_callback.handle_call(body), loop).result(
                30
            )
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}", seen
    server.shutdown()


async def _run_in_fake_kernel(namespace: dict, code: str, url: str, timeout: float = 10.0) -> dict:
    """Register the block, define its stubs and run ``code`` as the sandbox would.

    The "kernel" gets its own thread and event loop: in production it is another
    process, and a plain (sync) stub call blocks it while CUGA answers.
    """
    async with tool_callback.registered_block(namespace, "thread-t", timeout) as block:
        src = tool_callback.stub_code(
            namespace, url=url, block_id=block.block_id, token=block.token, timeout=timeout
        )

        def kernel_run() -> dict:
            kernel: dict = {}
            exec(compile(src, "<stubs>", "exec"), kernel)
            exec(
                compile(f"async def _main():\n{_indent(code)}\n    return locals()", "<block>", "exec"),
                kernel,
            )
            return asyncio.run(kernel["_main"]())

        return await asyncio.to_thread(kernel_run)


def _indent(code: str) -> str:
    return "\n".join("    " + line for line in code.splitlines())


async def test_async_and_sync_tools_run_in_cuga_and_return_their_results(cuga_server):
    url, seen = cuga_server
    calls = []

    async def crm_get_customers(region: str):
        """Customers in a region."""
        calls.append(region)
        return [{"name": "Acme", "region": region}]

    def fc_keep(value):
        return {"kept": value}

    out = await _run_in_fake_kernel(
        {"crm_get_customers": crm_get_customers, "_fc_keep": fc_keep, "x": 5},
        "rows = await crm_get_customers(region='EU')\nkept = _fc_keep(rows[0]['name'])",
        url,
    )

    assert out["rows"] == [{"name": "Acme", "region": "EU"}]
    assert out["kept"] == {"kept": "Acme"}
    assert calls == ["EU"]
    assert [b["name"] for b in seen] == ["crm_get_customers", "_fc_keep"]


async def test_concurrent_tool_calls_with_gather(cuga_server):
    url, _ = cuga_server

    async def double(x):
        await asyncio.sleep(0.05)
        return x * 2

    out = await _run_in_fake_kernel(
        {"double": double},
        "import asyncio\nr = await asyncio.gather(double(1), double(2), double(3))",
        url,
    )
    assert out["r"] == [2, 4, 6]


async def test_tool_exception_is_reraised_in_the_sandbox_with_its_message(cuga_server):
    url, _ = cuga_server

    async def fails():
        raise PermissionError("no access to that account")

    out = await _run_in_fake_kernel(
        {"fails": fails},
        "try:\n    await fails()\nexcept Exception as e:\n    err = (e.type, str(e))",
        url,
    )
    assert out["err"] == ("PermissionError", "no access to that account")


async def test_policy_wrapper_runs_because_the_namespace_callable_is_what_is_called(cuga_server):
    """ToolGuard wraps the namespace callable; the sandbox call reaches that wrapper, not the raw tool."""
    url, _ = cuga_server
    raw_calls = []

    async def raw_transfer(amount):
        raw_calls.append(amount)
        return "sent"

    async def guarded_transfer(amount):
        if amount > 100:
            return {"error": "Tool call blocked by policy: limit is 100", "blocked_by_policy": True}
        return await raw_transfer(amount)

    out = await _run_in_fake_kernel(
        {"transfer": guarded_transfer},
        "big = await transfer(500)\nsmall = await transfer(5)",
        url,
    )
    assert out["big"]["blocked_by_policy"] is True
    assert out["small"] == "sent"
    assert raw_calls == [5]


async def test_call_budget_is_charged_in_the_block_context(cuga_server, monkeypatch):
    """Calls from the sandbox count against the block's budget, set up by the turn before the block was sent."""
    url, _ = cuga_server
    monkeypatch.setattr(settings.advanced_features, "max_tool_calls_per_block", 2, raising=False)

    async def ping():
        return "pong"

    ToolCallTracker.seed_call_budget(0)
    ToolCallTracker.seed_block_budget()
    out = await _run_in_fake_kernel(
        {"ping": counted_tool_call(ping)},
        "a = await ping()\nb = await ping()\ntry:\n    await ping()\nexcept Exception as e:\n    refused = e.type",
        url,
    )
    assert out["a"] == out["b"] == "pong"
    assert out["refused"] == BlockToolCallBudgetExceeded.__name__
    assert ToolCallTracker.get_run_budget_used() == 2


async def test_in_kernel_tools_are_charged_against_the_budget(cuga_server, monkeypatch):
    """Filesystem tools and run_command run in the kernel, past the counted wrappers; they still pay."""
    from cuga.backend.cuga_graph.nodes.cuga_lite.executors.execd import execd_executor, kernel_templates

    url, seen = cuga_server
    monkeypatch.setattr(settings.advanced_features, "max_tool_calls_per_block", 2, raising=False)

    ToolCallTracker.seed_call_budget(0)
    ToolCallTracker.seed_block_budget()
    namespace = {kernel_templates.CHARGE_NAME: execd_executor._budget_charge()}
    # Top-level, as the generated filesystem/shell definitions are in the real kernel.
    kernel_tool = (
        "async def read_file(path):\n"
        "    return 'body of ' + path\n" + kernel_templates.charged_tools_code(["read_file", "absent_tool"])
    )
    code = (
        "a = await read_file('a')\nb = await read_file('b')\n"
        "try:\n    await read_file('c')\nexcept Exception as e:\n    refused = e.type"
    )
    async with tool_callback.registered_block(namespace, "thread-t", 10.0) as block:
        src = tool_callback.stub_code(
            namespace, url=url, block_id=block.block_id, token=block.token, timeout=10.0
        )

        def kernel_run() -> dict:
            kernel: dict = {}
            exec(compile(src + kernel_tool, "<stubs>", "exec"), kernel)
            exec(
                compile(f"async def _main():\n{_indent(code)}\n    return locals()", "<block>", "exec"),
                kernel,
            )
            return asyncio.run(kernel["_main"]())

        out = await asyncio.to_thread(kernel_run)

    assert out["a"] == "body of a" and out["b"] == "body of b"
    assert out["refused"] == BlockToolCallBudgetExceeded.__name__
    assert ToolCallTracker.get_run_budget_used() == 2
    assert {c["name"] for c in seen} == {kernel_templates.CHARGE_NAME}


async def test_callable_table_becomes_a_table_of_stubs(cuga_server):
    url, seen = cuga_server

    async def weird_name(q):
        return f"got {q}"

    out = await _run_in_fake_kernel(
        {"_fc_tools": {"my-tool.v2": weird_name}},
        "r = await _fc_tools['my-tool.v2'](q='hi')",
        url,
    )
    assert out["r"] == "got hi"
    assert seen[-1]["name"] == "_fc_tools[my-tool.v2]"


async def test_modules_in_the_namespace_are_imported_not_called(cuga_server):
    url, _ = cuga_server
    out = await _run_in_fake_kernel({"_internal_re": re}, "m = bool(_internal_re.match('a', 'abc'))", url)
    assert out["m"] is True


async def test_results_are_converted_to_json_shapes():
    class Model(BaseModel):
        when: dt.datetime
        amount: decimal.Decimal

    value = {
        "model": Model(when=dt.datetime(2026, 1, 2, 3, 4, 5), amount=decimal.Decimal("1.5")),
        "tags": {"a"},
        "raw": b"\x00\x01",
        "obj": object.__new__(type("Opaque", (), {"__str__": lambda self: "opaque!"})),
    }
    out = tool_callback.to_jsonable(value)
    assert out["model"] == {"when": "2026-01-02T03:04:05", "amount": 1.5}
    assert out["tags"] == ["a"]
    assert out["raw"] == "AAE="
    assert out["obj"] == "opaque!"
    json.dumps(out)


async def test_wrong_token_is_refused():
    async def tool():
        return 1

    async with tool_callback.registered_block({"tool": tool}, "t", 10) as blk:
        status, body = await tool_callback.handle_call(
            {"block": blk.block_id, "token": "0" * 64, "name": "tool", "args": [], "kwargs": {}}
        )
    assert status == 403 and body["ok"] is False


async def test_call_after_the_block_finished_is_refused():
    async def tool():
        return 1

    async with tool_callback.registered_block({"tool": tool}, "t", 10) as blk:
        pass
    status, body = await tool_callback.handle_call(
        {"block": blk.block_id, "token": blk.token, "name": "tool", "args": [], "kwargs": {}}
    )
    assert status == 410
    assert "finished" in body["error"]["message"]


async def test_only_the_blocks_own_callables_are_reachable():
    async def mine():
        return "mine"

    async def theirs():
        return "theirs"

    async with tool_callback.registered_block({"mine": mine}, "t1", 10) as h1:
        async with tool_callback.registered_block({"theirs": theirs}, "t2", 10) as h2:
            status, body = await tool_callback.handle_call(
                {"block": h1.block_id, "token": h1.token, "name": "theirs", "args": [], "kwargs": {}}
            )
            assert status == 404
            # A valid token for one block does not open another.
            status, _ = await tool_callback.handle_call(
                {"block": h2.block_id, "token": h1.token, "name": "theirs", "args": [], "kwargs": {}}
            )
            assert status == 403


async def test_oversized_result_is_an_error_not_a_payload(monkeypatch):
    monkeypatch.setattr(tool_callback, "MAX_RESULT_BYTES", 100)

    async def big():
        return "x" * 1000

    async with tool_callback.registered_block({"big": big}, "t", 10) as blk:
        status, body = await tool_callback.handle_call(
            {"block": blk.block_id, "token": blk.token, "name": "big", "args": [], "kwargs": {}}
        )
    assert status == 200 and body["ok"] is False
    assert "limit" in body["error"]["message"]


def test_callback_url_is_only_the_explicit_setting(monkeypatch):
    monkeypatch.setattr(
        settings.advanced_features, "sandbox_callback_url", "http://cuga.ns.svc:7860/", raising=False
    )
    assert tool_callback.callback_url() == "http://cuga.ns.svc:7860"
    # function_call_host names the tool registry, not this server: no fallback to it (item 26).
    monkeypatch.setattr(settings.advanced_features, "sandbox_callback_url", "", raising=False)
    monkeypatch.setattr(settings.server_ports, "function_call_host", "http://registry:8001", raising=False)
    assert tool_callback.callback_url() == ""


@pytest.mark.parametrize(
    "block, token",
    [
        ("é" * 32, "a" * 64),  # non-ASCII used to raise inside hmac and give a 500
        ("a" * 32, "é" * 64),
        ("a" * 31, "a" * 64),
        ("A" * 32, "a" * 64),
        (None, "a" * 64),
        ("a" * 32, 5),
    ],
)
async def test_malformed_block_or_token_is_a_400(block, token):
    status, body = await tool_callback.handle_call({"block": block, "token": token, "name": "x"})
    assert status == 400
    assert body["error"]["type"] == "CallRefused"


async def test_a_control_signal_ends_the_block_and_is_reraised_in_cuga(cuga_server):
    """A BaseException from a tool (ACP permission pause) must reach the graph, not be swallowed by agent code."""
    url, _ = cuga_server

    class PauseForPermission(BaseException):
        pass

    async def delegate():
        raise PauseForPermission("waiting for the user")

    after = []

    async def later():
        after.append(1)

    namespace = {"delegate": delegate, "later": later}
    async with tool_callback.registered_block(namespace, "thread-t", 10) as block:
        src = tool_callback.stub_code(
            namespace, url=url, block_id=block.block_id, token=block.token, timeout=10
        )

        def kernel_run():
            kernel: dict = {}
            exec(compile(src, "<stubs>", "exec"), kernel)
            # Agent code that catches every Exception must still not swallow the pause.
            body = "try:\n        await delegate()\n    except Exception:\n        pass\n    await later()"
            exec(compile(f"async def _main():\n    {body}", "<block>", "exec"), kernel)
            try:
                asyncio.run(kernel["_main"]())
            except BaseException as exc:  # the kernel reports the block as failed
                return type(exc).__name__
            return "completed"

        assert await asyncio.to_thread(kernel_run) == "_CugaStop"
        with pytest.raises(PauseForPermission):
            block.raise_pending_signal()
    assert after == []  # nothing after the pause ran


def _request(body: bytes, headers: dict | None = None):
    """A real Starlette request carrying ``body``, as uvicorn would hand it to the route."""
    from starlette.requests import Request

    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": "POST", "headers": raw_headers}, receive)


async def test_the_callback_route_does_not_exist_while_no_block_runs():
    """A server without the sandbox exposes nothing at /sandbox/tools/call (review item 3)."""
    from fastapi import HTTPException

    from cuga.backend.server.main import sandbox_tool_call

    assert not tool_callback.has_blocks()
    with pytest.raises(HTTPException) as exc:
        await sandbox_tool_call(_request(b'{"block": "x"}'))
    assert exc.value.status_code == 404

    async def tool():
        return "ok"

    async with tool_callback.registered_block({"tool": tool}, "t1", 10) as block:
        assert tool_callback.has_blocks()
        body = {"block": block.block_id, "token": block.token, "name": "tool", "args": [], "kwargs": {}}
        response = await sandbox_tool_call(_request(json.dumps(body).encode()))
        assert response.status_code == 200
        assert json.loads(response.body)["result"] == "ok"


async def test_an_oversized_call_is_a_413_without_reading_it_all():
    from cuga.backend.server.main import sandbox_tool_call

    async def tool():
        return "ok"  # pragma: no cover

    limit = tool_callback.MAX_RESULT_BYTES + 64 * 1024
    async with tool_callback.registered_block({"tool": tool}, "t1", 10):
        # Declared too large: refused from the header.
        response = await sandbox_tool_call(_request(b"{}", {"content-length": str(limit + 1)}))
        assert response.status_code == 413
        # No usable header (as with chunked bodies): refused while reading.
        response = await sandbox_tool_call(_request(b"x" * (limit + 1)))
        assert response.status_code == 413


@pytest.mark.parametrize("doc", ['Set status to "active"', r"Read C:\new\x01 or \N{...}", "Ends with '''"])
def test_a_tool_description_cannot_break_the_stub_code(doc):
    """MCP and OpenAPI descriptions are not ours; any first line must still compile as a docstring."""

    def sync_tool():
        return None

    async def async_tool():
        return None

    sync_tool.__doc__ = async_tool.__doc__ = doc + "\nsecond line"
    src = tool_callback.stub_code(
        {"sync_tool": sync_tool, "async_tool": async_tool}, url="http://x", block_id="b", token="t", timeout=1
    )

    kernel: dict = {}
    exec(compile(src, "<stubs>", "exec"), kernel)
    assert kernel["sync_tool"].__doc__ == doc
    assert kernel["async_tool"].__doc__ == doc
