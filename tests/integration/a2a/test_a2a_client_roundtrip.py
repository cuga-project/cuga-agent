"""Roundtrip tests using the real a2a-sdk against our router.

These are the strongest contract tests in the suite: if the SDK that real
A2A clients use can talk to our server end-to-end, we know the protocol
is correct beyond what hand-rolled JSON assertions can verify.

The client speaks to the in-process FastAPI app via httpx.ASGITransport,
so no port is bound and no LLM is called.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

a2a_client = pytest.importorskip("a2a.client")
a2a_types = pytest.importorskip("a2a.compat.v0_3.types")
httpx = pytest.importorskip("httpx")


pytestmark = pytest.mark.anyio


@pytest.fixture
async def asgi_httpx(app_with_a2a):
    transport = httpx.ASGITransport(app=app_with_a2a)
    async with httpx.AsyncClient(transport=transport, base_url="http://test.local") as client:
        yield client


async def test_sdk_card_resolver_fetches_agent_card(asgi_httpx):
    """The SDK's official resolver must accept what we serve at the
    well-known path. If this passes, our AgentCard is wire-compliant."""
    from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH

    resolver = a2a_client.A2ACardResolver(
        httpx_client=asgi_httpx,
        base_url="http://test.local",
        agent_card_path=AGENT_CARD_WELL_KNOWN_PATH,
    )
    card = await resolver.get_agent_card()
    assert card is not None
    assert card.name == "cuga-test"
    assert card.capabilities.streaming is True


async def test_jsonrpc_send_message_returns_task(asgi_httpx):
    """A JSON-RPC ``message/send`` against our router must return a Task
    that round-trips through the SDK's pydantic model. The task ID and
    completed terminal status are what real clients key off of."""
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid4()),
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": "hello CUGA"}],
                "messageId": uuid4().hex,
            }
        },
    }
    resp = await asgi_httpx.post("/a2a", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("jsonrpc") == "2.0"
    assert body.get("error") is None
    result = body["result"]
    # Round-trip through SDK pydantic Task — strongest cheap check.
    task = a2a_types.Task.model_validate(result)
    assert task.id
    assert task.status.state == a2a_types.TaskState.completed
