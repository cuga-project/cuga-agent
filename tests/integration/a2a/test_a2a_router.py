"""Integration tests for the A2A FastAPI router.

These drive the router via httpx.ASGITransport (no port bind) and assert
on the JSON-RPC + AgentCard contract. The CUGA graph is replaced with a
scripted runner via the `app_with_a2a` fixture in conftest.py.

Bug classes these target:
- malformed AgentCard at /.well-known/agent.json
- mounting the router unconditionally (it must be opt-in)
- raising HTTP 500 on protocol-level errors instead of returning a
  JSON-RPC error envelope (a real client would crash)
- silently ignoring unknown JSON-RPC methods
- regressing on the existing /stream endpoint
"""

from __future__ import annotations

import json

import pytest

a2a_types = pytest.importorskip("a2a.compat.v0_3.types")


pytestmark = pytest.mark.anyio


async def test_well_known_agent_card_returns_valid_card(asgi_client):
    resp = await asgi_client.get("/.well-known/agent.json")
    assert resp.status_code == 200
    body = resp.json()
    # Round-trip via the SDK's Pydantic model — the most robust contract check.
    card = a2a_types.AgentCard.model_validate(body)
    assert card.name
    assert card.capabilities is not None


async def test_well_known_agent_card_advertises_streaming(asgi_client):
    resp = await asgi_client.get("/.well-known/agent.json")
    body = resp.json()
    assert body["capabilities"]["streaming"] is True


async def test_well_known_404_when_router_not_mounted(app_a2a_disabled):
    """When the [a2a] section is disabled, main.py must not mount the
    router. This test simulates that by using an app with no router."""
    httpx = pytest.importorskip("httpx")
    transport = httpx.ASGITransport(app=app_a2a_disabled)
    async with httpx.AsyncClient(transport=transport, base_url="http://test.local") as client:
        resp = await client.get("/.well-known/agent.json")
    assert resp.status_code == 404


async def test_send_message_happy_path_returns_jsonrpc_envelope(asgi_client):
    """JSON-RPC 2.0 contract: response carries jsonrpc=2.0, the same id,
    and a `result` field. Failing this breaks every A2A client."""
    request_id = "req-123"
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": "hello"}],
                "messageId": "m-1",
            }
        },
    }
    resp = await asgi_client.post("/a2a", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("jsonrpc") == "2.0"
    assert body.get("id") == request_id
    assert "result" in body and "error" not in body


async def test_send_message_invokes_underlying_runner(asgi_client, scripted_runner):
    """The router must actually call the graph runner with the user text.
    A router that returns a static answer would pass the contract test
    above but fail this one."""
    payload = {
        "jsonrpc": "2.0",
        "id": "req-1",
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": "what is 2+2"}],
                "messageId": "m-1",
            }
        },
    }
    await asgi_client.post("/a2a", json=payload)
    assert any("what is 2+2" in msg for (msg, _ctx) in scripted_runner.received)


async def test_invalid_jsonrpc_returns_jsonrpc_error_not_http_500(asgi_client):
    """Per JSON-RPC 2.0, malformed envelopes return -32600/-32700 with
    HTTP 200, NOT an HTTP 5xx. Real clients only parse the JSON body."""
    resp = await asgi_client.post(
        "/a2a", content=b"{not valid json", headers={"content-type": "application/json"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("jsonrpc") == "2.0"
    assert "error" in body
    # -32700 = Parse error, -32600 = Invalid Request
    assert body["error"]["code"] in (-32700, -32600)


async def test_unknown_method_returns_method_not_found(asgi_client):
    payload = {
        "jsonrpc": "2.0",
        "id": "req-1",
        "method": "methods/does_not_exist",
        "params": {},
    }
    resp = await asgi_client.post("/a2a", json=payload)
    body = resp.json()
    assert "error" in body
    assert body["error"]["code"] == -32601  # Method not found


async def test_streaming_subscribe_yields_lifecycle(asgi_client):
    """`message/stream` (SSE) must yield at least one working update and
    a terminal completed event. The id from the request must be echoed
    in each chunk so a client can correlate."""
    payload = {
        "jsonrpc": "2.0",
        "id": "stream-1",
        "method": "message/stream",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": "go"}],
                "messageId": "m-1",
            }
        },
    }
    async with asgi_client.stream("POST", "/a2a", json=payload) as resp:
        assert resp.status_code == 200
        chunks = []
        async for line in resp.aiter_lines():
            line = line.strip()
            if not line:
                continue
            # SSE: "data: {...}"
            if line.startswith("data:"):
                line = line[len("data:") :].strip()
            try:
                chunks.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    # Every chunk is a JSON-RPC frame keyed back to our request id.
    assert all(c.get("id") == "stream-1" for c in chunks if "id" in c)
    # We saw a terminal-ish event somewhere in the stream.
    serialized = json.dumps(chunks)
    assert "completed" in serialized or "final" in serialized


async def test_sse_stream_emits_jsonrpc_error_when_runner_raises(fake_event_factory, a2a_settings):
    """If the runner blows up mid-stream, clients must still receive a
    parseable JSON-RPC error frame — not a half-open stream they can't
    distinguish from a network drop. Earlier the SSE generator just
    propagated the exception and SSE silently terminated."""
    pytest.importorskip("cuga.backend.server.a2a")
    from fastapi import FastAPI

    from cuga.backend.server.a2a import build_router

    class _BlowingRunner:
        async def run(self, message, context_id=None, approval=None):
            raise RuntimeError("simulated graph crash")
            yield  # pragma: no cover — mark as async generator

    app = FastAPI()
    app.include_router(build_router(runner=_BlowingRunner(), settings=a2a_settings))

    httpx = pytest.importorskip("httpx")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test.local") as client:
        payload = {
            "jsonrpc": "2.0",
            "id": "stream-err",
            "method": "message/stream",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"kind": "text", "text": "boom"}],
                    "messageId": "m-1",
                }
            },
        }
        chunks = []
        async with client.stream("POST", "/a2a", json=payload) as resp:
            assert resp.status_code == 200
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line:
                    continue
                if line.startswith("data:"):
                    line = line[len("data:") :].strip()
                try:
                    chunks.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    assert chunks, "expected at least one frame, got an empty stream"
    error_frames = [c for c in chunks if "error" in c]
    assert error_frames, f"expected a JSON-RPC error frame, got {chunks!r}"
    err = error_frames[-1]["error"]
    assert err["code"] == -32603  # internal error
    # Wire message must NOT contain the raw exception text.
    assert "simulated graph crash" not in err.get("message", "")


async def test_jsonrpc_send_does_not_leak_raw_exception_text(fake_event_factory, a2a_settings):
    """When the runner raises during ``message/send``, the JSON envelope
    must surface a generic message (plus exception class) — never the
    raw ``str(exc)`` which can leak paths/config."""
    pytest.importorskip("cuga.backend.server.a2a")
    from fastapi import FastAPI

    from cuga.backend.server.a2a import build_router

    class _BlowingRunner:
        async def run(self, message, context_id=None, approval=None):
            raise RuntimeError("/secret/path/leak")
            yield  # pragma: no cover

    app = FastAPI()
    app.include_router(build_router(runner=_BlowingRunner(), settings=a2a_settings))

    httpx = pytest.importorskip("httpx")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test.local") as client:
        resp = await client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "leak-check",
                "method": "message/send",
                "params": {
                    "message": {
                        "role": "user",
                        "parts": [{"kind": "text", "text": "go"}],
                        "messageId": "m-1",
                    }
                },
            },
        )
    body = resp.json()
    assert "error" in body
    assert body["error"]["code"] == -32603
    assert "/secret/path/leak" not in body["error"]["message"]
    assert "RuntimeError" in body["error"]["message"]  # class is fine; details aren't


async def test_context_id_propagates_to_graph_runner(asgi_client, scripted_runner):
    """contextId in the A2A request must be passed to the graph runner so
    multi-turn conversations land on the same thread."""
    payload = {
        "jsonrpc": "2.0",
        "id": "req-1",
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": "follow-up"}],
                "messageId": "m-2",
                "contextId": "ctx-existing-thread",
            }
        },
    }
    await asgi_client.post("/a2a", json=payload)
    contexts = [ctx for (_msg, ctx) in scripted_runner.received]
    assert "ctx-existing-thread" in contexts
