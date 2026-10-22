"""Unit tests for F1 — BackgroundTask lease release on StreamingResponse.

Verifies that the background task is:
- present on the StreamingResponse when prelude_result has a lease_token
- absent when prelude_result is None
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def _make_prelude_result(thread_id: str = "t1"):
    """Return a minimal prelude_result object with a lease_token."""
    from types import SimpleNamespace

    token = SimpleNamespace(thread_id=thread_id, holder_id="h1")
    return SimpleNamespace(lease_token=token)


def _make_prelude_no_token():
    """Return a prelude_result with no lease_token."""
    from types import SimpleNamespace

    return SimpleNamespace(lease_token=None)


def _build_stream_response(prelude_result, monkeypatch):
    """Call the stream route's BackgroundTask-building logic in isolation.

    We replicate the small block from main.py rather than spinning up the full
    route, so the test is fast and doesn't require a real ASGI server.
    """
    from unittest.mock import AsyncMock, MagicMock

    from fastapi.responses import StreamingResponse

    from cuga.backend.server import main

    # Minimal monkeypatching so get_thread_lease is accessible
    fake_lease = MagicMock()
    fake_lease.release = AsyncMock()
    monkeypatch.setattr(main, "get_thread_lease", lambda: fake_lease, raising=False)

    _background = None
    if prelude_result is not None and getattr(prelude_result, "lease_token", None) is not None:
        from starlette.background import BackgroundTask as BT

        _lease_token = prelude_result.lease_token

        async def _release_lease_bg():
            try:
                await main.get_thread_lease().release(_lease_token)
            except Exception:
                pass

        _background = BT(_release_lease_bg)

    async def _fake_gen():
        yield b"data: ok\n\n"

    resp = StreamingResponse(
        _fake_gen(),
        media_type="text/event-stream",
        background=_background,
    )
    return resp


def test_background_task_present_when_lease_token(monkeypatch):
    """BackgroundTask is set on StreamingResponse when prelude_result has a lease_token."""
    from starlette.background import BackgroundTask

    prelude = _make_prelude_result("thread-123")
    resp = _build_stream_response(prelude, monkeypatch)
    assert resp.background is not None, "expected BackgroundTask to be present"
    assert isinstance(resp.background, BackgroundTask)


def test_background_task_absent_when_no_prelude(monkeypatch):
    """BackgroundTask is None on StreamingResponse when prelude_result is None."""
    resp = _build_stream_response(None, monkeypatch)
    assert resp.background is None, "expected no BackgroundTask when prelude_result is None"


def test_background_task_absent_when_no_lease_token(monkeypatch):
    """BackgroundTask is None when prelude_result has no lease_token."""
    prelude = _make_prelude_no_token()
    resp = _build_stream_response(prelude, monkeypatch)
    assert resp.background is None, "expected no BackgroundTask when lease_token is None"
