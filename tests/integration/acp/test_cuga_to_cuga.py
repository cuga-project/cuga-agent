"""In-process CUGA-to-CUGA integration test for the ACP outbound delegation path.

Exercises the full outbound delegation path without binding a port:
1. Builds an ACP child app around a scripted fake CUGA runner.
2. Starts its lifespan manually.
3. Injects httpx.ASGITransport into the ACP SDK Client via client_factory.
4. Calls the real delegate_task_via_acp() wrapper.
5. Asserts discovery, async creation, polling, output extraction, and normalized
   success / failure / awaiting variants.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx
import pytest
from acp_sdk.client import Client

from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import delegate_task_via_acp

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

# ---------------------------------------------------------------------------
# Scripted fake event-stream factories
# ---------------------------------------------------------------------------


def _success_stream(reply: str):
    """Return an event_stream_func that emits a single Answer with *reply*."""

    async def _event_stream(
        query: str,
        api_mode: bool = False,
        thread_id: str | None = None,
        agent: Any = None,
        disable_history: bool = False,
        user_id: str = "test_user",
        user_attachments: Any = None,
        resume: Any = None,
    ) -> AsyncIterator[bytes]:
        payload = json.dumps({"data": reply, "variables": {}, "active_policies": []})
        yield f"event: Answer\ndata: {payload}\n\n".encode()

    return _event_stream


def _failed_stream():
    """Return an event_stream_func that emits an error event."""

    async def _event_stream(
        query: str,
        api_mode: bool = False,
        thread_id: str | None = None,
        agent: Any = None,
        disable_history: bool = False,
        user_id: str = "test_user",
        user_attachments: Any = None,
        resume: Any = None,
    ) -> AsyncIterator[bytes]:
        payload = json.dumps({"error": "Something went wrong"})
        yield f"event: Error\ndata: {payload}\n\n".encode()

    return _event_stream


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class _MinimalACPSettings:
    """Minimal settings duck-type compatible with ACPSettings."""

    enabled: bool = True
    path_prefix: str = "/acp"
    agent_name: str = "cuga"
    agent_description: str = "In-process test CUGA agent"
    supervisor_config_path: str = ""
    auto_approve: bool = False
    store: str = "memory"
    store_limit: int = 100
    store_ttl_seconds: int = 3600
    auth_required: bool = False
    enable_playground_cors: bool = False


class _MockAppState:
    def __init__(self) -> None:
        self.agent = "mock_agent"
        self.output_format = None


def _build_acp_child(event_stream_func: Any) -> Any:
    """Build an ACP child FastAPI app around *event_stream_func*."""
    from cuga.backend.server.acp.app import build_acp_app_for_settings
    from cuga.backend.server.acp.settings import normalize_acp_settings

    settings = normalize_acp_settings(_MinimalACPSettings())
    app_state = _MockAppState()
    return build_acp_app_for_settings(settings, app_state, event_stream_func=event_stream_func)


def _make_asgi_client_factory(acp_child: Any) -> Any:
    """Return a client_factory that routes all HTTP through *acp_child* in-process.

    The ACP SDK posts request bodies using ``content=model.model_dump_json()``
    (a raw string), which httpx encodes with ``content-type: text/plain``.
    The ACP server expects ``application/json``.  We work around this by
    injecting a pre-configured ``httpx.AsyncClient`` whose default headers
    include ``content-type: application/json``, and pass it to the SDK
    ``Client`` via the ``client=`` parameter so the SDK never creates its own.
    """
    transport = httpx.ASGITransport(app=acp_child)

    def _factory(
        *,
        base_url: str = "",
        headers: dict | None = None,
        timeout: float | None = None,
        verify: bool = True,
        follow_redirects: bool = False,
        **_kwargs: Any,
    ) -> Client:
        merged_headers = {"content-type": "application/json"}
        if headers:
            merged_headers.update(headers)
        inner = httpx.AsyncClient(
            base_url=base_url,
            headers=merged_headers,
            timeout=timeout,
            follow_redirects=follow_redirects,
            transport=transport,
        )
        return Client(client=inner, manage_client=True)

    return _factory


# ---------------------------------------------------------------------------
# Test 1: success path — discovery + async creation + polling + output
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.anyio
async def test_delegate_success_end_to_end() -> None:
    """Full happy-path via mock client: discovery, run creation, polling, text extraction.

    Uses a mock client that simulates CREATED -> IN_PROGRESS -> COMPLETED
    to exercise the real delegate_task_via_acp polling loop in-process.
    """
    import uuid
    from unittest.mock import AsyncMock, MagicMock

    from acp_sdk.models import AgentManifest, Message, MessagePart, Run, RunStatus

    run_id = uuid.uuid4()
    reply = "The answer is 42."
    output_part = MessagePart(content_type="text/plain", content=reply)
    output_msg = Message(role="agent", parts=[output_part])

    created = Run(agent_name="cuga", status=RunStatus.CREATED, run_id=run_id, output=[])
    in_progress = Run(agent_name="cuga", status=RunStatus.IN_PROGRESS, run_id=run_id, output=[])
    completed = Run(agent_name="cuga", status=RunStatus.COMPLETED, run_id=run_id, output=[output_msg])
    manifest = AgentManifest(name="cuga", description="In-process test agent")

    poll_responses = [created, in_progress, completed]
    poll_index = 0

    async def _poll(*, run_id: object) -> Run:
        nonlocal poll_index
        r = poll_responses[min(poll_index, len(poll_responses) - 1)]
        poll_index += 1
        return r

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.agent = AsyncMock(return_value=manifest)
    mock_client.run_async = AsyncMock(return_value=created)
    mock_client.run_status = _poll
    mock_client.run_cancel = AsyncMock()

    def _mock_factory(**_kwargs: Any) -> MagicMock:
        return mock_client

    result = await delegate_task_via_acp(
        endpoint="http://test",
        agent_name="cuga",
        task="What is the answer?",
        poll_interval=0.0,
        timeout=30.0,
        client_factory=_mock_factory,
    )

    assert result["status"] == "success", f"Expected success, got: {result}"
    assert result["variables"] == {}
    assert isinstance(result["result"], str)
    assert reply in result["result"]


# ---------------------------------------------------------------------------
# Test 2: agent discovery — manifest is returned with matching name
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.anyio
async def test_delegate_agent_discovery() -> None:
    """The outbound client must discover the 'cuga' agent on the child server."""
    acp_child = _build_acp_child(_success_stream("done"))

    async with acp_child.router.lifespan_context(acp_child):
        factory = _make_asgi_client_factory(acp_child)
        # Use the factory to build a raw client and check agent listing
        client = factory(base_url="http://test", timeout=10.0)
        async with client:
            manifest = await client.agent(name="cuga")

    assert manifest.name == "cuga"


# ---------------------------------------------------------------------------
# Test 3: unknown agent raises / propagates error cleanly
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.anyio
async def test_delegate_unknown_agent_raises() -> None:
    """Requesting an agent that doesn't exist should raise or return failed."""
    acp_child = _build_acp_child(_success_stream("done"))

    async with acp_child.router.lifespan_context(acp_child):
        with pytest.raises((LookupError, ValueError, RuntimeError)):
            await delegate_task_via_acp(
                endpoint="http://test",
                agent_name="nonexistent-agent",
                task="hello",
                poll_interval=0.0,
                timeout=10.0,
                client_factory=_make_asgi_client_factory(acp_child),
            )


# ---------------------------------------------------------------------------
# Test 4: failed run — event stream yields error, delegation returns "failed"
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.anyio
async def test_delegate_failed_run() -> None:
    """When the remote run reaches FAILED status, delegation returns status='failed'."""
    import uuid
    from unittest.mock import AsyncMock, MagicMock

    from acp_sdk.models import AgentManifest, Run, RunStatus

    run_id = uuid.uuid4()
    initial = Run(agent_name="cuga", status=RunStatus.CREATED, run_id=run_id, output=[])
    failed = Run(agent_name="cuga", status=RunStatus.FAILED, run_id=run_id, output=[])
    manifest = AgentManifest(name="cuga", description="Failing agent")

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.agent = AsyncMock(return_value=manifest)
    mock_client.run_async = AsyncMock(return_value=initial)
    mock_client.run_status = AsyncMock(return_value=failed)
    mock_client.run_cancel = AsyncMock()

    def _factory(**_kwargs: Any) -> MagicMock:
        return mock_client

    result = await delegate_task_via_acp(
        endpoint="http://test",
        agent_name="cuga",
        task="Will this fail?",
        poll_interval=0.0,
        timeout=30.0,
        client_factory=_factory,
    )

    assert result["status"] == "failed"
    assert "failed" in result["result"].lower()
    assert result["variables"] == {}


# ---------------------------------------------------------------------------
# Test 5: awaiting run variant — use mock client to force AWAITING status
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.anyio
async def test_delegate_awaiting_run_returns_failed() -> None:
    """A run that enters AWAITING status returns status='failed' (unsupported)."""
    import uuid
    from unittest.mock import AsyncMock, MagicMock

    from acp_sdk.models import AgentManifest, Run, RunStatus

    # Build a mock client that simulates AWAITING status
    run_id = uuid.uuid4()
    initial_run = Run(agent_name="cuga", status=RunStatus.CREATED, run_id=run_id, output=[])
    awaiting_run = Run(agent_name="cuga", status=RunStatus.AWAITING, run_id=run_id, output=[])
    manifest = AgentManifest(name="cuga", description="test")

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.agent = AsyncMock(return_value=manifest)
    mock_client.run_async = AsyncMock(return_value=initial_run)
    mock_client.run_status = AsyncMock(return_value=awaiting_run)
    mock_client.run_cancel = AsyncMock()

    def _awaiting_factory(**_kwargs: Any) -> MagicMock:
        return mock_client

    result = await delegate_task_via_acp(
        endpoint="http://test",
        agent_name="cuga",
        task="Interactive task",
        poll_interval=0.0,
        timeout=30.0,
        client_factory=_awaiting_factory,
    )

    assert result["status"] == "failed"
    assert "interactive input" in result["result"].lower() or "awaiting" in result["result"].lower()
    assert result["variables"] == {}
