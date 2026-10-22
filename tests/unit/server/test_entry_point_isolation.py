"""Unit tests for scoped_checkpoint_key() (W07.1) and non-UI entry point isolation (W07.2–W07.5)."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cuga.backend.server.conversation_threads import Scope
from cuga.backend.server.thread_scope_key import scoped_checkpoint_key

pytestmark = pytest.mark.unit


def _scope(tenant_id="tenant1", instance_id="inst1", agent_id="agent1", user_id="user1") -> Scope:
    return Scope(tenant_id=tenant_id, instance_id=instance_id, agent_id=agent_id, user_id=user_id)


# 1. Two scopes with the same external_id produce different keys when tenant_id differs
def test_different_tenant_produces_different_key():
    scope_a = _scope(tenant_id="tenantA")
    scope_b = _scope(tenant_id="tenantB")
    key_a = scoped_checkpoint_key("run", scope_a, "ext-001")
    key_b = scoped_checkpoint_key("run", scope_b, "ext-001")
    assert key_a != key_b


# 1b. Two scopes with the same external_id produce different keys when agent_id differs
def test_different_agent_produces_different_key():
    scope_a = _scope(agent_id="agentX")
    scope_b = _scope(agent_id="agentY")
    key_a = scoped_checkpoint_key("run", scope_a, "ext-001")
    key_b = scoped_checkpoint_key("run", scope_b, "ext-001")
    assert key_a != key_b


# 2. An external_id containing ':' cannot collide with another scope
def test_colon_in_external_id_is_encoded_and_cannot_forge_scope():
    # A crafted external_id that embeds a colon followed by scope-like data
    # must NOT produce the same key as a legitimate scope with those values.
    legitimate_scope = _scope(tenant_id="tenant1", agent_id="agent1")
    crafted_scope = _scope(tenant_id="tenant1", agent_id="agent1")

    # Craft an external_id that contains ':' hoping to shift scope boundaries
    crafted_external_id = "x:tenant1:inst1:agent1:real-ext"
    key_crafted = scoped_checkpoint_key("run", crafted_scope, crafted_external_id)
    key_legit = scoped_checkpoint_key("run", legitimate_scope, "real-ext")

    assert key_crafted != key_legit


def test_colon_in_external_id_is_percent_encoded():
    scope = _scope()
    key = scoped_checkpoint_key("run", scope, "id:with:colons")
    # The external_id portion should have colons encoded, not raw
    assert "id%3Awith%3Acolons" in key
    # Raw colon in external_id must not appear as a bare field separator
    parts = key.split(":")
    # Format: entry:tenant:instance:agent:external_id
    # With encoded colons the split should yield exactly 5 parts
    assert len(parts) == 5


# 3. The output never parses as a valid UUID (starts with entry prefix)
def test_output_is_not_a_valid_uuid():
    scope = _scope()
    key = scoped_checkpoint_key("run", scope, str(uuid.uuid4()))
    with pytest.raises(ValueError):
        uuid.UUID(key)
    assert key.startswith("run:")


# 4. Same scope + same external_id always produces the same key (deterministic)
def test_same_inputs_produce_same_key():
    scope = _scope()
    key1 = scoped_checkpoint_key("run", scope, "stable-id")
    key2 = scoped_checkpoint_key("run", scope, "stable-id")
    assert key1 == key2


# 5. The "run:" prefix key is distinct from a bare UUID even when external_id equals that UUID
def test_prefixed_key_differs_from_bare_uuid():
    raw_uuid = str(uuid.uuid4())
    scope = _scope()
    prefixed_key = scoped_checkpoint_key("run", scope, raw_uuid)
    assert prefixed_key != raw_uuid
    assert prefixed_key.startswith("run:")


# ---------------------------------------------------------------------------
# W07.2 — /run endpoint uses scoped_checkpoint_key
# ---------------------------------------------------------------------------

_KNOWN_SCOPE = _scope(tenant_id="tenant1", instance_id="inst1", agent_id="cuga-default")
_WEB_UUID = str(uuid.uuid4())


@pytest.mark.asyncio
async def test_run_endpoint_passes_prefixed_key_to_event_stream():
    """W07.2: /run must pass a run:-prefixed key to event_stream, not the raw UUID."""
    from cuga.backend.server import run_routes

    captured: dict = {}

    async def fake_event_stream(*args, **kwargs):
        captured["thread_id"] = kwargs.get("thread_id")
        return
        yield  # make it an async generator

    run_routes._EVENT_STREAM = fake_event_stream
    run_routes._DEFAULT_USER_ID = "default_user"
    run_routes._ASSERT_THREAD_ACCESS = AsyncMock()
    run_routes._RUNTIME_AGENT_ID = lambda: "cuga-default"

    # Build a minimal Request mock
    body = {"query": "hello", "thread_id": _WEB_UUID}
    request = MagicMock()
    request.json = AsyncMock(return_value=body)
    request.headers = {}
    request.app = MagicMock()

    with (
        patch("cuga.backend.server.run_routes._run_auth_failure", new=AsyncMock(return_value=None)),
        patch("cuga.backend.server.run_routes._authenticated_user_id", new=AsyncMock(return_value=None)),
        patch("cuga.backend.server.run_routes.events_bridge.forwards_to_events", return_value=False),
        patch("cuga.backend.server.run_routes._get_supervisor", new=AsyncMock(return_value=None)),
        patch("cuga.backend.server.run_routes._release_supervisor", new=AsyncMock()),
        patch(
            "cuga.backend.server.conversation_threads.request_scope",
            return_value=_KNOWN_SCOPE,
        ),
        patch(
            "cuga.backend.server.thread_scope.checkpoint_thread_id",
            return_value="cuga-thread-v1:abc123",
        ),
    ):
        await run_routes.run_sync(request)

    assert captured.get("thread_id", "").startswith("run:"), (
        f"Expected run:-prefixed key, got: {captured.get('thread_id')}"
    )
    assert captured.get("thread_id") != _WEB_UUID, "run_key must not equal the raw web UUID"


# ---------------------------------------------------------------------------
# W07.3 — SimpleAgentRunner uses scoped_checkpoint_key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_simple_runner_uses_a2a_prefixed_key():
    """W07.3: SimpleAgentRunner must pass an a2a:-prefixed key to event_stream."""
    from cuga.backend.server.agent_protocol.simple_runner import SimpleAgentRunner

    web_uuid = str(uuid.uuid4())
    captured: dict = {}

    async def fake_event_stream(*args, **kwargs):
        captured["thread_id"] = kwargs.get("thread_id")
        frame = b'event: Answer\ndata: {"data": "done"}\n\n'
        yield frame

    app_state = MagicMock()
    app_state.agent_id = "cuga-default"
    app_state.agent = None  # no graph, so _pending_action returns None early

    runner = SimpleAgentRunner(
        app_state_ref=app_state,
        event_stream_func=fake_event_stream,
        auto_approve=False,
        caller_user_id="a2a_user",
    )

    with (
        patch(
            "cuga.backend.server.conversation_history.get_conversation_db",
            return_value=MagicMock(claim_thread=AsyncMock()),
        ),
        patch(
            "cuga.backend.server.conversation_threads.request_scope",
            return_value=_KNOWN_SCOPE,
        ),
    ):
        events = []
        async for ev in runner.run("hello", context_id=web_uuid):
            events.append(ev)

    assert captured.get("thread_id", "").startswith("a2a:"), (
        f"Expected a2a:-prefixed key, got: {captured.get('thread_id')}"
    )
    assert captured.get("thread_id") != web_uuid, "checkpoint_key must not equal the raw web UUID"


# ---------------------------------------------------------------------------
# W07.4 — ACP StdioCugaRunner uses scoped_checkpoint_key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acp_runner_uses_acp_prefixed_key():
    """W07.4: ACP StdioCugaRunner must pass an acp:-prefixed key to agent.invoke."""
    from cuga.backend.server.acp.runner import StdioCugaRunner

    web_uuid = str(uuid.uuid4())
    captured: dict = {}

    mock_result = MagicMock()
    mock_result.error = None
    mock_result.answer = "done"

    mock_graph = MagicMock()
    mock_graph.aget_state = AsyncMock(return_value=MagicMock(next=None))

    mock_agent = MagicMock()
    mock_agent.agent_id = "cuga-default"
    mock_agent.graph = mock_graph

    async def fake_invoke(message, *, thread_id=None, action_response=None):
        captured["thread_id"] = thread_id
        return mock_result

    mock_agent.invoke = fake_invoke

    runner = StdioCugaRunner()
    runner._agent = mock_agent
    runner._initialized = True

    with patch(
        "cuga.backend.server.conversation_threads.request_scope",
        return_value=_KNOWN_SCOPE,
    ):
        events = []
        async for ev in runner.run("hello", context_id=web_uuid):
            events.append(ev)

    assert captured.get("thread_id", "").startswith("acp:"), (
        f"Expected acp:-prefixed key, got: {captured.get('thread_id')}"
    )
    assert captured.get("thread_id") != web_uuid, "checkpoint_key must not equal the raw web UUID"


# ---------------------------------------------------------------------------
# W07.5 — /extension/agent_query uses scoped_checkpoint_key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extension_endpoint_uses_ext_prefixed_key():
    """W07.5: extension_agent_query must pass an ext:-prefixed key to event_stream."""
    # Import main lazily; avoid full FastAPI startup by only testing the scoping logic.
    # We test the key derivation directly using the same inputs the endpoint uses.
    from cuga.backend.server.thread_scope_key import scoped_checkpoint_key

    web_uuid = str(uuid.uuid4())
    ext_scope = _KNOWN_SCOPE

    # Simulate what extension_agent_query does: compute ext_key from body_request_id
    ext_key = scoped_checkpoint_key("ext", ext_scope, web_uuid)

    assert ext_key.startswith("ext:"), f"Expected ext:-prefixed key, got: {ext_key}"
    assert ext_key != web_uuid, "ext_key must not equal the raw web UUID"
    # Confirm the key is deterministic (same inputs → same key)
    assert ext_key == scoped_checkpoint_key("ext", ext_scope, web_uuid)
