"""End-to-end: a CUGA agent talking to another CUGA agent via A2A.

This is the symmetry test. CUGA already speaks A2A as a *client* (via
``cuga.backend.cuga_graph.nodes.cuga_supervisor.a2a_protocol``). Once
the new server module lands, the same SDK that powers outbound delegation
must be able to talk to our own inbound endpoint.

We run two CUGAs in-process:

  Server CUGA  -- FastAPI app + A2A router + scripted graph
  Client CUGA  -- the existing supervisor delegation helpers, pointed
                  at the server via httpx.ASGITransport

If this test passes, CUGA's outbound and inbound A2A halves agree on the
wire format — the strongest single check we can run without standing up
a real network.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

a2a_client = pytest.importorskip("a2a.client")
a2a_types = pytest.importorskip("a2a.compat.v0_3.types")
httpx = pytest.importorskip("httpx")


pytestmark = pytest.mark.anyio


@pytest.fixture
async def server_asgi_client(app_with_a2a):
    """Server CUGA accessible via in-process httpx transport."""
    transport = httpx.ASGITransport(app=app_with_a2a)
    async with httpx.AsyncClient(transport=transport, base_url="http://server-cuga.local") as client:
        yield client


async def test_client_cuga_fetches_server_cuga_agent_card(server_asgi_client):
    """Client CUGA discovers Server CUGA exactly as it would discover any
    third-party A2A agent."""
    from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH

    resolver = a2a_client.A2ACardResolver(
        httpx_client=server_asgi_client,
        base_url="http://server-cuga.local",
        agent_card_path=AGENT_CARD_WELL_KNOWN_PATH,
    )
    card = await resolver.get_agent_card()
    assert card is not None
    assert card.name == "cuga-test"
    # Server CUGA must advertise at least one skill so the client can route to it.
    assert len(card.skills) >= 1


async def test_client_cuga_delegates_simple_task_to_server_cuga(app_with_a2a, scripted_runner):
    """The full delegation path: discover card, send message, receive
    response. This drives the actual production delegate_task_via_a2a_sdk
    helper at the in-process server CUGA via ASGI transport — same code
    path real cross-process delegation would take, just without the TCP
    hop. If this passes, both halves of CUGA's A2A surface agree."""
    from unittest.mock import patch

    from cuga.backend.cuga_graph.nodes.cuga_supervisor.a2a_protocol import (
        delegate_task_via_a2a_sdk,
    )

    transport = httpx.ASGITransport(app=app_with_a2a)
    real_async_client = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    class _Card:
        url = "http://server-cuga.local"

    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_supervisor.a2a_protocol.httpx.AsyncClient",
        side_effect=_factory,
    ):
        result = await delegate_task_via_a2a_sdk(_Card(), "please summarize")

    # The supervisor helper got a real answer back from the in-process server.
    assert result["status"] == "success"
    assert "42" in result["result"]  # scripted_runner replies with "the answer is 42"
    # And the server's scripted graph received the user's task verbatim.
    assert any("please summarize" in msg for (msg, _ctx) in scripted_runner.received)


async def test_streaming_events_propagate_end_to_end(server_asgi_client):
    """A2A streaming: the server must emit task updates the client SDK
    can consume incrementally."""
    payload = {
        "jsonrpc": "2.0",
        "id": "stream-e2e",
        "method": "message/stream",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": "stream please"}],
                "messageId": "m-1",
            }
        },
    }
    saw_lifecycle = False
    async with server_asgi_client.stream("POST", "/a2a", json=payload) as resp:
        assert resp.status_code == 200
        async for line in resp.aiter_lines():
            if "completed" in line or "working" in line:
                saw_lifecycle = True
    assert saw_lifecycle


async def test_context_id_isolation_between_concurrent_delegations(
    app_with_a2a, scripted_runner, fake_event_factory
):
    """Two concurrent delegations with distinct contextIds must not
    cross-contaminate threads on the server."""
    import asyncio

    transport = httpx.ASGITransport(app=app_with_a2a)
    async with httpx.AsyncClient(transport=transport, base_url="http://server-cuga.local") as c:

        async def call(text, ctx):
            payload = {
                "jsonrpc": "2.0",
                "id": f"req-{ctx}",
                "method": "message/send",
                "params": {
                    "message": {
                        "role": "user",
                        "parts": [{"kind": "text", "text": text}],
                        "messageId": uuid4().hex,
                        "contextId": ctx,
                    }
                },
            }
            return await c.post("/a2a", json=payload)

        responses = await asyncio.gather(
            call("task-A", "ctx-A"),
            call("task-B", "ctx-B"),
        )
    assert all(r.status_code == 200 for r in responses)
    contexts = {ctx for (_msg, ctx) in scripted_runner.received}
    assert {"ctx-A", "ctx-B"}.issubset(contexts)


@pytest.mark.xfail(reason="auth-token forwarding across A2A boundary not yet implemented")
async def test_auth_token_forwarded_on_delegation(server_asgi_client):
    """When the client CUGA authenticates to server CUGA, its bearer
    token must reach require_chat_access on the server side. Marked xfail
    until we ship token forwarding in v1."""
    raise AssertionError("token forwarding not implemented")
