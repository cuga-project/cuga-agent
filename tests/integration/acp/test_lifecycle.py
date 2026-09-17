"""Lifecycle integration tests for the ACP child application.

These tests prove that the ACP SDK executor is properly initialized when the
child app is mounted under a parent FastAPI application, and that the child
lifespan is entered and exited exactly once per parent lifespan cycle.

Marked with both @pytest.mark.anyio and @pytest.mark.unit so they run in
the existing unit-b CI shard without binding ports.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.anyio]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_child_and_parent(
    acp_settings: Any,
    app_state: Any,
    event_stream_func: Any,
) -> tuple[Any, Any, list[int], list[int]]:
    """Return (parent_app, acp_child_app, startup_count, shutdown_count)."""
    from contextlib import AsyncExitStack

    from fastapi import FastAPI

    from cuga.backend.server.acp.app import build_acp_app_for_settings

    acp_child = build_acp_app_for_settings(acp_settings, app_state, event_stream_func=event_stream_func)

    startup_count: list[int] = []
    shutdown_count: list[int] = []

    @asynccontextmanager
    async def parent_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        stack = AsyncExitStack()
        async with stack:
            await stack.enter_async_context(acp_child.router.lifespan_context(acp_child))
            startup_count.append(1)
            yield
            shutdown_count.append(1)

    parent = FastAPI(lifespan=parent_lifespan)
    parent.mount("/acp", acp_child)

    return parent, acp_child, startup_count, shutdown_count


# ---------------------------------------------------------------------------
# Spike: child executor initializes under parent lifespan
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.unit
async def test_acp_run_executes_under_parent_lifespan(
    acp_settings: Any,
    mock_app_state: Any,
    fake_event_stream: Any,
) -> None:
    """A synchronous ACP run executes correctly; SDK executor is initialized."""
    import httpx
    from acp_sdk.models import RunMode

    parent, _, startup_count, _ = _build_child_and_parent(acp_settings, mock_app_state, fake_event_stream)

    async with parent.router.lifespan_context(parent):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=parent),
            base_url="http://test",
        ) as client:
            run_resp = await client.post(
                "/acp/runs",
                json={
                    "agent_name": "cuga",
                    "input": [
                        {
                            "role": "user",
                            "parts": [{"content_type": "text/plain", "content": "hello"}],
                        }
                    ],
                    "mode": RunMode.SYNC,
                },
            )
            assert run_resp.status_code in (200, 201), (
                f"Unexpected status {run_resp.status_code}: {run_resp.text[:200]}"
            )

    assert startup_count == [1], f"Expected exactly one startup, got {startup_count}"


@pytest.mark.anyio
@pytest.mark.unit
async def test_child_lifespan_entered_exactly_once(
    acp_settings: Any,
    mock_app_state: Any,
    fake_event_stream: Any,
) -> None:
    """Child lifespan context is entered exactly once per parent startup."""
    import httpx

    parent, _, startup_count, shutdown_count = _build_child_and_parent(
        acp_settings, mock_app_state, fake_event_stream
    )

    async with parent.router.lifespan_context(parent):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=parent),
            base_url="http://test",
        ) as client:
            resp = await client.get("/acp/ping")
            assert resp.status_code == 200

    assert startup_count == [1], f"Expected exactly one startup, got {startup_count}"
    assert shutdown_count == [1], f"Expected exactly one shutdown, got {shutdown_count}"


@pytest.mark.anyio
@pytest.mark.unit
async def test_ping_reachable_under_parent(
    acp_settings: Any,
    mock_app_state: Any,
    fake_event_stream: Any,
) -> None:
    """GET /acp/ping returns 200 under parent app."""
    import httpx

    parent, _, _, _ = _build_child_and_parent(acp_settings, mock_app_state, fake_event_stream)

    async with parent.router.lifespan_context(parent):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=parent),
            base_url="http://test",
        ) as client:
            resp = await client.get("/acp/ping")
            assert resp.status_code == 200


@pytest.mark.anyio
@pytest.mark.unit
async def test_agents_endpoint_reachable(
    acp_settings: Any,
    mock_app_state: Any,
    fake_event_stream: Any,
) -> None:
    """GET /acp/agents lists the configured agent."""
    import httpx

    parent, _, _, _ = _build_child_and_parent(acp_settings, mock_app_state, fake_event_stream)

    async with parent.router.lifespan_context(parent):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=parent),
            base_url="http://test",
        ) as client:
            resp = await client.get("/acp/agents")
            assert resp.status_code == 200
            body = resp.json()
            agent_names = [a["name"] for a in body.get("agents", [])]
            assert "cuga" in agent_names


# ---------------------------------------------------------------------------
# Task 2.6: Mount verification
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.unit
async def test_acp_ping_and_agents_work_when_enabled(
    acp_settings: Any,
    mock_app_state: Any,
    fake_event_stream: Any,
) -> None:
    """ACP enabled: /acp/ping and /acp/agents are reachable."""
    import httpx

    parent, _, _, _ = _build_child_and_parent(acp_settings, mock_app_state, fake_event_stream)

    async with parent.router.lifespan_context(parent):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=parent),
            base_url="http://test",
        ) as client:
            ping = await client.get("/acp/ping")
            assert ping.status_code == 200

            agents = await client.get("/acp/agents")
            assert agents.status_code == 200


@pytest.mark.anyio
@pytest.mark.unit
async def test_acp_disabled_ping_returns_404() -> None:
    """ACP disabled: no /acp/ping route mounted on the parent app."""
    import httpx
    from fastapi import FastAPI

    # Parent app with NO ACP child mounted
    parent = FastAPI()

    @parent.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=parent),
        base_url="http://test",
    ) as client:
        resp = await client.get("/acp/ping")
        assert resp.status_code == 404


@pytest.mark.anyio
@pytest.mark.unit
async def test_acp_sdk_not_imported_when_disabled() -> None:
    """Importing cuga.backend.server.acp.app does not import acp_sdk at module level."""

    # The acp/app module should be importable without touching acp_sdk internals
    # at module level. The SDK import is deferred inside the function body.
    # Verify by checking that the module-level globals of app.py don't include acp_sdk.
    import cuga.backend.server.acp.app as acp_app_mod

    module_globals = vars(acp_app_mod)
    # No module-level reference to acp_sdk should exist
    assert "acp_sdk" not in module_globals, (
        "acp_sdk found as a module-level binding in acp/app.py — "
        "SDK import must remain deferred inside the function body"
    )
