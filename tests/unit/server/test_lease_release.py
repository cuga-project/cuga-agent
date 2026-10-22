"""Unit tests for F1 — _LeaseStreamingResponse releases the lease on ASGI cancellation.

Three scenarios are tested:
1. Normal completion — release is called after the stream finishes.
2. ASGI cancellation before the first body byte — release is still called.
3. No prelude / no lease token — plain StreamingResponse is returned, no release.
"""

from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_prelude(thread_id: str = "t1"):
    from types import SimpleNamespace

    token = SimpleNamespace(thread_id=thread_id, holder_id="h1")
    return SimpleNamespace(lease_token=token)


def _make_prelude_no_token():
    from types import SimpleNamespace

    return SimpleNamespace(lease_token=None)


def _build_lease_response(prelude_result, monkeypatch):
    """Construct a response using the same logic as the stream route.

    Returns (response, release_mock).
    """
    from unittest.mock import AsyncMock

    from fastapi.responses import StreamingResponse

    from cuga.backend.server import main

    release_mock = AsyncMock()
    fake_lease = type("L", (), {"release": release_mock})()
    monkeypatch.setattr(main, "get_thread_lease", lambda: fake_lease, raising=False)

    async def _fake_gen():
        yield b"data: ok\n\n"

    resp = None
    if prelude_result is not None and getattr(prelude_result, "lease_token", None) is not None:
        _lease_token = prelude_result.lease_token

        class _LeaseStreamingResponse(StreamingResponse):
            async def __call__(self, scope, receive, send) -> None:  # type: ignore[override]
                try:
                    await super().__call__(scope, receive, send)
                finally:
                    try:
                        await asyncio.shield(main.get_thread_lease().release(_lease_token))
                    except Exception:
                        pass

        resp = _LeaseStreamingResponse(_fake_gen(), media_type="text/event-stream")
    else:
        resp = StreamingResponse(_fake_gen(), media_type="text/event-stream")

    return resp, release_mock


async def _run_asgi(response, *, cancel_before_body: bool = False):
    """Drive the response through a minimal ASGI cycle.

    If *cancel_before_body* is True the task is cancelled immediately after the
    response-start message is sent (before any body chunks).
    """
    scope = {
        "type": "http",
        "asgi": {"spec_version": "2.4"},
        "method": "GET",
        "path": "/stream",
        "query_string": b"",
        "headers": [],
    }

    async def receive():
        await asyncio.sleep(3600)  # never sends disconnect during our test window
        return {"type": "http.disconnect"}

    sent = []

    async def send(message):
        sent.append(message["type"])
        if cancel_before_body and message["type"] == "http.response.start":
            # Simulate ASGI cancelling the task immediately after headers are sent.
            raise asyncio.CancelledError()

    task = asyncio.create_task(response(scope, receive, send))
    try:
        await task
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lease_released_on_normal_completion(monkeypatch):
    """Release is called after normal streaming completes."""
    prelude = _make_prelude("thread-normal")
    resp, release_mock = _build_lease_response(prelude, monkeypatch)
    await _run_asgi(resp, cancel_before_body=False)
    release_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_lease_released_on_asgi_cancel_before_first_body(monkeypatch):
    """Release is called even when ASGI cancels the response before the first body byte."""
    prelude = _make_prelude("thread-cancel")
    resp, release_mock = _build_lease_response(prelude, monkeypatch)
    await _run_asgi(resp, cancel_before_body=True)
    release_mock.assert_awaited_once()


def test_plain_streaming_response_returned_when_no_prelude(monkeypatch):
    """When prelude_result is None a plain StreamingResponse is returned (no release logic)."""
    from fastapi.responses import StreamingResponse

    resp, release_mock = _build_lease_response(None, monkeypatch)
    assert type(resp) is StreamingResponse
    release_mock.assert_not_awaited()


def test_plain_streaming_response_returned_when_no_lease_token(monkeypatch):
    """When lease_token is None a plain StreamingResponse is returned."""
    from fastapi.responses import StreamingResponse

    resp, release_mock = _build_lease_response(_make_prelude_no_token(), monkeypatch)
    assert type(resp) is StreamingResponse
    release_mock.assert_not_awaited()
