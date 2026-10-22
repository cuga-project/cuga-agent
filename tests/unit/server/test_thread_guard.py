"""Unit tests for thread_guard.py — W06.1."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from cuga.backend.server.conversation_threads import Scope, ThreadRecord, ThreadStatus
from cuga.backend.server.thread_guard import ThreadNotFound, resolve_owned_thread, _NOT_FOUND_DETAIL

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scope(
    tenant_id: str = "tenant-1",
    instance_id: str = "inst-1",
    agent_id: str = "agent-1",
    user_id: str = "user-1",
) -> Scope:
    return Scope(
        tenant_id=tenant_id,
        instance_id=instance_id,
        agent_id=agent_id,
        user_id=user_id,
    )


def _record(scope: Scope, thread_id: str = "tid-abc") -> ThreadRecord:
    return ThreadRecord(
        thread_id=thread_id,
        tenant_id=scope.tenant_id,
        instance_id=scope.instance_id,
        agent_id=scope.agent_id,
        user_id=scope.user_id,
        status=ThreadStatus.ACTIVE,
        config_agent_hash="h",
        config_runtime_ver="1.0",
        pending_interrupt=False,
        latest_checkpoint_id="ckpt-1",
        projected_checkpoint_id="",
        created_at="2024-01-01T00:00:00+00:00",
        updated_at="2024-01-01T00:00:00+00:00",
    )


def _mock_db(return_value):
    """Return a mock ConversationThreadsDB whose get_in_scope returns return_value."""
    db = MagicMock()
    db.get_in_scope = AsyncMock(return_value=return_value)
    return db


# ---------------------------------------------------------------------------
# Test 1: owner gets the record
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_owner_gets_record():
    """resolve_owned_thread returns the ThreadRecord when the scope matches."""
    scope = _scope()
    rec = _record(scope, "tid-owner")
    mock_db = _mock_db(rec)

    with patch(
        "cuga.backend.server.conversation_threads.get_conversation_threads_db",
        return_value=mock_db,
    ):
        result = await resolve_owned_thread(scope, "tid-owner")

    assert result is rec
    mock_db.get_in_scope.assert_awaited_once_with(scope, "tid-owner")


# ---------------------------------------------------------------------------
# Test 2: another user's thread ID gives same 404 as a random UUID
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_another_users_thread_same_404_as_random_uuid():
    """Both another user's thread and a random UUID produce identical ThreadNotFound."""
    scope = _scope()

    # Simulate another user's thread: DB returns None (scope mismatch → not found)
    mock_db_other = _mock_db(None)
    with patch(
        "cuga.backend.server.conversation_threads.get_conversation_threads_db",
        return_value=mock_db_other,
    ):
        with pytest.raises(ThreadNotFound) as exc_other:
            await resolve_owned_thread(scope, "other-users-tid")

    # Simulate a random UUID: DB also returns None
    mock_db_random = _mock_db(None)
    with patch(
        "cuga.backend.server.conversation_threads.get_conversation_threads_db",
        return_value=mock_db_random,
    ):
        with pytest.raises(ThreadNotFound) as exc_random:
            await resolve_owned_thread(scope, "00000000-0000-0000-0000-000000000000")

    assert exc_other.value.status_code == exc_random.value.status_code == 404
    assert exc_other.value.detail == exc_random.value.detail == _NOT_FOUND_DETAIL


# ---------------------------------------------------------------------------
# Test 3: wrong agent_id gives 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wrong_agent_id_raises_not_found():
    """A scope with the wrong agent_id resolves to None in the DB → ThreadNotFound."""
    wrong_scope = _scope(agent_id="wrong-agent")
    mock_db = _mock_db(None)

    with patch(
        "cuga.backend.server.conversation_threads.get_conversation_threads_db",
        return_value=mock_db,
    ):
        with pytest.raises(ThreadNotFound) as exc_info:
            await resolve_owned_thread(wrong_scope, "tid-abc")

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == _NOT_FOUND_DETAIL


# ---------------------------------------------------------------------------
# Test 4: DB exception gives 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_db_exception_raises_not_found():
    """A DB error during get_in_scope must result in ThreadNotFound (fail-closed)."""
    scope = _scope()
    db = MagicMock()
    db.get_in_scope = AsyncMock(side_effect=RuntimeError("connection refused"))

    with patch(
        "cuga.backend.server.conversation_threads.get_conversation_threads_db",
        return_value=db,
    ):
        with pytest.raises(ThreadNotFound) as exc_info:
            await resolve_owned_thread(scope, "tid-abc")

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == _NOT_FOUND_DETAIL


# ---------------------------------------------------------------------------
# Test 5: empty or oversized thread_id gives 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_thread_id_raises_not_found():
    """An empty thread_id must raise ThreadNotFound without touching the DB."""
    scope = _scope()

    with patch(
        "cuga.backend.server.conversation_threads.get_conversation_threads_db",
    ) as mock_factory:
        with pytest.raises(ThreadNotFound) as exc_info:
            await resolve_owned_thread(scope, "")

    mock_factory.assert_not_called()
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == _NOT_FOUND_DETAIL


@pytest.mark.asyncio
async def test_whitespace_only_thread_id_raises_not_found():
    """A whitespace-only thread_id must raise ThreadNotFound without touching the DB."""
    scope = _scope()

    with patch(
        "cuga.backend.server.conversation_threads.get_conversation_threads_db",
    ) as mock_factory:
        with pytest.raises(ThreadNotFound) as exc_info:
            await resolve_owned_thread(scope, "   ")

    mock_factory.assert_not_called()
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == _NOT_FOUND_DETAIL


@pytest.mark.asyncio
async def test_oversized_thread_id_raises_not_found():
    """A thread_id longer than 128 chars must raise ThreadNotFound without touching the DB."""
    scope = _scope()
    oversized = "x" * 129

    with patch(
        "cuga.backend.server.conversation_threads.get_conversation_threads_db",
    ) as mock_factory:
        with pytest.raises(ThreadNotFound) as exc_info:
            await resolve_owned_thread(scope, oversized)

    mock_factory.assert_not_called()
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == _NOT_FOUND_DETAIL


# ---------------------------------------------------------------------------
# W06.2 / W06.3 / W06.4 — endpoint wiring with a real (temp) threads DB
# ---------------------------------------------------------------------------


@pytest.fixture
def threads_db(tmp_path, monkeypatch):
    from cuga.backend.server.conversation_threads import ConversationThreadsDB
    from cuga.backend.storage.relational.local import LocalRelationalStore

    store = LocalRelationalStore(str(tmp_path / "threads.db"))
    db = ConversationThreadsDB()
    db._get_store = lambda: store
    monkeypatch.setattr("cuga.backend.server.conversation_threads.get_conversation_threads_db", lambda: db)
    return db


@pytest.fixture
def stream_app(monkeypatch):
    from types import SimpleNamespace

    from fastapi import FastAPI

    from cuga.backend.server import main

    graph = MagicMock()
    graph.aget_state = AsyncMock()
    graph.get_state = MagicMock()
    state = SimpleNamespace(
        knowledge_provider=None,
        knowledge_engine=None,
        current_llm=None,
        agent_id="cuga-default",
        output_format=None,
        agent=SimpleNamespace(graph=graph),
        stop_events={},
    )
    seen: list = []

    async def fake_event_stream(*args, **kwargs):
        seen.append(kwargs)
        yield "data: ok\n\n"

    monkeypatch.setattr(main, "app_state", state)
    monkeypatch.setattr(main, "event_stream", fake_event_stream)
    monkeypatch.setattr(main, "get_query", AsyncMock(return_value="hello"))
    monkeypatch.setattr(main, "get_attachment_snapshot", AsyncMock(return_value=None))
    monkeypatch.setattr(main.events_bridge, "forwards_to_events", lambda *args: False)
    app = FastAPI()
    app.post("/stream")(main.stream)
    app.post("/stop")(main.stop)
    return SimpleNamespace(app=app, main=main, graph=graph, seen=seen)


def _client(app):
    import httpx

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_stream_other_users_thread_is_404_before_any_state_read(threads_db, stream_app):
    """W06.2: a foreign thread ID 404s before the saver/graph is touched or the stream starts."""
    from types import SimpleNamespace

    from cuga.backend.server.conversation_threads import request_scope

    owner = SimpleNamespace(sub="user-a")
    await threads_db.create(request_scope(owner, "cuga-default"), "thread-a")
    stream_app.app.dependency_overrides[stream_app.main.require_chat_access] = lambda: SimpleNamespace(
        sub="user-b"
    )

    async with _client(stream_app.app) as client:
        response = await client.post("/stream", headers={"X-Thread-ID": "thread-a"})

    assert response.status_code == 404
    assert response.json() == {"detail": _NOT_FOUND_DETAIL}
    stream_app.graph.aget_state.assert_not_called()
    stream_app.graph.get_state.assert_not_called()
    assert stream_app.seen == []


@pytest.mark.asyncio
async def test_stream_first_turn_record_without_history_is_protected(threads_db, stream_app):
    """A freshly created (status=new, no history) thread is 404 for others and open to its owner."""
    from types import SimpleNamespace

    from cuga.backend.server.conversation_threads import request_scope

    owner = SimpleNamespace(sub="user-a")
    rec = await threads_db.create(request_scope(owner, "cuga-default"), "fresh-thread")
    assert rec.status == ThreadStatus.NEW

    stream_app.app.dependency_overrides[stream_app.main.require_chat_access] = lambda: SimpleNamespace(
        sub="user-b"
    )
    async with _client(stream_app.app) as client:
        denied = await client.post("/stream", headers={"X-Thread-ID": "fresh-thread"})
    assert denied.status_code == 404
    assert stream_app.seen == []

    stream_app.app.dependency_overrides[stream_app.main.require_chat_access] = lambda: owner
    async with _client(stream_app.app) as client:
        allowed = await client.post("/stream", headers={"X-Thread-ID": "fresh-thread"})
    assert allowed.status_code == 200
    assert stream_app.seen[0]["thread_id"] == "fresh-thread"


@pytest.mark.asyncio
async def test_stream_draft_preview_uses_namespaced_key_without_guard(threads_db, stream_app):
    """W06.2: draft + disable-history skips the record guard and uses a draft: key."""
    stream_app.app.dependency_overrides[stream_app.main.require_chat_access] = lambda: None

    async with _client(stream_app.app) as client:
        response = await client.post(
            "/stream",
            headers={"X-Thread-ID": "client-made-id", "X-Use-Draft": "true", "X-Disable-History": "true"},
        )

    assert response.status_code == 200
    key = stream_app.seen[0]["thread_id"]
    assert key.startswith("draft:") and key.endswith(":client-made-id")


@pytest.mark.asyncio
async def test_auth_disabled_server_issued_thread_passes_guard(threads_db, stream_app):
    """W06.4: current_user=None maps to DEFAULT_USER_ID; a server-issued record passes."""
    from cuga.backend.server.conversation_threads import request_scope

    await threads_db.create(request_scope(None, "cuga-default"), "local-thread")
    stream_app.app.dependency_overrides[stream_app.main.require_chat_access] = lambda: None

    async with _client(stream_app.app) as client:
        stop = await client.post("/stop", headers={"X-Thread-ID": "local-thread"})
        stream = await client.post("/stream", headers={"X-Thread-ID": "local-thread"})

    assert stop.status_code == 200
    assert stream.status_code == 200


@pytest.mark.asyncio
async def test_auth_disabled_invented_thread_id_is_404(threads_db, stream_app):
    """W06.4: no special-casing of the default user; invented IDs 404 in single-user mode."""
    stream_app.app.dependency_overrides[stream_app.main.require_chat_access] = lambda: None

    async with _client(stream_app.app) as client:
        stop = await client.post("/stop", headers={"X-Thread-ID": "invented-id"})
        stream = await client.post("/stream", headers={"X-Thread-ID": "invented-id"})

    assert stop.status_code == 404
    assert stream.status_code == 404
    assert stream_app.seen == []


# ---------------------------------------------------------------------------
# W09.5 — two simultaneous /stream posts on one thread: 200 + 409 conflict
# ---------------------------------------------------------------------------


def test_concurrent_stream_posts_one_200_one_409_conflict(tmp_path, monkeypatch, stream_app):
    """The second simultaneous turn on a thread gets 409 {"error": "conflict", "retry_after_ms": 2000}."""
    import asyncio
    from types import SimpleNamespace


    from cuga.backend.server.thread_lease import ThreadLease
    from cuga.backend.storage.relational.local import LocalRelationalStore

    store = LocalRelationalStore(str(tmp_path / "leases.db"))
    lease = ThreadLease()
    lease._get_store = lambda: store

    # Stand-in for the T13 prelude: acquire the per-thread lease and never release it,
    # as an in-flight turn would. The loser raises ThreadBusy, which /stream maps to 409.
    async def guard_with_lease(scope, thread_id):
        await lease.acquire(thread_id)

    monkeypatch.setattr("cuga.backend.server.thread_guard.resolve_owned_thread", guard_with_lease)
    stream_app.app.dependency_overrides[stream_app.main.require_chat_access] = lambda: SimpleNamespace(
        sub="user-a"
    )

    async def two_posts():
        async with _client(stream_app.app) as client:
            return await asyncio.gather(
                client.post("/stream", headers={"X-Thread-ID": "busy-thread"}),
                client.post("/stream", headers={"X-Thread-ID": "busy-thread"}),
            )

    responses = asyncio.run(two_posts())

    codes = sorted(r.status_code for r in responses)
    assert codes == [200, 409]
    loser = next(r for r in responses if r.status_code == 409)
    assert loser.json() == {"error": "conflict", "retry_after_ms": 2000}
    assert len(stream_app.seen) == 1
