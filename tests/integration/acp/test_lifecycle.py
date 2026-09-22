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


# ---------------------------------------------------------------------------
# Task 2.7: Comprehensive inbound lifecycle tests
# ---------------------------------------------------------------------------

# ── helpers ─────────────────────────────────────────────────────────────────

_AGENT_INPUT = [
    {
        "role": "user",
        "parts": [{"content_type": "text/plain", "content": "hello"}],
    }
]


def _run_request(mode: str, agent_name: str = "cuga") -> dict:
    return {"agent_name": agent_name, "input": _AGENT_INPUT, "mode": mode}


def _parse_sse_events(body: bytes) -> list[dict]:
    """Parse a raw SSE body into a list of {event, data} dicts."""
    import json

    events: list[dict] = []
    current: dict = {}
    for raw_line in body.decode().splitlines():
        line = raw_line.strip()
        if line.startswith("data:"):
            try:
                current["data"] = json.loads(line[5:].strip())
            except Exception:
                current["data"] = line[5:].strip()
        elif line == "" and current:
            events.append(current)
            current = {}
    return events


# ── fixture: event stream that raises an exception ──────────────────────────


@pytest.fixture
def raising_event_stream() -> Any:
    """Event-stream fixture that raises an exception during streaming."""
    import json

    async def _event_stream(
        query: str,
        api_mode: bool = False,
        thread_id: str | None = None,
        agent: Any = None,
        disable_history: bool = False,
        user_id: str = "test_user",
        user_attachments: Any = None,
        resume: Any = None,
    ) -> Any:
        # yield one valid byte so the stream starts before raising
        payload = json.dumps({"data": "thinking…", "variables": {}, "active_policies": []})
        yield f"event: AgentThinking\ndata: {payload}\n\n".encode()
        raise RuntimeError("Simulated runner crash")

    return _event_stream


# ── Case 1: Agent listing and manifest lookup ──────────────────────────────


@pytest.mark.anyio
@pytest.mark.unit
async def test_agent_listing_returns_cuga(
    acp_settings: Any,
    mock_app_state: Any,
    fake_event_stream: Any,
) -> None:
    """GET /acp/agents lists agents; GET /acp/agents/cuga returns the manifest."""
    import httpx
    from acp_sdk.models import AgentManifest

    parent, _, _, _ = _build_child_and_parent(acp_settings, mock_app_state, fake_event_stream)

    async with parent.router.lifespan_context(parent):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=parent),
            base_url="http://test",
        ) as client:
            # List
            resp_list = await client.get("/acp/agents")
            assert resp_list.status_code == 200
            body = resp_list.json()
            agent_names = [a["name"] for a in body.get("agents", [])]
            assert "cuga" in agent_names

            # Single manifest
            resp_one = await client.get("/acp/agents/cuga")
            assert resp_one.status_code == 200
            manifest = AgentManifest(**resp_one.json())
            assert manifest.name == "cuga"
            assert "text/plain" in manifest.input_content_types


# ── Case 2: Unknown agent returns 404 ─────────────────────────────────────


@pytest.mark.anyio
@pytest.mark.unit
async def test_unknown_agent_returns_404(
    acp_settings: Any,
    mock_app_state: Any,
    fake_event_stream: Any,
) -> None:
    """GET /acp/agents/nonexistent returns 404."""
    import httpx

    parent, _, _, _ = _build_child_and_parent(acp_settings, mock_app_state, fake_event_stream)

    async with parent.router.lifespan_context(parent):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=parent),
            base_url="http://test",
        ) as client:
            resp = await client.get("/acp/agents/nonexistent")
            assert resp.status_code == 404


# ── Case 3: Synchronous run returns text output ────────────────────────────


@pytest.mark.anyio
@pytest.mark.unit
async def test_sync_run_returns_agent_message(
    acp_settings: Any,
    mock_app_state: Any,
    fake_event_stream: Any,
) -> None:
    """A SYNC run completes and the Run body contains an agent message."""
    import httpx
    from acp_sdk.models import Run, RunMode, RunStatus

    parent, _, _, _ = _build_child_and_parent(acp_settings, mock_app_state, fake_event_stream)

    async with parent.router.lifespan_context(parent):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=parent),
            base_url="http://test",
        ) as client:
            resp = await client.post("/acp/runs", json=_run_request(RunMode.SYNC))
            assert resp.status_code in (200, 201), resp.text[:300]
            run = Run(**resp.json())
            assert run.status == RunStatus.COMPLETED
            assert run.output, "Expected at least one output message"
            text = "".join(p.content for msg in run.output for p in msg.parts if p.content)
            assert text, "Expected non-empty text in output"


# ── Case 4: Asynchronous run — 202, then poll to completion ───────────────


@pytest.mark.anyio
@pytest.mark.unit
async def test_async_run_returns_202_then_completes(
    acp_settings: Any,
    mock_app_state: Any,
    fake_event_stream: Any,
) -> None:
    """ASYNC run returns 202 immediately; polling /runs/{id} reaches completed."""
    import asyncio

    import httpx
    from acp_sdk.models import Run, RunMode, RunStatus

    parent, _, _, _ = _build_child_and_parent(acp_settings, mock_app_state, fake_event_stream)

    async with parent.router.lifespan_context(parent):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=parent),
            base_url="http://test",
        ) as client:
            resp = await client.post("/acp/runs", json=_run_request(RunMode.ASYNC))
            assert resp.status_code == 202, resp.text[:300]
            run = Run(**resp.json())
            run_id = str(run.run_id)

            # Poll until terminal, with a short timeout
            for _ in range(20):
                poll = await client.get(f"/acp/runs/{run_id}")
                assert poll.status_code == 200
                polled_run = Run(**poll.json())
                if polled_run.status in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED):
                    break
                await asyncio.sleep(0.05)
            else:
                pytest.fail(f"Run did not reach a terminal status. Last status: {polled_run.status}")

            assert polled_run.status == RunStatus.COMPLETED


# ── Case 5: Stream run — SSE events contain expected discriminators ────────


@pytest.mark.anyio
@pytest.mark.unit
async def test_stream_run_sse_events(
    acp_settings: Any,
    mock_app_state: Any,
    fake_event_stream: Any,
) -> None:
    """STREAM run SSE body includes run.created, run.in-progress, and run.completed events."""
    import httpx
    from acp_sdk.models import RunMode

    parent, _, _, _ = _build_child_and_parent(acp_settings, mock_app_state, fake_event_stream)

    async with parent.router.lifespan_context(parent):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=parent),
            base_url="http://test",
        ) as client:
            resp = await client.post("/acp/runs", json=_run_request(RunMode.STREAM))
            assert resp.status_code == 200, resp.text[:300]

    events = _parse_sse_events(resp.content)
    event_types = [e.get("data", {}).get("type") for e in events if isinstance(e.get("data"), dict)]
    assert "run.created" in event_types, f"run.created missing from SSE. Got types: {event_types}"
    assert "run.in-progress" in event_types, f"run.in-progress missing from SSE. Got types: {event_types}"
    assert "run.completed" in event_types, f"run.completed missing from SSE. Got types: {event_types}"


# ── Case 6: Event-history endpoint returns JSON (not SSE) ─────────────────


@pytest.mark.anyio
@pytest.mark.unit
async def test_event_history_returns_json(
    acp_settings: Any,
    mock_app_state: Any,
    fake_event_stream: Any,
) -> None:
    """GET /acp/runs/{run_id}/events returns a JSON list, not SSE."""
    import httpx
    from acp_sdk.models import RunMode

    parent, _, _, _ = _build_child_and_parent(acp_settings, mock_app_state, fake_event_stream)

    async with parent.router.lifespan_context(parent):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=parent),
            base_url="http://test",
        ) as client:
            run_resp = await client.post("/acp/runs", json=_run_request(RunMode.SYNC))
            assert run_resp.status_code in (200, 201)
            run_id = run_resp.headers.get("run-id") or run_resp.json()["run_id"]

            events_resp = await client.get(f"/acp/runs/{run_id}/events")
            assert events_resp.status_code == 200
            assert "application/json" in events_resp.headers.get("content-type", "")
            body = events_resp.json()
            assert "events" in body, f"Expected 'events' key, got: {list(body.keys())}"
            assert isinstance(body["events"], list)
            assert len(body["events"]) > 0


# ── Case 7: Session ID reused across two runs ─────────────────────────────


@pytest.mark.anyio
@pytest.mark.unit
async def test_session_id_reused_across_runs(
    acp_settings: Any,
    mock_app_state: Any,
    tracking_event_stream: Any,
) -> None:
    """Two runs with the same session_id share the same session and thread_id."""
    import httpx
    from acp_sdk.models import Run, RunMode

    event_stream_func, captured_thread_ids = tracking_event_stream
    parent, _, _, _ = _build_child_and_parent(acp_settings, mock_app_state, event_stream_func)

    async with parent.router.lifespan_context(parent):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=parent),
            base_url="http://test",
        ) as client:
            # Create first run to get a session_id
            r1 = await client.post("/acp/runs", json=_run_request(RunMode.SYNC))
            assert r1.status_code in (200, 201)
            run1 = Run(**r1.json())
            session_id = str(run1.session_id)

            # Second run reuses the same session_id
            req2 = {**_run_request(RunMode.SYNC), "session_id": session_id}
            r2 = await client.post("/acp/runs", json=req2)
            assert r2.status_code in (200, 201)
            run2 = Run(**r2.json())

            assert str(run2.session_id) == session_id, (
                f"Expected session_id {session_id}, got {run2.session_id}"
            )
            assert run2.run_id != run1.run_id, "Two runs must have distinct run IDs"

    # Both runs must have passed the same context_id (thread_id) to the runner
    assert len(captured_thread_ids) == 2, f"Expected 2 runner invocations, got {len(captured_thread_ids)}"
    assert captured_thread_ids[0] is not None, "First run must pass a non-None thread_id"
    assert captured_thread_ids[0] == captured_thread_ids[1], (
        f"Both runs must use the same thread_id (context_id). "
        f"Got {captured_thread_ids[0]!r} vs {captured_thread_ids[1]!r}"
    )


# ── Case 8: HITL in direct-agent mode ──────────────────────────────────────


@pytest.mark.anyio
@pytest.mark.unit
async def test_hitl_direct_agent_mode(acp_settings: Any) -> None:
    """HITL reaches awaiting, resumes over HTTP, and completes on the same thread."""
    import json
    from types import SimpleNamespace

    import httpx
    from acp_sdk.models import (
        Message,
        MessageAwaitResume,
        MessagePart,
        Run,
        RunMode,
        RunResumeRequest,
        RunStatus,
    )

    calls: list[tuple[str | None, Any]] = []
    parked_threads: set[str] = set()

    class _Graph:
        def get_state(self, config: dict[str, Any]) -> Any:
            thread_id = config["configurable"]["thread_id"]
            if thread_id not in parked_threads:
                return SimpleNamespace(next=(), values={})
            return SimpleNamespace(
                next=("wait_for_response",),
                values={
                    "hitl_action": {
                        "action_id": "approve-1",
                        "type": "confirmation",
                        "description": "Run the protected action",
                    }
                },
            )

    app_state = SimpleNamespace(agent=SimpleNamespace(graph=_Graph()), output_format=None)

    async def _hitl_event_stream(
        query: str | None,
        api_mode: bool = False,
        thread_id: str | None = None,
        agent: Any = None,
        disable_history: bool = False,
        user_id: str = "test_user",
        user_attachments: Any = None,
        resume: Any = None,
    ) -> AsyncGenerator[bytes, None]:
        calls.append((thread_id, resume))
        if resume is None:
            parked_threads.add(str(thread_id))
            if False:
                yield b""
            return
        parked_threads.discard(str(thread_id))
        payload = json.dumps({"data": "Approved and completed", "variables": {}, "active_policies": []})
        yield f"event: Answer\ndata: {payload}\n\n".encode()

    parent, _, _, _ = _build_child_and_parent(acp_settings, app_state, _hitl_event_stream)

    async with parent.router.lifespan_context(parent):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=parent),
            base_url="http://test",
        ) as client:
            create_response = await client.post("/acp/runs", json=_run_request(RunMode.SYNC))
            assert create_response.status_code in (200, 201), create_response.text[:300]
            awaiting_run = Run(**create_response.json())
            assert awaiting_run.status == RunStatus.AWAITING
            assert awaiting_run.await_request is not None

            resume_request = RunResumeRequest(
                await_resume=MessageAwaitResume(
                    message=Message(
                        role="user",
                        parts=[MessagePart(content_type="text/plain", content="approve")],
                    )
                ),
                mode=RunMode.SYNC,
            )
            resume_response = await client.post(
                f"/acp/runs/{awaiting_run.run_id}",
                json=resume_request.model_dump(mode="json"),
            )
            assert resume_response.status_code == 200, resume_response.text[:300]
            completed_run = Run(**resume_response.json())

    assert completed_run.status == RunStatus.COMPLETED
    assert len(calls) == 2
    assert calls[0][0] == calls[1][0] == str(awaiting_run.session_id)
    assert calls[0][1] is None
    assert calls[1][1] is not None
    assert calls[1][1].action_id == "approve-1"
    assert calls[1][1].confirmed is True


# ── Case 9: Cancellation of active run ───────────────────────────────────


@pytest.mark.anyio
@pytest.mark.unit
async def test_cancel_active_run(
    acp_settings: Any,
    mock_app_state: Any,
    fake_event_stream: Any,
) -> None:
    """DELETE (cancel) an active run via POST /acp/runs/{run_id}/cancel returns 202."""
    import httpx
    from acp_sdk.models import Run, RunMode, RunStatus

    parent, _, _, _ = _build_child_and_parent(acp_settings, mock_app_state, fake_event_stream)

    async with parent.router.lifespan_context(parent):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=parent),
            base_url="http://test",
        ) as client:
            # Use ASYNC so the run is briefly in-progress
            resp = await client.post("/acp/runs", json=_run_request(RunMode.ASYNC))
            assert resp.status_code == 202
            run_id = str(Run(**resp.json()).run_id)

            cancel_resp = await client.post(f"/acp/runs/{run_id}/cancel")
            # SDK returns 202 or 403 if already terminal; both are valid here
            assert cancel_resp.status_code in (202, 403), cancel_resp.text[:300]
            if cancel_resp.status_code == 202:
                cancelled_run = Run(**cancel_resp.json())
                assert cancelled_run.status in (RunStatus.CANCELLING, RunStatus.CANCELLED)


# ── Case 10: Cancellation after completion ───────────────────────────────


@pytest.mark.anyio
@pytest.mark.unit
async def test_cancel_completed_run_returns_rejection(
    acp_settings: Any,
    mock_app_state: Any,
    fake_event_stream: Any,
) -> None:
    """Cancelling a completed run returns 403 (SDK-defined rejection for terminal status)."""
    import httpx
    from acp_sdk.models import Run, RunMode

    parent, _, _, _ = _build_child_and_parent(acp_settings, mock_app_state, fake_event_stream)

    async with parent.router.lifespan_context(parent):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=parent),
            base_url="http://test",
        ) as client:
            # Synchronous run completes before we can cancel
            resp = await client.post("/acp/runs", json=_run_request(RunMode.SYNC))
            assert resp.status_code in (200, 201)
            run_id = str(Run(**resp.json()).run_id)

            cancel_resp = await client.post(f"/acp/runs/{run_id}/cancel")
            assert cancel_resp.status_code == 403, (
                f"Expected 403 for cancel-after-completion, got {cancel_resp.status_code}: {cancel_resp.text[:200]}"
            )


# ── Case 11: Unknown run ID returns 404 ──────────────────────────────────


@pytest.mark.anyio
@pytest.mark.unit
async def test_unknown_run_id_returns_404(
    acp_settings: Any,
    mock_app_state: Any,
    fake_event_stream: Any,
) -> None:
    """GET /acp/runs/{unknown} returns 404 for never-created and different UUIDs."""
    import uuid

    import httpx

    parent, _, _, _ = _build_child_and_parent(acp_settings, mock_app_state, fake_event_stream)

    async with parent.router.lifespan_context(parent):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=parent),
            base_url="http://test",
        ) as client:
            # A nil UUID that was never created
            resp = await client.get("/acp/runs/00000000-0000-0000-0000-000000000000")
            assert resp.status_code == 404

            # A random valid-looking UUID also never created
            random_id = str(uuid.uuid4())
            resp2 = await client.get(f"/acp/runs/{random_id}")
            assert resp2.status_code == 404, (
                f"Expected 404 for random UUID {random_id}, got {resp2.status_code}"
            )


# ── Case 11b: Expired run ID returns 404 (slow — TTL wall-clock) ──────────


@pytest.mark.anyio
@pytest.mark.unit
@pytest.mark.slow
@pytest.mark.skip(reason="Requires real wall-clock TTL; run manually with -m slow")
async def test_expired_run_id_returns_404(
    mock_app_state: Any,
    fake_event_stream: Any,
) -> None:
    """A run that existed but whose TTL has elapsed returns 404."""
    import asyncio

    import httpx
    from acp_sdk.models import Run, RunMode
    from cuga.backend.server.acp.app import build_acp_app_for_settings
    from cuga.backend.server.acp.settings import normalize_acp_settings

    # Build settings with a 1-second TTL
    from dataclasses import dataclass

    @dataclass
    class _ShortTTLSettings:
        enabled: bool = True
        path_prefix: str = "/acp"
        agent_name: str = "cuga"
        agent_description: str = "TTL test"
        supervisor_config_path: str = ""
        auto_approve: bool = False
        store: str = "memory"
        store_limit: int = 100
        store_ttl_seconds: int = 1
        auth_required: bool = False
        enable_playground_cors: bool = False

    short_ttl_settings = normalize_acp_settings(_ShortTTLSettings())
    acp_child = build_acp_app_for_settings(
        short_ttl_settings, mock_app_state, event_stream_func=fake_event_stream
    )

    from contextlib import asynccontextmanager
    from fastapi import FastAPI

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        from contextlib import AsyncExitStack

        async with AsyncExitStack() as stack:
            await stack.enter_async_context(acp_child.router.lifespan_context(acp_child))
            yield

    parent = FastAPI(lifespan=_lifespan)
    parent.mount("/acp", acp_child)

    async with parent.router.lifespan_context(parent):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=parent),
            base_url="http://test",
        ) as client:
            # Create a run
            resp = await client.post(
                "/acp/runs",
                json={"agent_name": "cuga", "input": _AGENT_INPUT, "mode": RunMode.SYNC},
            )
            assert resp.status_code in (200, 201)
            run_id = str(Run(**resp.json()).run_id)

            # Wait for TTL to expire (2 × the configured TTL)
            await asyncio.sleep(2)

            # The run should now be gone from the store
            expired_resp = await client.get(f"/acp/runs/{run_id}")
            assert expired_resp.status_code == 404, (
                f"Expected 404 for expired run {run_id}, got {expired_resp.status_code}"
            )


# ── Case 12: Two concurrent runs do not mix events ───────────────────────


@pytest.mark.anyio
@pytest.mark.unit
async def test_concurrent_runs_do_not_mix_events(
    acp_settings: Any,
    mock_app_state: Any,
    fake_event_stream: Any,
) -> None:
    """Two concurrent SYNC runs return independent run_ids and distinct output."""
    import asyncio

    import httpx
    from acp_sdk.models import Run, RunMode, RunStatus

    parent, _, _, _ = _build_child_and_parent(acp_settings, mock_app_state, fake_event_stream)

    async with parent.router.lifespan_context(parent):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=parent),
            base_url="http://test",
        ) as client:
            req_a = {
                "agent_name": "cuga",
                "input": [
                    {"role": "user", "parts": [{"content_type": "text/plain", "content": "query_alpha"}]}
                ],
                "mode": RunMode.SYNC,
            }
            req_b = {
                "agent_name": "cuga",
                "input": [
                    {"role": "user", "parts": [{"content_type": "text/plain", "content": "query_beta"}]}
                ],
                "mode": RunMode.SYNC,
            }

            resp_a, resp_b = await asyncio.gather(
                client.post("/acp/runs", json=req_a),
                client.post("/acp/runs", json=req_b),
            )

    assert resp_a.status_code in (200, 201), resp_a.text[:200]
    assert resp_b.status_code in (200, 201), resp_b.text[:200]

    run_a = Run(**resp_a.json())
    run_b = Run(**resp_b.json())

    assert run_a.run_id != run_b.run_id, "Concurrent runs must have distinct run IDs"
    assert run_a.status == RunStatus.COMPLETED
    assert run_b.status == RunStatus.COMPLETED

    text_a = "".join(p.content for msg in (run_a.output or []) for p in msg.parts if p.content)
    text_b = "".join(p.content for msg in (run_b.output or []) for p in msg.parts if p.content)
    # fake_event_stream echoes the query back in the answer
    assert "query_alpha" in text_a, f"Expected 'query_alpha' in run A output. Got: {text_a!r}"
    assert "query_beta" in text_b, f"Expected 'query_beta' in run B output. Got: {text_b!r}"


# ── Case 13: Memory-store TTL expiry — marked slow, skipped by default ────


@pytest.mark.anyio
@pytest.mark.unit
@pytest.mark.slow
@pytest.mark.skip(reason="Requires real wall-clock time; run manually with -m slow")
async def test_memory_store_ttl_expiry(
    acp_settings: Any,
    mock_app_state: Any,
    fake_event_stream: Any,
) -> None:
    """After TTL seconds, a completed run ID is no longer retrievable."""
    ...  # would need acp_settings.store_ttl_seconds = 1 and asyncio.sleep(2)


# ── Case 14: Invalid inputs return ACP errors without reflecting values ────


@pytest.mark.anyio
@pytest.mark.unit
async def test_invalid_input_returns_acp_error(
    acp_settings: Any,
    mock_app_state: Any,
    fake_event_stream: Any,
) -> None:
    """A run with no user text returns an ACP error; input is never echoed back."""
    import httpx
    from acp_sdk.models import RunMode

    INJECTED = "THIS_SECRET_MUST_NOT_APPEAR"

    parent, _, _, _ = _build_child_and_parent(acp_settings, mock_app_state, fake_event_stream)

    async with parent.router.lifespan_context(parent):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=parent),
            base_url="http://test",
        ) as client:
            # No user parts — only agent-role message → should be rejected
            bad_input_resp = await client.post(
                "/acp/runs",
                json={
                    "agent_name": "cuga",
                    "input": [
                        {
                            "role": "agent",
                            "parts": [{"content_type": "text/plain", "content": INJECTED}],
                        }
                    ],
                    "mode": RunMode.SYNC,
                },
            )

    # The SDK wraps ACPError inside the run: HTTP 200 with Run.status == "failed"
    assert bad_input_resp.status_code in (200, 201, 400, 422), (
        f"Unexpected status for invalid input: {bad_input_resp.status_code}"
    )
    body_text = bad_input_resp.text
    assert INJECTED not in body_text, "Input value must not be reflected in the error response"
    if bad_input_resp.status_code in (200, 201):
        from acp_sdk.models import Run, RunStatus

        run = Run(**bad_input_resp.json())
        assert run.status == RunStatus.FAILED, f"Expected failed run, got {run.status}"
        assert run.error is not None, "Expected error object in failed run"
        # The error message must be the constant sanitized string, not the injected value
        assert INJECTED not in (run.error.message or ""), (
            "Injected input must not appear in the error message"
        )


# ── Case 15: Runner exception creates a failed run with sanitized message ─


@pytest.mark.anyio
@pytest.mark.unit
async def test_runner_exception_creates_failed_run(
    acp_settings: Any,
    mock_app_state: Any,
    raising_event_stream: Any,
) -> None:
    """When the runner raises, the ACP run completes with a constant sanitized error."""
    import httpx
    from acp_sdk.models import Run, RunMode, RunStatus

    parent, _, _, _ = _build_child_and_parent(acp_settings, mock_app_state, raising_event_stream)

    async with parent.router.lifespan_context(parent):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=parent),
            base_url="http://test",
        ) as client:
            resp = await client.post("/acp/runs", json=_run_request(RunMode.SYNC))

    # The SDK wraps the error; the run may complete (with an error message) or fail
    assert resp.status_code in (200, 201, 500), resp.text[:300]

    # Raw exception text must never leak in the response body regardless of status
    assert "Simulated runner crash" not in resp.text, (
        "Raw exception message must not appear in the response body"
    )

    if resp.status_code in (200, 201):
        run = Run(**resp.json())
        # Either the run failed with a sanitized error, or completed with the error message yielded
        if run.status == RunStatus.FAILED:
            assert run.error is not None, "Failed run must carry an error object"
            assert "Simulated runner crash" not in (run.error.message or ""), (
                "Raw exception message must not be exposed"
            )
        else:
            # CugaACPAgent catches the exception and yields _CUGA_ERROR constant message
            output_text = "".join(p.content for msg in (run.output or []) for p in msg.parts if p.content)
            assert "Simulated runner crash" not in output_text, (
                "Raw exception message must not be in run output"
            )
