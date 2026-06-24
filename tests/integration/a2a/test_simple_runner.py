"""Integration tests for the SimpleA2ARunner.

These tests verify that the SimpleA2ARunner correctly processes messages
through CUGA's event stream and returns proper A2A responses without
requiring supervisor configuration.
"""

from __future__ import annotations

import json
from typing import AsyncIterator

import pytest

pytestmark = pytest.mark.anyio


def _task_text(result: dict) -> str:
    """Concatenate all message text in a Task result (history + status message)."""
    parts = []
    for msg in result.get("history") or []:
        for part in msg.get("parts") or []:
            if isinstance(part.get("text"), str):
                parts.append(part["text"])
    status_msg = (result.get("status") or {}).get("message") or {}
    for part in status_msg.get("parts") or []:
        if isinstance(part.get("text"), str):
            parts.append(part["text"])
    return " ".join(parts)


@pytest.fixture
def mock_event_stream():
    """Mock event_stream function that simulates CUGA's event stream."""

    async def _event_stream(
        query: str,
        api_mode: bool = False,
        thread_id: str | None = None,
        agent=None,
        disable_history: bool = False,
        user_id: str = "test_user",
        user_attachments=None,
        resume=None,
    ) -> AsyncIterator[bytes]:
        """Simulate CUGA's real SSE wire format (``event: <name>\\ndata: ...``)."""
        # Simulate intermediate progress events (named frames are captured).
        yield b"event: AgentThinking\ndata: Processing request...\n\n"
        yield b"event: tool_call\ndata: calculator(2+2)\n\n"

        # Final answer mirrors event_stream's non-WXO envelope.
        answer = f"The answer to '{query}' is 4"
        payload = json.dumps({"data": answer, "variables": {}, "active_policies": []})
        yield f"event: Answer\ndata: {payload}\n\n".encode()

    return _event_stream


@pytest.fixture
def mock_app_state():
    """Mock app_state with minimal required attributes."""

    class MockAppState:
        def __init__(self):
            self.agent = "mock_agent"
            self.output_format = None

    return MockAppState()


@pytest.fixture
def simple_runner_app(mock_app_state, mock_event_stream):
    """Build a FastAPI app with SimpleA2ARunner mounted."""
    pytest.importorskip("cuga.backend.server.a2a")
    from fastapi import FastAPI
    from cuga.backend.server.a2a.runner import build_a2a_router_for_settings

    app = FastAPI()

    # Settings that trigger SimpleA2ARunner (no supervisor_config_path)
    a2a_settings = {
        "enabled": True,
        "agent_name": "cuga-simple",
        "agent_description": "CUGA with simple runner",
        "agent_version": "0.0.0-test",
        "agent_url": "http://test.local",
        "skill_ids": ["delegate_task"],
        "supervisor_config_path": "",  # Empty to trigger SimpleA2ARunner
    }

    # Build router with event_stream function
    router = build_a2a_router_for_settings(a2a_settings, mock_app_state, event_stream_func=mock_event_stream)
    app.include_router(router)
    return app


@pytest.fixture
async def simple_runner_client(simple_runner_app):
    """An httpx.AsyncClient bound to the app with SimpleA2ARunner."""
    httpx = pytest.importorskip("httpx")
    transport = httpx.ASGITransport(app=simple_runner_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test.local") as client:
        yield client


async def test_simple_runner_returns_actual_response(simple_runner_client):
    """SimpleA2ARunner should return actual agent responses, not placeholder."""
    payload = {
        "jsonrpc": "2.0",
        "id": "test-1",
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": "What is 2+2?"}],
                "messageId": "m-1",
            }
        },
    }

    resp = await simple_runner_client.post("/a2a", json=payload)
    assert resp.status_code == 200

    body = resp.json()
    assert body.get("jsonrpc") == "2.0"
    assert body.get("id") == "test-1"
    assert "result" in body

    result = body["result"]
    assert result["status"]["state"] == "completed"

    result_text = _task_text(result)
    # Should NOT be the placeholder message
    assert "supervisor_config_path is not set" not in result_text
    # Should contain actual response
    assert "2+2" in result_text or "4" in result_text


async def test_simple_runner_maintains_context(simple_runner_client):
    """SimpleA2ARunner should pass context_id to event_stream for conversation continuity."""
    context_id = "ctx-test-123"

    payload = {
        "jsonrpc": "2.0",
        "id": "test-1",
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": "Hello"}],
                "messageId": "m-1",
                "contextId": context_id,
            }
        },
    }

    resp = await simple_runner_client.post("/a2a", json=payload)
    assert resp.status_code == 200

    body = resp.json()
    # Context ID should be preserved in response
    assert body["result"]["contextId"] == context_id


async def test_simple_runner_streaming_support(simple_runner_client):
    """SimpleA2ARunner should support streaming via message/stream."""
    payload = {
        "jsonrpc": "2.0",
        "id": "stream-1",
        "method": "message/stream",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": "Tell me something"}],
                "messageId": "m-1",
            }
        },
    }

    chunks = []
    async with simple_runner_client.stream("POST", "/a2a", json=payload) as resp:
        assert resp.status_code == 200
        async for line in resp.aiter_lines():
            line = line.strip()
            if not line or not line.startswith("data:"):
                continue
            try:
                data = json.loads(line[5:].strip())
                chunks.append(data)
            except json.JSONDecodeError:
                continue

    # Should have received multiple events
    assert len(chunks) > 0

    # Each chunk is a TaskStatusUpdateEvent with status.state.
    states = [c.get("result", {}).get("status", {}).get("state") for c in chunks if "result" in c]

    # Should have streamed in-flight progress before completing.
    assert "working" in states
    # Final chunk should be completed.
    assert "completed" in states


async def test_simple_runner_error_handling(mock_app_state):
    """SimpleA2ARunner should handle errors gracefully."""
    pytest.importorskip("cuga.backend.server.a2a")
    from fastapi import FastAPI
    from cuga.backend.server.a2a.runner import build_a2a_router_for_settings

    # Event stream that raises an error
    async def failing_event_stream(*args, **kwargs):
        raise RuntimeError("Simulated failure")
        yield  # pragma: no cover

    app = FastAPI()
    a2a_settings = {
        "enabled": True,
        "agent_name": "cuga-simple",
        "agent_description": "CUGA with simple runner",
        "agent_version": "0.0.0-test",
        "agent_url": "http://test.local",
        "skill_ids": ["delegate_task"],
        "supervisor_config_path": "",
    }

    router = build_a2a_router_for_settings(
        a2a_settings, mock_app_state, event_stream_func=failing_event_stream
    )
    app.include_router(router)

    httpx = pytest.importorskip("httpx")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test.local") as client:
        payload = {
            "jsonrpc": "2.0",
            "id": "error-test",
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"kind": "text", "text": "Test"}],
                    "messageId": "m-1",
                }
            },
        }

        resp = await client.post("/a2a", json=payload)
        assert resp.status_code == 200

        body = resp.json()
        # Should return error in JSON-RPC format, not HTTP 500
        assert "result" in body
        result = body["result"]
        # Error should surface as a failed task carrying the exception class.
        assert result["status"]["state"] == "failed"
        assert "RuntimeError" in _task_text(result)


async def test_simple_runner_vs_placeholder():
    """Verify SimpleA2ARunner is used when event_stream is provided, not placeholder."""
    pytest.importorskip("cuga.backend.server.a2a")
    from fastapi import FastAPI
    from cuga.backend.server.a2a.runner import build_a2a_router_for_settings

    class MockAppState:
        agent = "mock"

    async def mock_stream(*args, **kwargs):
        payload = json.dumps({"data": "Real response", "variables": {}, "active_policies": []})
        yield f"event: Answer\ndata: {payload}\n\n".encode()

    # With event_stream: should use SimpleA2ARunner
    app_with_stream = FastAPI()
    router_with_stream = build_a2a_router_for_settings(
        {
            "enabled": True,
            "agent_name": "test",
            "agent_description": "test",
            "agent_version": "0.0.0",
            "agent_url": "http://test",
            "skill_ids": [],
            "supervisor_config_path": "",
        },
        MockAppState(),
        event_stream_func=mock_stream,
    )
    app_with_stream.include_router(router_with_stream)

    # Without event_stream: should use PlaceholderA2ARunner
    app_without_stream = FastAPI()
    router_without_stream = build_a2a_router_for_settings(
        {
            "enabled": True,
            "agent_name": "test",
            "agent_description": "test",
            "agent_version": "0.0.0",
            "agent_url": "http://test",
            "skill_ids": [],
            "supervisor_config_path": "",
        },
        MockAppState(),
        event_stream_func=None,
    )
    app_without_stream.include_router(router_without_stream)

    httpx = pytest.importorskip("httpx")

    # Test with SimpleA2ARunner
    transport_with = httpx.ASGITransport(app=app_with_stream)
    async with httpx.AsyncClient(transport=transport_with, base_url="http://test") as client:
        resp = await client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "1",
                "method": "message/send",
                "params": {
                    "message": {"role": "user", "parts": [{"kind": "text", "text": "Hi"}], "messageId": "m1"}
                },
            },
        )
        body_with = resp.json()

    # Test with PlaceholderA2ARunner
    transport_without = httpx.ASGITransport(app=app_without_stream)
    async with httpx.AsyncClient(transport=transport_without, base_url="http://test") as client:
        resp = await client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": "1",
                "method": "message/send",
                "params": {
                    "message": {"role": "user", "parts": [{"kind": "text", "text": "Hi"}], "messageId": "m1"}
                },
            },
        )
        body_without = resp.json()

    # SimpleA2ARunner should return real response
    assert "Real response" in str(body_with)

    # PlaceholderA2ARunner should return config message
    assert "supervisor_config_path is not set" in str(body_without)


# Made with Bob
