"""Unit tests for conversation_threads.py — W05.1 (schema) and W05.2 (repository API)."""

from __future__ import annotations

import pytest

from cuga.backend.storage.relational.local import LocalRelationalStore
from cuga.backend.server.conversation_threads import (
    DEFAULT_USER_ID,
    ConversationThreadsDB,
    Scope,
    ThreadIdConflict,
    ThreadStatus,
    get_conversation_threads_db,
    request_scope,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db(tmp_path) -> ConversationThreadsDB:
    store = LocalRelationalStore(str(tmp_path / "threads.db"))
    db = ConversationThreadsDB()
    db._get_store = lambda: store
    return db


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


# ---------------------------------------------------------------------------
# W05.1 — Schema idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_schema_idempotent(tmp_path):
    """Calling _ensure_schema twice on the same store must not raise."""
    db = _make_db(tmp_path)
    await db._ensure_schema()
    # Second call must be idempotent (CREATE TABLE IF NOT EXISTS)
    await db._ensure_schema()


@pytest.mark.asyncio
async def test_schema_table_exists_after_ensure(tmp_path):
    """After _ensure_schema the conversation_threads table must exist."""
    db = _make_db(tmp_path)
    await db._ensure_schema()
    store = db._get_store()
    rows = await store.fetchall(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='conversation_threads'"
    )
    assert len(rows) == 1
    rows_idx = await store.fetchall(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_ct_scope'"
    )
    assert len(rows_idx) == 1


# ---------------------------------------------------------------------------
# W05.2 — create / get_in_scope round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_and_get_in_scope(tmp_path):
    """create followed by get_in_scope returns the same record."""
    db = _make_db(tmp_path)
    scope = _scope()
    created = await db.create(scope, thread_id="t-001")

    assert created.thread_id == "t-001"
    assert created.tenant_id == "tenant-1"
    assert created.status == ThreadStatus.NEW
    assert created.pending_interrupt is False

    fetched = await db.get_in_scope(scope, "t-001")
    assert fetched is not None
    assert fetched.thread_id == "t-001"
    assert fetched.status == ThreadStatus.NEW


# ---------------------------------------------------------------------------
# W05.2 — scope isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wrong_user_returns_none(tmp_path):
    """get_in_scope with a different user_id must return None."""
    db = _make_db(tmp_path)
    scope = _scope()
    await db.create(scope, thread_id="t-002")

    wrong_scope = _scope(user_id="other-user")
    result = await db.get_in_scope(wrong_scope, "t-002")
    assert result is None


@pytest.mark.asyncio
async def test_wrong_agent_returns_none(tmp_path):
    """get_in_scope with a different agent_id must return None."""
    db = _make_db(tmp_path)
    scope = _scope()
    await db.create(scope, thread_id="t-003")

    wrong_scope = _scope(agent_id="other-agent")
    result = await db.get_in_scope(wrong_scope, "t-003")
    assert result is None


@pytest.mark.asyncio
async def test_wrong_tenant_returns_none(tmp_path):
    """get_in_scope with a different tenant_id must return None."""
    db = _make_db(tmp_path)
    scope = _scope()
    await db.create(scope, thread_id="t-004")

    wrong_scope = _scope(tenant_id="other-tenant")
    result = await db.get_in_scope(wrong_scope, "t-004")
    assert result is None


# ---------------------------------------------------------------------------
# W05.2 — ThreadIdConflict on duplicate primary key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_thread_id_raises_conflict(tmp_path):
    """Inserting the same thread_id (even in a different scope) raises ThreadIdConflict."""
    db = _make_db(tmp_path)
    scope_a = _scope(user_id="user-a")
    await db.create(scope_a, thread_id="t-dup")

    scope_b = _scope(user_id="user-b")
    with pytest.raises(ThreadIdConflict):
        await db.create(scope_b, thread_id="t-dup")


# ---------------------------------------------------------------------------
# W05.2 — mark_turn_committed (new → active)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_turn_committed_transitions_new_to_active(tmp_path):
    """mark_turn_committed must change status from new to active and update fields."""
    db = _make_db(tmp_path)
    scope = _scope()
    await db.create(scope, thread_id="t-005")

    await db.mark_turn_committed(
        scope,
        "t-005",
        checkpoint_id="ckpt-1",
        agent_hash="hash-abc",
        runtime_ver="1.2.3",
        pending_interrupt=True,
    )

    record = await db.get_in_scope(scope, "t-005")
    assert record is not None
    assert record.status == ThreadStatus.ACTIVE
    assert record.latest_checkpoint_id == "ckpt-1"
    assert record.config_agent_hash == "hash-abc"
    assert record.config_runtime_ver == "1.2.3"
    assert record.pending_interrupt is True


# ---------------------------------------------------------------------------
# W05.2 — deleted rows not returned by get_in_scope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deleted_rows_not_returned_by_get_in_scope(tmp_path):
    """get_in_scope must exclude rows with status=deleted."""
    db = _make_db(tmp_path)
    scope = _scope()
    await db.create(scope, thread_id="t-006")

    await db.mark_deleted(scope, "t-006")

    result = await db.get_in_scope(scope, "t-006")
    assert result is None


# ---------------------------------------------------------------------------
# W05.2 — list_for_scope excludes deleted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_for_scope_excludes_deleted(tmp_path):
    """list_for_scope must not return deleted threads."""
    db = _make_db(tmp_path)
    scope = _scope()
    await db.create(scope, thread_id="t-live")
    await db.create(scope, thread_id="t-gone")
    await db.mark_deleted(scope, "t-gone")

    records = await db.list_for_scope(scope)
    ids = [r.thread_id for r in records]
    assert "t-live" in ids
    assert "t-gone" not in ids


# ---------------------------------------------------------------------------
# W05.2 — mark_projected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_projected_stores_checkpoint_id(tmp_path):
    """mark_projected must update the projected_checkpoint_id field."""
    db = _make_db(tmp_path)
    scope = _scope()
    await db.create(scope, thread_id="t-007")

    await db.mark_projected(scope, "t-007", checkpoint_id="proj-ckpt-1")

    record = await db.get_in_scope(scope, "t-007")
    assert record is not None
    assert record.projected_checkpoint_id == "proj-ckpt-1"


# ---------------------------------------------------------------------------
# W05.2 — singleton
# ---------------------------------------------------------------------------


def test_get_conversation_threads_db_singleton():
    """get_conversation_threads_db must return the same instance on repeated calls."""
    db1 = get_conversation_threads_db()
    db2 = get_conversation_threads_db()
    assert db1 is db2


# ---------------------------------------------------------------------------
# W05.3 — Scope and request_scope helper
# ---------------------------------------------------------------------------


def test_request_scope_with_authenticated_user(monkeypatch):
    """A mock current_user with .sub produces a Scope with that user_id."""
    monkeypatch.setattr("cuga.config.get_tenant_id", lambda: "tenant-auth")
    monkeypatch.setattr("cuga.config.get_service_instance_id", lambda: "instance-auth")

    class MockUser:
        sub = "user-123"

    scope = request_scope(MockUser(), agent_id="agent-custom")
    assert scope == Scope(
        tenant_id="tenant-auth",
        instance_id="instance-auth",
        agent_id="agent-custom",
        user_id="user-123",
    )


def test_request_scope_with_none_user(monkeypatch):
    """current_user=None produces a Scope with user_id=DEFAULT_USER_ID."""
    monkeypatch.setattr("cuga.config.get_tenant_id", lambda: "tenant-anon")
    monkeypatch.setattr("cuga.config.get_service_instance_id", lambda: "instance-anon")

    scope = request_scope(None, agent_id="cuga-default")
    assert scope == Scope(
        tenant_id="tenant-anon",
        instance_id="instance-anon",
        agent_id="cuga-default",
        user_id=DEFAULT_USER_ID,
    )


def test_request_scope_client_headers_cannot_influence_scope(monkeypatch):
    """Client headers (e.g. X-Tenant-ID) cannot affect the Scope; only get_tenant_id() matters."""
    monkeypatch.setattr("cuga.config.get_tenant_id", lambda: "server-tenant-id")
    monkeypatch.setattr("cuga.config.get_service_instance_id", lambda: "server-instance-id")

    monkeypatch.setenv("HTTP_X_TENANT_ID", "attacker-tenant")
    monkeypatch.setenv("X_TENANT_ID", "attacker-tenant")

    class MockUser:
        sub = "legit-user"

    scope = request_scope(MockUser(), agent_id="sales-agent")
    assert scope.tenant_id == "server-tenant-id"
    assert scope.instance_id == "server-instance-id"
    assert scope.user_id == "legit-user"
    assert scope.agent_id == "sales-agent"


def test_request_scope_agent_id_preserved(monkeypatch):
    """The agent_id passed in is used unchanged."""
    monkeypatch.setattr("cuga.config.get_tenant_id", lambda: "tenant-x")
    monkeypatch.setattr("cuga.config.get_service_instance_id", lambda: "instance-x")

    scope = request_scope(None, agent_id="arbitrary-agent-id-123")
    assert scope.agent_id == "arbitrary-agent-id-123"


def test_scope_is_frozen():
    """Scope is immutable (@dataclass(frozen=True))."""
    s = Scope(tenant_id="t", instance_id="i", agent_id="a", user_id="u")
    with pytest.raises(Exception):
        s.user_id = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# W05.4 — POST /api/conversations creates a real thread record
# ---------------------------------------------------------------------------


def test_post_conversations_creates_record_in_callers_scope(tmp_path, monkeypatch):
    """POST /api/conversations persists a 'new' record under the caller's scope."""
    import asyncio
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from cuga.backend.server import main

    db = _make_db(tmp_path)
    monkeypatch.setattr("cuga.backend.server.conversation_threads.get_conversation_threads_db", lambda: db)
    user = SimpleNamespace(sub="user-create")
    app = FastAPI()
    app.post("/api/conversations")(main.create_conversation)
    app.dependency_overrides[main.require_chat_access] = lambda: user

    with TestClient(app) as client:
        response = client.post("/api/conversations", json={"title": "Hello"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "new"
    assert body["title"] == "Hello"
    assert body["preview"] == ""
    record = asyncio.run(db.get_in_scope(request_scope(user, "cuga-default"), body["id"]))
    assert record is not None
    assert record.status == ThreadStatus.NEW
    assert record.user_id == "user-create"
    # Another user cannot see it.
    other = asyncio.run(db.get_in_scope(request_scope(SimpleNamespace(sub="someone-else"), "cuga-default"), body["id"]))
    assert other is None


def test_post_conversations_error_does_not_leak_exception_text(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from cuga.backend.server import main

    class BrokenDB:
        async def create(self, scope, thread_id):
            raise RuntimeError("secret-db-detail")

    monkeypatch.setattr(
        "cuga.backend.server.conversation_threads.get_conversation_threads_db", lambda: BrokenDB()
    )
    app = FastAPI()
    app.post("/api/conversations")(main.create_conversation)
    app.dependency_overrides[main.require_chat_access] = lambda: None

    with TestClient(app) as client:
        response = client.post("/api/conversations", json={})

    assert response.status_code == 500
    assert response.json() == {"detail": "Failed to create conversation"}
    assert "secret-db-detail" not in response.text


# ---------------------------------------------------------------------------
# W05.5 — /stream creates a record only when X-Thread-ID is absent
# ---------------------------------------------------------------------------


def _stream_client_app(tmp_path, monkeypatch, user):
    from types import SimpleNamespace

    from unittest.mock import AsyncMock, MagicMock

    from fastapi import FastAPI

    from cuga.backend.server import main

    db = _make_db(tmp_path)
    monkeypatch.setattr("cuga.backend.server.conversation_threads.get_conversation_threads_db", lambda: db)
    state = SimpleNamespace(
        knowledge_provider=None,
        knowledge_engine=None,
        current_llm=None,
        agent_id="cuga-default",
        output_format=None,
        agent=SimpleNamespace(graph=MagicMock()),
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
    app.dependency_overrides[main.require_chat_access] = lambda: user
    return app, db, seen


def test_stream_without_thread_id_creates_record(tmp_path, monkeypatch):
    """No X-Thread-ID: /stream issues an ID, persists a 'new' record, and passes it to event_stream."""
    import asyncio
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    user = SimpleNamespace(sub="user-stream")
    app, db, seen = _stream_client_app(tmp_path, monkeypatch, user)

    with TestClient(app) as client:
        response = client.post("/stream")

    assert response.status_code == 200, response.text
    issued = seen[0]["thread_id"]
    assert seen[0]["issued_thread_id"] == issued
    record = asyncio.run(db.get_in_scope(request_scope(user, "cuga-default"), issued))
    assert record is not None
    assert record.status == ThreadStatus.NEW
    assert record.user_id == "user-stream"


def test_stream_unknown_thread_id_is_404_and_creates_no_record(tmp_path, monkeypatch):
    """A client-supplied unknown X-Thread-ID is 404 and never creates a record."""
    import asyncio
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    user = SimpleNamespace(sub="user-stream")
    app, db, seen = _stream_client_app(tmp_path, monkeypatch, user)

    with TestClient(app) as client:
        response = client.post("/stream", headers={"X-Thread-ID": "unknown-id"})

    assert response.status_code == 404
    assert seen == []
    assert asyncio.run(db.get_in_scope(request_scope(user, "cuga-default"), "unknown-id")) is None


# ---------------------------------------------------------------------------
# W05.6 — GET /api/conversations returns status + pending_interrupt
# ---------------------------------------------------------------------------


def test_get_conversations_returns_status_and_legacy(tmp_path, monkeypatch):
    """Mixed listing: active thread has record → 'active'; history-only thread → 'legacy'.
    No new record is created for the legacy thread.
    """
    import asyncio
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from cuga.backend.server import main

    # Build an in-memory ConversationThreadsDB
    db = _make_db(tmp_path)
    monkeypatch.setattr("cuga.backend.server.conversation_threads.get_conversation_threads_db", lambda: db)

    # Patch config accessors so request_scope works
    monkeypatch.setattr("cuga.config.get_tenant_id", lambda: "tenant-1")
    monkeypatch.setattr("cuga.config.get_service_instance_id", lambda: "inst-1")

    user = SimpleNamespace(sub="user-listing")
    scope = _scope(tenant_id="tenant-1", instance_id="inst-1", agent_id="cuga-default", user_id="user-listing")

    # Create a record for thread "t-active" (status will be 'active' after mark_turn_committed)
    asyncio.run(db.create(scope, "t-active"))
    asyncio.run(
        db.mark_turn_committed(
            scope, "t-active",
            checkpoint_id="ckpt-x",
            agent_hash="",
            runtime_ver="",
            pending_interrupt=False,
        )
    )
    # "t-legacy" has no record at all

    # Stub conversation_db.get_all_threads_for_agent
    class FakeConvDB:
        async def get_all_threads_for_agent(self, agent_id, user_id):
            return [
                {"thread_id": "t-active", "first_message": "Hello", "updated_at": "2024-01-01T00:00:00"},
                {"thread_id": "t-legacy", "first_message": "Old msg", "updated_at": "2023-06-01T00:00:00"},
            ]

    monkeypatch.setattr(main, "get_conversation_db", lambda: FakeConvDB())

    app_under_test = FastAPI()
    app_under_test.get("/api/conversations")(main.get_conversations)
    app_under_test.dependency_overrides[main.require_chat_access] = lambda: user

    with TestClient(app_under_test) as client:
        response = client.get("/api/conversations")

    assert response.status_code == 200, response.text
    data = response.json()
    by_id = {item["id"]: item for item in data}

    assert by_id["t-active"]["status"] == ThreadStatus.ACTIVE
    assert by_id["t-active"]["pending_interrupt"] is False

    assert by_id["t-legacy"]["status"] == "legacy"
    assert by_id["t-legacy"]["pending_interrupt"] is False

    # No new record was created for the legacy thread
    record = asyncio.run(db.get_in_scope(scope, "t-legacy"))
    assert record is None
