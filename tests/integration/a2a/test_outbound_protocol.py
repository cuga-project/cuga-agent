"""Tests for the rewritten outbound A2A client.

The previous SDK-1.x-incompatible imports made this code path silently
no-op; these tests pin the new behavior so the regression doesn't recur.
The strongest check is the round-trip: drive the rewritten outbound
client at the in-process A2A router via ``httpx.ASGITransport`` and
assert text flows end-to-end.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

a2a_types = pytest.importorskip("a2a.compat.v0_3.types")
httpx = pytest.importorskip("httpx")
pytest.importorskip("cuga.backend.cuga_graph.nodes.cuga_supervisor.a2a_protocol")


pytestmark = pytest.mark.anyio


def test_module_reports_sdk_available():
    """If this flips back to False, every HTTP-A2A delegation silently
    no-ops. A bare assertion is the cheapest tripwire we can install."""
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.a2a_protocol import HAS_A2A_SDK

    assert HAS_A2A_SDK is True


def test_extract_text_picks_latest_agent_message():
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.a2a_protocol import _extract_text_from_task

    task = {
        "history": [
            {"role": "user", "parts": [{"text": "hi"}]},
            {"role": "agent", "parts": [{"text": "first reply"}]},
            {"role": "agent", "parts": [{"text": "final reply"}]},
        ],
    }
    assert _extract_text_from_task(task) == "final reply"


def test_extract_text_falls_back_to_status_message():
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.a2a_protocol import _extract_text_from_task

    task = {
        "history": [],
        "status": {"message": {"parts": [{"text": "from status"}]}},
    }
    assert _extract_text_from_task(task) == "from status"


def test_agent_card_rpc_url_uses_card_url_when_endpoint_already_set():
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.a2a_protocol import _agent_card_rpc_url

    class _Card:
        url = "https://peer.example/a2a"

    assert _agent_card_rpc_url(_Card()) == "https://peer.example/a2a"


def test_agent_card_rpc_url_appends_a2a_when_card_points_at_base():
    """Cards from CUGA's own builder advertise the base URL, not /a2a.
    The outbound client must still find the JSON-RPC endpoint."""
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.a2a_protocol import _agent_card_rpc_url

    class _Card:
        url = "https://peer.example"

    assert _agent_card_rpc_url(_Card()) == "https://peer.example/a2a"


def test_agent_card_rpc_url_extracts_from_supported_interfaces_proto_shape():
    """SDK 1.x's A2ACardResolver returns a *protobuf* AgentCard whose
    URL lives in ``supported_interfaces[0].url`` rather than ``.url``.
    This is how every real outbound delegation gets its endpoint, so
    if this test breaks, every cross-process A2A call breaks with it."""
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.a2a_protocol import _agent_card_rpc_url

    class _Iface:
        url = "http://peer.example:8002"
        protocol_binding = "JSONRPC"
        protocol_version = "0.3.0"

    class _ProtoCard:
        url = ""  # absent on protobuf
        supported_interfaces = (_Iface(),)

    assert _agent_card_rpc_url(_ProtoCard()) == "http://peer.example:8002/a2a"


def test_agent_card_rpc_url_prefers_jsonrpc_interface_when_multiple_present():
    """When a peer advertises both gRPC and JSONRPC interfaces, we must
    pick JSONRPC — that's the wire we actually speak."""
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.a2a_protocol import _agent_card_rpc_url

    class _Grpc:
        url = "grpc://peer.example:50051"
        protocol_binding = "GRPC"

    class _Json:
        url = "https://peer.example/a2a"
        protocol_binding = "JSONRPC"

    class _Card:
        url = ""
        supported_interfaces = (_Grpc(), _Json())

    assert _agent_card_rpc_url(_Card()) == "https://peer.example/a2a"


def test_agent_card_rpc_url_uses_fallback_when_card_has_no_url():
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.a2a_protocol import _agent_card_rpc_url

    class _Card:
        url = None

    assert _agent_card_rpc_url(_Card(), fallback_base="http://fallback") == "http://fallback/a2a"


async def test_delegate_round_trips_against_real_router(app_with_a2a, scripted_runner):
    """The strongest check: drive the rewritten outbound client at our
    inbound router and assert that the user message reaches the runner
    AND the answer comes back. If either half breaks, this test fails."""
    from cuga.backend.cuga_graph.nodes.cuga_supervisor.a2a_protocol import (
        delegate_task_via_a2a_sdk,
    )

    transport = httpx.ASGITransport(app=app_with_a2a)

    # Patch the module-level httpx.AsyncClient constructor so the outbound
    # client transparently routes through the in-process app — no port bind.
    real_async_client = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    class _Card:
        url = "http://test.local"

    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_supervisor.a2a_protocol.httpx.AsyncClient",
        side_effect=_factory,
    ):
        result = await delegate_task_via_a2a_sdk(_Card(), "please count to three")

    # Server received the user's text verbatim — proves the wire roundtrips.
    assert any("please count to three" in msg for (msg, _ctx) in scripted_runner.received)
    # And the response shape is what callers downstream expect.
    assert isinstance(result, dict)
    assert result["status"] == "success"
    # The scripted runner replies with "the answer is 42" for final events,
    # which our adapter wraps into the task's last agent message.
    assert "42" in result["result"]


async def test_delegate_surfaces_jsonrpc_error_as_runtime_error():
    """If the peer returns a JSON-RPC error envelope, the helper must
    raise — silent failure here lets bad delegations look like empty
    successes upstream. We stand up a tiny FastAPI app that always
    answers with a JSON-RPC error so the helper hits the error branch
    deterministically."""
    from fastapi import FastAPI

    from cuga.backend.cuga_graph.nodes.cuga_supervisor.a2a_protocol import (
        delegate_task_via_a2a_sdk,
    )

    error_app = FastAPI()

    @error_app.post("/a2a")
    async def _always_error():
        return {
            "jsonrpc": "2.0",
            "id": "x",
            "error": {"code": -32603, "message": "boom"},
        }

    transport = httpx.ASGITransport(app=error_app)
    real_async_client = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    class _Card:
        url = "http://test.local"

    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_supervisor.a2a_protocol.httpx.AsyncClient",
        side_effect=_factory,
    ):
        with pytest.raises(RuntimeError, match="A2A send_message failed"):
            await delegate_task_via_a2a_sdk(_Card(), "anything")


async def test_delegate_surfaces_peer_task_failure_as_failed_status():
    """When the peer answers 200 with a Task whose state=failed, the
    helper must return status='failed' instead of the prior bug where
    every shaped 200 was flattened to status='success'."""
    from fastapi import FastAPI

    from cuga.backend.cuga_graph.nodes.cuga_supervisor.a2a_protocol import (
        delegate_task_via_a2a_sdk,
    )

    failing_app = FastAPI()

    @failing_app.post("/a2a")
    async def _peer_task_failed():
        # Shape mirrors what our own router emits when a graph errors.
        return {
            "jsonrpc": "2.0",
            "id": "x",
            "result": {
                "id": "task-1",
                "contextId": "ctx-1",
                "status": {"state": "failed"},
                "history": [
                    {
                        "role": "agent",
                        "parts": [{"kind": "text", "text": "remote LLM 401"}],
                        "messageId": "m-final",
                    }
                ],
            },
        }

    transport = httpx.ASGITransport(app=failing_app)
    real_async_client = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    class _Card:
        url = "http://test.local"

    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_supervisor.a2a_protocol.httpx.AsyncClient",
        side_effect=_factory,
    ):
        result = await delegate_task_via_a2a_sdk(_Card(), "anything")

    assert result["status"] == "failed"
    assert "remote LLM 401" in result["result"]


async def test_delegate_raises_when_response_lacks_task_envelope():
    """If the peer answers 200 with neither error nor a dict result, the
    earlier code returned status='success' with empty text — making the
    failure indistinguishable from a successful empty answer. The helper
    must raise instead so callers can route the failure."""
    from fastapi import FastAPI

    from cuga.backend.cuga_graph.nodes.cuga_supervisor.a2a_protocol import (
        delegate_task_via_a2a_sdk,
    )

    bogus_app = FastAPI()

    @bogus_app.post("/a2a")
    async def _bogus():
        return {"jsonrpc": "2.0", "id": "x", "result": "not-a-task-object"}

    transport = httpx.ASGITransport(app=bogus_app)
    real_async_client = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    class _Card:
        url = "http://test.local"

    with patch(
        "cuga.backend.cuga_graph.nodes.cuga_supervisor.a2a_protocol.httpx.AsyncClient",
        side_effect=_factory,
    ):
        with pytest.raises(RuntimeError, match="missing a valid"):
            await delegate_task_via_a2a_sdk(_Card(), "anything")
