"""Unit tests for DELETE /api/conversations/{id} — W12.1 (checkpoints) and W12.6 (child threads)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from cuga.backend.server.conversation_threads import request_scope

pytestmark = pytest.mark.unit

AGENT_ID = "cuga-default"
OWNER = SimpleNamespace(sub="user-a")


@pytest.fixture
def threads_db(tmp_path, monkeypatch):
    from cuga.backend.server.conversation_threads import ConversationThreadsDB
    from cuga.backend.storage.relational.local import LocalRelationalStore

    store = LocalRelationalStore(str(tmp_path / "threads.db"))
    db = ConversationThreadsDB()
    db._get_store = lambda: store
    monkeypatch.setattr("cuga.backend.server.conversation_threads.get_conversation_threads_db", lambda: db)
    return db


def _make_saver(child_ids=()):
    saver = MagicMock()
    saver.adelete_thread = AsyncMock()

    async def _alist(config, *, filter=None, **kwargs):
        for cid in child_ids:
            yield SimpleNamespace(config={"configurable": {"thread_id": cid}})

    saver.alist = MagicMock(side_effect=_alist)
    return saver


@pytest.fixture
def delete_app(threads_db, monkeypatch):
    from fastapi import FastAPI

    from cuga.backend.server import main

    history = MagicMock()
    history.get_latest_version = AsyncMock(return_value=0)
    history.delete_thread = AsyncMock(return_value=True)
    lease = MagicMock()
    lease.request_stop = AsyncMock(return_value=False)
    monkeypatch.setattr(main, "get_conversation_db", lambda: history)
    monkeypatch.setattr("cuga.backend.server.thread_lease.get_thread_lease", lambda: lease)
    monkeypatch.setattr(main, "_delete_session_knowledge_for_thread", AsyncMock())
    monkeypatch.setattr(main, "delete_thread_uploads", AsyncMock())
    monkeypatch.setattr(main.agent_registry, "is_agent_registry_enabled", lambda: False)
    monkeypatch.setattr(main, "settings", SimpleNamespace(supervisor=SimpleNamespace(enabled=False)))

    app = FastAPI()
    app.state.app_state = SimpleNamespace(checkpointer=None)
    app.delete("/api/conversations/{conversation_id}")(main.delete_conversation)
    app.dependency_overrides[main.require_chat_access] = lambda: OWNER
    return SimpleNamespace(app=app, main=main, history=history, lease=lease)


def _client(app):
    import httpx

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _create(threads_db, thread_id="thread-a"):
    return await threads_db.create(request_scope(OWNER, AGENT_ID), thread_id)


# ---------------------------------------------------------------------------
# W12.1
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_removes_checkpoint_for_thread(threads_db, delete_app):
    """The saver's adelete_thread is called with the thread's scoped checkpoint key."""
    await _create(threads_db)
    saver = _make_saver()
    delete_app.app.state.app_state.checkpointer = saver

    async with _client(delete_app.app) as client:
        response = await client.delete("/api/conversations/thread-a")

    assert response.status_code == 200
    expected_key = delete_app.main._checkpoint_thread_id("thread-a", "user-a", AGENT_ID)
    saver.adelete_thread.assert_awaited_once_with(expected_key)
    delete_app.history.delete_thread.assert_awaited_once_with(AGENT_ID, "thread-a", "user-a")


@pytest.mark.asyncio
async def test_delete_marks_thread_deleted_so_guard_returns_404(threads_db, delete_app):
    """After delete, resolve_owned_thread raises 404 for the same owner."""
    from cuga.backend.server.thread_guard import ThreadNotFound, resolve_owned_thread

    await _create(threads_db)
    scope = request_scope(OWNER, AGENT_ID)
    assert await resolve_owned_thread(scope, "thread-a")

    async with _client(delete_app.app) as client:
        response = await client.delete("/api/conversations/thread-a")

    assert response.status_code == 200
    with pytest.raises(ThreadNotFound):
        await resolve_owned_thread(scope, "thread-a")
    delete_app.lease.request_stop.assert_awaited_once_with("thread-a")


@pytest.mark.asyncio
async def test_checkpoint_failure_is_logged_and_does_not_fail_delete(threads_db, delete_app, monkeypatch):
    """A failing adelete_thread is logged (no exception text in the response) and delete completes."""
    await _create(threads_db)
    saver = _make_saver()
    saver.adelete_thread = AsyncMock(side_effect=RuntimeError("boom-secret"))
    delete_app.app.state.app_state.checkpointer = saver
    errors: list[str] = []
    monkeypatch.setattr(delete_app.main.logger, "error", lambda msg, *a, **k: errors.append(str(msg)))

    async with _client(delete_app.app) as client:
        response = await client.delete("/api/conversations/thread-a")

    assert response.status_code == 200
    assert "boom-secret" not in response.text
    assert any("checkpoint deletion failed" in e for e in errors)
    delete_app.history.delete_thread.assert_awaited_once()


@pytest.mark.asyncio
async def test_checkpoint_failure_still_completes_mark_deleted(threads_db, delete_app):
    """Step 1 (mark_deleted) has succeeded even when checkpoint deletion fails."""
    await _create(threads_db)
    saver = _make_saver()
    saver.adelete_thread = AsyncMock(side_effect=RuntimeError("boom"))
    delete_app.app.state.app_state.checkpointer = saver

    async with _client(delete_app.app) as client:
        await client.delete("/api/conversations/thread-a")

    assert await threads_db.get_in_scope(request_scope(OWNER, AGENT_ID), "thread-a") is None


@pytest.mark.asyncio
async def test_delete_unknown_thread_is_404(threads_db, delete_app):
    async with _client(delete_app.app) as client:
        response = await client.delete("/api/conversations/never-existed")

    assert response.status_code == 404
    delete_app.history.delete_thread.assert_not_awaited()


# ---------------------------------------------------------------------------
# W12.6
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_parent_also_deletes_child_threads(threads_db, delete_app, monkeypatch):
    """With delegation enabled, children tagged with the parent id are deleted too."""
    await _create(threads_db)
    monkeypatch.setattr(
        delete_app.main, "settings", SimpleNamespace(supervisor=SimpleNamespace(enabled=True))
    )
    saver = _make_saver(child_ids=("sup_child_1", "sup_child_2", "sup_child_1"))
    delete_app.app.state.app_state.checkpointer = saver

    async with _client(delete_app.app) as client:
        response = await client.delete("/api/conversations/thread-a")

    assert response.status_code == 200
    assert saver.alist.call_args.kwargs["filter"] == {"cuga_parent_thread_id": "thread-a"}
    deleted = [c.args[0] for c in saver.adelete_thread.await_args_list]
    parent_key = delete_app.main._checkpoint_thread_id("thread-a", "user-a", AGENT_ID)
    assert deleted[0] == parent_key
    assert sorted(deleted[1:]) == ["sup_child_1", "sup_child_2"]


@pytest.mark.asyncio
async def test_single_agent_delete_does_not_list_children(threads_db, delete_app):
    """A graph that cannot delegate never runs the child lookup."""
    await _create(threads_db)
    saver = _make_saver(child_ids=("sup_child_1",))
    delete_app.app.state.app_state.checkpointer = saver

    async with _client(delete_app.app) as client:
        response = await client.delete("/api/conversations/thread-a")

    assert response.status_code == 200
    saver.alist.assert_not_called()
    assert saver.adelete_thread.await_count == 1
