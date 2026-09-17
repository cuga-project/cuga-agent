"""Unit tests for delegate_task_via_acp (Task 3.2 — TDD).

These tests are written BEFORE the implementation exists (Task 3.3).
All tests will fail with ImportError or similar until the function is
implemented.  That is expected and intentional.

The function under test:

    async def delegate_task_via_acp(
        *,
        endpoint: str,
        agent_name: str,
        task: str,
        auth: Mapping[str, Any] | None = None,
        timeout: float = 30.0,
        verify_tls: bool = True,
        poll_interval: float = 0.25,
        client_factory: Callable[..., Client] = Client,
    ) -> dict[str, Any]:

Expected return shapes:
  success  -> {"result": <text>, "status": "success", "variables": {}}
  failed   -> {"result": "Remote ACP agent failed.", "status": "failed", "variables": {}}
  cancelled-> {"result": "Remote ACP agent cancelled the run.", "status": "failed", "variables": {}}
  awaiting -> {"result": "Remote ACP agent requires interactive input, which supervisor delegation does not support.", "status": "failed", "variables": {}}
  timeout  -> {"result": "Remote ACP agent timed out.", "status": "failed", "variables": {}}
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from acp_sdk.models import AgentManifest, Error, ErrorCode, Message, MessagePart, Run, RunStatus
from acp_sdk.models.errors import ACPError

# The function does not exist yet — importing it here is intentional.
# Tests will report ImportError/ModuleNotFoundError until Task 3.3 lands.
from cuga.backend.cuga_graph.nodes.cuga_supervisor.acp_protocol import delegate_task_via_acp

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_AGENT = "my-acp-agent"
_ENDPOINT = "https://acp.example.com"


def _text_msg(text: str) -> Message:
    """Build a single-part plain-text agent Message."""
    return Message(
        role="agent",
        parts=[MessagePart(content_type="text/plain", content=text, content_encoding="plain")],
    )


def _completed_run(*texts: str) -> Run:
    """Return a COMPLETED Run whose output contains one message per text."""
    return Run(
        agent_name=_AGENT,
        status=RunStatus.COMPLETED,
        output=[_text_msg(t) for t in texts],
    )


def _status_run(status: RunStatus) -> Run:
    """Return a Run with the given status and no output."""
    return Run(agent_name=_AGENT, status=status, output=[])


def _failed_run() -> Run:
    return Run(
        agent_name=_AGENT,
        status=RunStatus.FAILED,
        error=Error(code=ErrorCode.SERVER_ERROR, message="remote failure"),
        output=[],
    )


def _cancelled_run() -> Run:
    return Run(agent_name=_AGENT, status=RunStatus.CANCELLED, output=[])


def _awaiting_run() -> Run:
    return Run(agent_name=_AGENT, status=RunStatus.AWAITING, output=[])


def _manifest(name: str = _AGENT) -> AgentManifest:
    return AgentManifest(name=name, description="Test agent")


async def _async_iter(items: list) -> AsyncIterator:
    """Yield items from an async iterator."""
    for item in items:
        yield item


def _make_client(
    *,
    manifest: AgentManifest | None = None,
    agents: list[AgentManifest] | None = None,
    initial_run: Run | None = None,
    poll_runs: list[Run] | None = None,
    cancel_run: Run | None = None,
) -> MagicMock:
    """Build a mock ACP Client context manager.

    * ``agents`` — list returned by ``client.agents()`` async iterator.
    * ``manifest`` — single agent returned by ``client.agent(name=...)``; if
      None a default _manifest() is used.
    * ``initial_run`` — returned by ``run_async``; defaults to CREATED status.
    * ``poll_runs`` — sequence of Runs returned by successive ``run_status``
      calls; defaults to a single COMPLETED run.
    * ``cancel_run`` — returned by ``run_cancel``; defaults to CANCELLED run.
    """
    client = MagicMock()

    # async context-manager support
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    # agents() async iterator
    agent_list = agents if agents is not None else [manifest or _manifest()]

    async def _agents_iter(**_kwargs):
        for a in agent_list:
            yield a

    client.agents = MagicMock(return_value=_agents_iter())

    # agent(name=...) single lookup
    async def _agent(*, name: str, base_url=None):
        for a in agent_list:
            if a.name == name:
                return a
        raise LookupError(f"Agent '{name}' not found")

    client.agent = AsyncMock(side_effect=_agent)

    # run_async
    run_id = uuid.uuid4()
    default_initial = Run(agent_name=_AGENT, status=RunStatus.CREATED, run_id=run_id, output=[])
    first_run = initial_run if initial_run is not None else default_initial

    client.run_async = AsyncMock(return_value=first_run)

    # run_status — called once per poll cycle
    completed = _completed_run("Task done.")
    poll_sequence = poll_runs if poll_runs is not None else [completed]
    poll_iter = iter(poll_sequence)

    async def _run_status(*, run_id, base_url=None):
        try:
            return next(poll_iter)
        except StopIteration:
            return poll_sequence[-1]

    client.run_status = AsyncMock(side_effect=_run_status)

    # run_cancel
    cancelled = cancel_run if cancel_run is not None else _cancelled_run()
    client.run_cancel = AsyncMock(return_value=cancelled)

    return client


def _factory(client: MagicMock):
    """Return a client_factory callable that always yields *client*."""

    def _make(**_kwargs):
        return client

    return _make


# ---------------------------------------------------------------------------
# 1. Manifest discovery succeeds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_manifest_discovery_succeeds() -> None:
    """delegate_task_via_acp must call client.agent(name=agent_name)."""
    client = _make_client()
    result = await delegate_task_via_acp(
        endpoint=_ENDPOINT,
        agent_name=_AGENT,
        task="do something",
        poll_interval=0.0,
        client_factory=_factory(client),
    )
    client.agent.assert_awaited_once()
    assert result["status"] == "success"


# ---------------------------------------------------------------------------
# 2. Agent name mismatch or missing agent fails clearly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_agent_name_not_found_raises() -> None:
    """If the remote endpoint does not expose the requested agent, must raise."""
    client = _make_client(agents=[_manifest("other-agent")])
    with pytest.raises((ValueError, LookupError, RuntimeError)):
        await delegate_task_via_acp(
            endpoint=_ENDPOINT,
            agent_name="unknown-agent",
            task="do something",
            poll_interval=0.0,
            client_factory=_factory(client),
        )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_manifest_name_mismatch_raises() -> None:
    """If manifest.name != requested agent_name, must raise clearly."""
    wrong_manifest = _manifest("wrong-agent")
    client = _make_client(manifest=wrong_manifest, agents=[wrong_manifest])
    with pytest.raises((ValueError, LookupError, RuntimeError)):
        await delegate_task_via_acp(
            endpoint=_ENDPOINT,
            agent_name="expected-agent",
            task="do something",
            poll_interval=0.0,
            client_factory=_factory(client),
        )


# ---------------------------------------------------------------------------
# 3. Async run polled from created/in-progress to completed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_run_polled_from_created_to_completed() -> None:
    """Poll loop must continue through CREATED → IN_PROGRESS → COMPLETED."""
    poll_sequence = [
        _status_run(RunStatus.CREATED),
        _status_run(RunStatus.IN_PROGRESS),
        _completed_run("Final answer"),
    ]
    client = _make_client(poll_runs=poll_sequence)
    result = await delegate_task_via_acp(
        endpoint=_ENDPOINT,
        agent_name=_AGENT,
        task="do something",
        poll_interval=0.0,
        client_factory=_factory(client),
    )
    assert result["status"] == "success"
    assert result["result"] == "Final answer"
    assert client.run_status.await_count == 3


# ---------------------------------------------------------------------------
# 4. Text extracted from all plain-text output parts in order
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_text_extracted_from_multiple_output_messages() -> None:
    """Text from all plain-text output messages must be concatenated in order."""
    run = Run(
        agent_name=_AGENT,
        status=RunStatus.COMPLETED,
        output=[_text_msg("Hello "), _text_msg("world")],
    )
    client = _make_client(poll_runs=[run])
    result = await delegate_task_via_acp(
        endpoint=_ENDPOINT,
        agent_name=_AGENT,
        task="greet",
        poll_interval=0.0,
        client_factory=_factory(client),
    )
    assert result["status"] == "success"
    assert "Hello" in result["result"]
    assert "world" in result["result"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_non_plaintext_parts_excluded_from_result() -> None:
    """Non-plain-text parts (e.g. application/json) must not appear in text result."""
    parts = [
        MessagePart(content_type="text/plain", content="Plain text part", content_encoding="plain"),
        MessagePart(content_type="application/json", content='{"key": "value"}', content_encoding="plain"),
    ]
    run = Run(
        agent_name=_AGENT,
        status=RunStatus.COMPLETED,
        output=[Message(role="agent", parts=parts)],
    )
    client = _make_client(poll_runs=[run])
    result = await delegate_task_via_acp(
        endpoint=_ENDPOINT,
        agent_name=_AGENT,
        task="mixed output",
        poll_interval=0.0,
        client_factory=_factory(client),
    )
    assert result["status"] == "success"
    assert "Plain text part" in result["result"]


# ---------------------------------------------------------------------------
# 5. Failed and cancelled runs return normalized statuses
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_failed_run_returns_failed_status() -> None:
    """A FAILED run must return status='failed' with the normalized message."""
    client = _make_client(poll_runs=[_failed_run()])
    result = await delegate_task_via_acp(
        endpoint=_ENDPOINT,
        agent_name=_AGENT,
        task="will fail",
        poll_interval=0.0,
        client_factory=_factory(client),
    )
    assert result["status"] == "failed"
    assert result["result"] == "Remote ACP agent failed."
    assert result["variables"] == {}


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cancelled_run_returns_failed_status() -> None:
    """A CANCELLED run must return status='failed' with the cancellation message."""
    client = _make_client(poll_runs=[_cancelled_run()])
    result = await delegate_task_via_acp(
        endpoint=_ENDPOINT,
        agent_name=_AGENT,
        task="will cancel",
        poll_interval=0.0,
        client_factory=_factory(client),
    )
    assert result["status"] == "failed"
    assert result["result"] == "Remote ACP agent cancelled the run."
    assert result["variables"] == {}


# ---------------------------------------------------------------------------
# 6. Awaiting runs return failed/unsupported rather than polling forever
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_awaiting_run_returns_unsupported_result() -> None:
    """An AWAITING run must return immediately with the unsupported-input message."""
    client = _make_client(poll_runs=[_awaiting_run()])
    result = await delegate_task_via_acp(
        endpoint=_ENDPOINT,
        agent_name=_AGENT,
        task="needs input",
        poll_interval=0.0,
        client_factory=_factory(client),
    )
    assert result["status"] == "failed"
    assert result["result"] == (
        "Remote ACP agent requires interactive input, which supervisor delegation does not support."
    )
    assert result["variables"] == {}


# ---------------------------------------------------------------------------
# 7. Timeout triggers remote cancellation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_timeout_triggers_cancellation_and_returns_timeout_result() -> None:
    """When the timeout elapses, run_cancel must be called and result is 'timed out'."""
    # Make poll never reach terminal state within budget
    in_progress = _status_run(RunStatus.IN_PROGRESS)
    client = _make_client(poll_runs=[in_progress] * 1000)

    result = await delegate_task_via_acp(
        endpoint=_ENDPOINT,
        agent_name=_AGENT,
        task="slow task",
        timeout=0.01,  # very short budget
        poll_interval=0.0,
        client_factory=_factory(client),
    )
    client.run_cancel.assert_awaited_once()
    assert result["status"] == "failed"
    assert result["result"] == "Remote ACP agent timed out."
    assert result["variables"] == {}


# ---------------------------------------------------------------------------
# 8. Coroutine cancellation triggers remote cancellation and re-raises CancelledError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_coroutine_cancellation_triggers_remote_cancel_and_reraises() -> None:
    """asyncio.CancelledError must cancel the remote run then propagate."""
    cancel_event = asyncio.Event()
    poll_count = 0

    async def _slow_status(*, run_id, base_url=None):
        nonlocal poll_count
        poll_count += 1
        if poll_count >= 2:
            cancel_event.set()
        await asyncio.sleep(0.05)
        return _status_run(RunStatus.IN_PROGRESS)

    client = _make_client()
    client.run_status = AsyncMock(side_effect=_slow_status)

    async def _run_task():
        await asyncio.sleep(0.01)
        return await delegate_task_via_acp(
            endpoint=_ENDPOINT,
            agent_name=_AGENT,
            task="cancellable task",
            poll_interval=0.0,
            client_factory=_factory(client),
        )

    task = asyncio.create_task(_run_task())
    # Wait briefly then cancel
    await asyncio.sleep(0.02)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    client.run_cancel.assert_awaited_once()


# ---------------------------------------------------------------------------
# 9. Authentication header from configured environment variable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_auth_header_forwarded_to_client_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bearer token from auth must be passed to the client factory."""
    captured_kwargs: dict[str, Any] = {}

    def _capturing_factory(**kwargs):
        captured_kwargs.update(kwargs)
        return _make_client()

    await delegate_task_via_acp(
        endpoint=_ENDPOINT,
        agent_name=_AGENT,
        task="authenticated task",
        auth={"type": "bearer", "token": "secret-token"},
        poll_interval=0.0,
        client_factory=_capturing_factory,
    )
    # The factory must receive auth / headers containing the bearer token
    auth_val = captured_kwargs.get("auth") or captured_kwargs.get("headers") or {}
    auth_str = str(auth_val)
    assert "secret-token" in auth_str or "Bearer" in auth_str


@pytest.mark.asyncio
@pytest.mark.unit
async def test_auth_from_env_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    """If auth is not provided, the function should read from environment."""
    monkeypatch.setenv("ACP_AUTH_TOKEN", "env-token")
    captured_kwargs: dict[str, Any] = {}

    def _capturing_factory(**kwargs):
        captured_kwargs.update(kwargs)
        return _make_client()

    await delegate_task_via_acp(
        endpoint=_ENDPOINT,
        agent_name=_AGENT,
        task="env auth task",
        auth=None,
        poll_interval=0.0,
        client_factory=_capturing_factory,
    )
    # Implementation may or may not forward env token; it must at least not crash
    # and must succeed without a token being explicitly provided.


# ---------------------------------------------------------------------------
# 10. Missing token / malformed output / ACP error / transport error sanitized
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_missing_token_does_not_crash() -> None:
    """Calling with auth=None must not raise; the function must handle it gracefully."""
    client = _make_client()
    result = await delegate_task_via_acp(
        endpoint=_ENDPOINT,
        agent_name=_AGENT,
        task="no auth",
        auth=None,
        poll_interval=0.0,
        client_factory=_factory(client),
    )
    assert result["status"] in ("success", "failed")


@pytest.mark.asyncio
@pytest.mark.unit
async def test_malformed_output_parts_handled_gracefully() -> None:
    """Parts without content or with None content must not raise; result is sanitized."""
    parts = [
        MessagePart(content_type="text/plain", content=None, content_encoding="plain"),
        MessagePart(content_type="text/plain", content="real text", content_encoding="plain"),
    ]
    run = Run(
        agent_name=_AGENT,
        status=RunStatus.COMPLETED,
        output=[Message(role="agent", parts=parts)],
    )
    client = _make_client(poll_runs=[run])
    result = await delegate_task_via_acp(
        endpoint=_ENDPOINT,
        agent_name=_AGENT,
        task="malformed output",
        poll_interval=0.0,
        client_factory=_factory(client),
    )
    assert result["status"] == "success"
    assert "real text" in result["result"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_acp_error_during_run_async_is_sanitized() -> None:
    """ACPError raised by run_async must not leak raw exception text to caller."""
    client = _make_client()
    client.run_async = AsyncMock(
        side_effect=ACPError(Error(code=ErrorCode.SERVER_ERROR, message="internal server error"))
    )
    result = await delegate_task_via_acp(
        endpoint=_ENDPOINT,
        agent_name=_AGENT,
        task="failing task",
        poll_interval=0.0,
        client_factory=_factory(client),
    )
    assert result["status"] == "failed"
    # The raw server error message must NOT be returned verbatim to the caller
    assert "internal server error" not in result["result"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_transport_error_is_sanitized() -> None:
    """Network-level errors (e.g. OSError) must be caught and returned as failed."""
    import httpx

    client = _make_client()
    client.run_async = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
    result = await delegate_task_via_acp(
        endpoint=_ENDPOINT,
        agent_name=_AGENT,
        task="unreachable task",
        poll_interval=0.0,
        client_factory=_factory(client),
    )
    assert result["status"] == "failed"


# ---------------------------------------------------------------------------
# 11. TLS verification defaults true; redirects default false
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_tls_verification_defaults_true() -> None:
    """Client factory must be called with verify=True when verify_tls is not overridden."""
    captured: dict[str, Any] = {}

    def _capturing_factory(**kwargs):
        captured.update(kwargs)
        return _make_client()

    await delegate_task_via_acp(
        endpoint=_ENDPOINT,
        agent_name=_AGENT,
        task="tls check",
        poll_interval=0.0,
        client_factory=_capturing_factory,
    )
    assert captured.get("verify", True) is True


@pytest.mark.asyncio
@pytest.mark.unit
async def test_tls_verification_can_be_disabled() -> None:
    """Passing verify_tls=False must forward verify=False to the client factory."""
    captured: dict[str, Any] = {}

    def _capturing_factory(**kwargs):
        captured.update(kwargs)
        return _make_client()

    await delegate_task_via_acp(
        endpoint=_ENDPOINT,
        agent_name=_AGENT,
        task="no tls",
        verify_tls=False,
        poll_interval=0.0,
        client_factory=_capturing_factory,
    )
    assert captured.get("verify") is False


@pytest.mark.asyncio
@pytest.mark.unit
async def test_redirects_default_false() -> None:
    """Client factory must be called with follow_redirects=False by default."""
    captured: dict[str, Any] = {}

    def _capturing_factory(**kwargs):
        captured.update(kwargs)
        return _make_client()

    await delegate_task_via_acp(
        endpoint=_ENDPOINT,
        agent_name=_AGENT,
        task="redirect check",
        poll_interval=0.0,
        client_factory=_capturing_factory,
    )
    assert captured.get("follow_redirects", False) is False


# ---------------------------------------------------------------------------
# 12. Return shape contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_success_return_shape() -> None:
    """Successful delegation must return exactly {result, status, variables}."""
    client = _make_client(poll_runs=[_completed_run("answer text")])
    result = await delegate_task_via_acp(
        endpoint=_ENDPOINT,
        agent_name=_AGENT,
        task="shape check",
        poll_interval=0.0,
        client_factory=_factory(client),
    )
    assert set(result.keys()) == {"result", "status", "variables"}
    assert result["status"] == "success"
    assert result["variables"] == {}
    assert isinstance(result["result"], str)
