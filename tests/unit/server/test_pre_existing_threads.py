"""Unit tests for legacy (history-only) threads — W13.1, W13.2, W13.3."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.unit

AGENT_ID = "cuga-default"
OWNER = SimpleNamespace(sub="user-a")
OTHER = SimpleNamespace(sub="user-b")
LEGACY = "legacy-thread"


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
def legacy_app(threads_db, monkeypatch):
    """App with the legacy thread in history for user-a only (no thread record)."""
    from fastapi import FastAPI

    from cuga.backend.server import main

    history = MagicMock()

    def _owned(user_id):
        return user_id == "user-a"

    history.get_all_threads_for_agent = AsyncMock(
        side_effect=lambda agent_id, user_id: (
            [{"thread_id": LEGACY, "first_message": "hello", "updated_at": "2024-01-01T00:00:00"}]
            if _owned(user_id)
            else []
        )
    )
    history.get_latest_version = AsyncMock(
        side_effect=lambda agent_id, thread_id, user_id: 1 if _owned(user_id) and thread_id == LEGACY else 0
    )
    history.get_conversation = AsyncMock(
        return_value=SimpleNamespace(messages=[SimpleNamespace(model_dump=lambda: {"role": "user", "content": "hi"})])
    )
    history.delete_thread = AsyncMock(return_value=True)
    monkeypatch.setattr(main, "get_conversation_db", lambda: history)

    graph = MagicMock()
    graph.aget_state = AsyncMock()
    state = SimpleNamespace(
        knowledge_provider=None,
        knowledge_engine=None,
        current_llm=None,
        agent_id=AGENT_ID,
        output_format=None,
        agent=SimpleNamespace(graph=graph),
        stop_events={},
        checkpointer=None,
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
    monkeypatch.setattr(main, "_delete_session_knowledge_for_thread", AsyncMock())
    monkeypatch.setattr(main, "delete_thread_uploads", AsyncMock())
    monkeypatch.setattr(main.agent_registry, "is_agent_registry_enabled", lambda: False)

    app = FastAPI()
    app.state.app_state = state
    app.get("/api/conversations")(main.get_conversations)
    app.get("/api/conversation-messages/{thread_id}")(main.get_conversation_messages)
    app.delete("/api/conversations/{conversation_id}")(main.delete_conversation)
    app.post("/stream")(main.stream)
    app.post("/stop")(main.stop)
    app.post("/api/workspace/upload")(main.upload_workspace_file)
    app.get("/api/agent/state")(main.get_agent_state)
    app.get("/api/workspace/file")(main.get_workspace_file)
    app.dependency_overrides[main.require_chat_access] = lambda: OWNER
    return SimpleNamespace(app=app, main=main, history=history, graph=graph, seen=seen)


def _client(app):
    import httpx

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _as(legacy_app, user):
    legacy_app.app.dependency_overrides[legacy_app.main.require_chat_access] = lambda: user


async def _legacy_requests(client):
    """The requests that must 404 for a legacy thread (and for any non-owner)."""
    hdr = {"X-Thread-ID": LEGACY}
    return {
        "stream": await client.post("/stream", headers=hdr),
        "upload": await client.post(
            "/api/workspace/upload",
            params={"thread_id": LEGACY},
            files={"file": ("a.json", b"{}", "application/json")},
        ),
        "state": await client.get("/api/agent/state", headers=hdr),
        "stop": await client.post("/stop", headers=hdr),
    }


@pytest.mark.asyncio
async def test_listing_includes_legacy_thread_with_legacy_status(legacy_app):
    async with _client(legacy_app.app) as client:
        response = await client.get("/api/conversations")

    assert response.status_code == 200
    (item,) = response.json()
    assert item["id"] == LEGACY
    assert item["status"] == "legacy"
    assert item["pending_interrupt"] is False


@pytest.mark.asyncio
async def test_owner_reads_legacy_transcript(legacy_app):
    async with _client(legacy_app.app) as client:
        response = await client.get(f"/api/conversation-messages/{LEGACY}")

    assert response.status_code == 200
    assert response.json() == {"messages": [{"role": "user", "content": "hi"}]}


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["stream", "upload", "state", "stop"])
async def test_owner_mutating_operations_on_legacy_thread_are_404(legacy_app, operation):
    async with _client(legacy_app.app) as client:
        responses = await _legacy_requests(client)

    assert responses[operation].status_code == 404
    assert legacy_app.seen == []
    legacy_app.graph.aget_state.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["stream", "upload", "state", "stop"])
async def test_other_user_gets_404_everywhere(legacy_app, operation):
    _as(legacy_app, OTHER)
    async with _client(legacy_app.app) as client:
        responses = await _legacy_requests(client)
        transcript = await client.get(f"/api/conversation-messages/{LEGACY}")
        listing = await client.get("/api/conversations")
        delete = await client.delete(f"/api/conversations/{LEGACY}")

    assert responses[operation].status_code == 404
    assert transcript.json() == {"messages": []}
    assert listing.json() == []
    assert delete.status_code == 404
    legacy_app.history.delete_thread.assert_not_awaited()


@pytest.mark.asyncio
async def test_owner_deletes_legacy_thread(legacy_app):
    async with _client(legacy_app.app) as client:
        response = await client.delete(f"/api/conversations/{LEGACY}")

    assert response.status_code == 200
    assert response.json() == {"status": "success", "message": "Conversation deleted"}
    legacy_app.history.delete_thread.assert_awaited_once_with(AGENT_ID, LEGACY, "user-a")
    # Legacy delete removes only the caller's history rows: no unscoped cleanup.
    legacy_app.main._delete_session_knowledge_for_thread.assert_not_awaited()
    legacy_app.main.delete_thread_uploads.assert_not_awaited()


@pytest.mark.asyncio
async def test_workspace_read_on_shared_legacy_thread_is_404_for_both_users(legacy_app):
    """History from two users on one thread id: workspace reads 404 for both."""
    for user in (OWNER, OTHER):
        _as(legacy_app, user)
        async with _client(legacy_app.app) as client:
            response = await client.get(
                "/api/workspace/file", params={"path": "a.txt", "thread_id": LEGACY}
            )
        assert response.status_code == 404
