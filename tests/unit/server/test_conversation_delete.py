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
    lease.is_active = AsyncMock(return_value=False)
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
    expected_key = delete_app.main._checkpoint_thread_id("thread-a", "user-a", AGENT_ID)
    delete_app.lease.request_stop.assert_awaited_once_with(expected_key)


@pytest.mark.asyncio
async def test_checkpoint_failure_is_logged_and_does_not_fail_delete(threads_db, delete_app, monkeypatch):
    """A failing adelete_thread is logged (no exception text in the response) and delete completes."""
    await _create(threads_db)
    saver = _make_saver()
    saver.adelete_thread = AsyncMock(side_effect=RuntimeError("boom-secret"))
    delete_app.app.state.app_state.checkpointer = saver
    errors: list[str] = []
    monkeypatch.setattr(delete_app.main.logger, "exception", lambda msg, *a, **k: errors.append(str(msg)))

    async with _client(delete_app.app) as client:
        response = await client.delete("/api/conversations/thread-a")

    assert response.status_code == 200
    assert "boom-secret" not in response.text
    assert any("parent checkpoint cleanup failed" in e for e in errors)
    delete_app.history.delete_thread.assert_awaited_once()


@pytest.mark.asyncio
async def test_checkpoint_failure_still_completes_mark_deleted(threads_db, delete_app):
    """Step 1 (mark_deleted_and_enqueue) has succeeded even when checkpoint deletion fails."""
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


# ---------------------------------------------------------------------------
# W12.1 — checkpoint_delete_outbox: durable intent and retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outbox_row_created_atomically_with_mark_deleted(threads_db, delete_app):
    """delete_conversation writes the outbox entry in the same step as mark_deleted."""
    await _create(threads_db)
    # Even with no saver, the outbox row must appear.
    delete_app.app.state.app_state.checkpointer = None

    async with _client(delete_app.app) as client:
        response = await client.delete("/api/conversations/thread-a")

    assert response.status_code == 200
    pending = await threads_db.pending_checkpoint_deletes()
    # Row is present even though there was nothing to delete.
    assert len(pending) == 1
    expected_key = delete_app.main._checkpoint_thread_id("thread-a", "user-a", AGENT_ID)
    assert pending[0]["scoped_checkpoint_id"] == expected_key


@pytest.mark.asyncio
async def test_outbox_row_acknowledged_when_inline_delete_succeeds(threads_db, delete_app):
    """After a successful adelete_thread the outbox row is removed immediately."""
    await _create(threads_db)
    saver = _make_saver()
    delete_app.app.state.app_state.checkpointer = saver

    async with _client(delete_app.app) as client:
        response = await client.delete("/api/conversations/thread-a")

    assert response.status_code == 200
    pending = await threads_db.pending_checkpoint_deletes()
    assert pending == [], "outbox should be empty after successful inline delete"


@pytest.mark.asyncio
async def test_outbox_row_remains_when_inline_delete_fails(threads_db, delete_app):
    """When adelete_thread fails the outbox row stays for the worker to retry."""
    await _create(threads_db)
    saver = _make_saver()
    saver.adelete_thread = AsyncMock(side_effect=RuntimeError("disk full"))
    delete_app.app.state.app_state.checkpointer = saver

    async with _client(delete_app.app) as client:
        response = await client.delete("/api/conversations/thread-a")

    assert response.status_code == 200
    pending = await threads_db.pending_checkpoint_deletes()
    assert len(pending) == 1
    expected_key = delete_app.main._checkpoint_thread_id("thread-a", "user-a", AGENT_ID)
    assert pending[0]["scoped_checkpoint_id"] == expected_key


@pytest.mark.asyncio
async def test_retry_consumer_acks_on_success(threads_db, delete_app):
    """Retry consumer deletes the checkpoint and removes the outbox row."""
    from cuga.backend.server import main as main_mod

    await _create(threads_db)
    expected_key = main_mod._checkpoint_thread_id("thread-a", "user-a", AGENT_ID)
    await threads_db.enqueue_checkpoint_delete(expected_key)

    saver = _make_saver()
    delete_app.app.state.app_state.checkpointer = saver

    await main_mod._retry_checkpoint_deletes(delete_app.app.state.app_state)

    saver.adelete_thread.assert_awaited_once_with(expected_key)
    pending = await threads_db.pending_checkpoint_deletes()
    assert pending == [], "outbox should be empty after successful retry"


@pytest.mark.asyncio
async def test_two_consecutive_saver_failures_then_recovery_without_new_delete(threads_db, delete_app):
    """Row survives two consecutive saver failures and is acked on the third attempt — no new DELETE needed."""
    from cuga.backend.server import main as main_mod

    await _create(threads_db)
    expected_key = main_mod._checkpoint_thread_id("thread-a", "user-a", AGENT_ID)
    await threads_db.enqueue_checkpoint_delete(expected_key)

    saver = _make_saver()
    saver.adelete_thread = AsyncMock(side_effect=RuntimeError("still down"))
    delete_app.app.state.app_state.checkpointer = saver

    # First failure
    await main_mod._retry_checkpoint_deletes(delete_app.app.state.app_state)
    assert len(await threads_db.pending_checkpoint_deletes()) == 1, "row must remain after first failure"

    # Second failure
    await main_mod._retry_checkpoint_deletes(delete_app.app.state.app_state)
    assert len(await threads_db.pending_checkpoint_deletes()) == 1, "row must remain after second failure"

    # Saver recovers — third attempt succeeds
    saver.adelete_thread = AsyncMock()
    await main_mod._retry_checkpoint_deletes(delete_app.app.state.app_state)
    assert await threads_db.pending_checkpoint_deletes() == [], "outbox must be empty after recovery"
    saver.adelete_thread.assert_awaited_once_with(expected_key)


@pytest.mark.asyncio
async def test_retry_consumer_leaves_row_on_failure(threads_db, delete_app):
    """When retry also fails the outbox row remains for the next cycle."""
    from cuga.backend.server import main as main_mod

    await _create(threads_db)
    expected_key = main_mod._checkpoint_thread_id("thread-a", "user-a", AGENT_ID)
    await threads_db.enqueue_checkpoint_delete(expected_key)

    saver = _make_saver()
    saver.adelete_thread = AsyncMock(side_effect=RuntimeError("still down"))
    delete_app.app.state.app_state.checkpointer = saver

    await main_mod._retry_checkpoint_deletes(delete_app.app.state.app_state)

    pending = await threads_db.pending_checkpoint_deletes()
    assert len(pending) == 1, "row must remain when retry fails"
    assert pending[0]["scoped_checkpoint_id"] == expected_key


@pytest.mark.asyncio
async def test_child_failure_retries_every_cleanup_stage(threads_db, delete_app, monkeypatch):
    """A child failure keeps the whole cleanup intent until a worker pass succeeds."""
    await _create(threads_db)
    monkeypatch.setattr(
        delete_app.main, "settings", SimpleNamespace(supervisor=SimpleNamespace(enabled=True))
    )
    saver = _make_saver(child_ids=("child-a",))
    saver.adelete_thread = AsyncMock(side_effect=[None, RuntimeError("child unavailable"), None, None])
    delete_app.app.state.app_state.checkpointer = saver

    async with _client(delete_app.app) as client:
        response = await client.delete("/api/conversations/thread-a")

    assert response.status_code == 200
    pending = await threads_db.pending_checkpoint_deletes()
    assert len(pending) == 1
    assert '"delete_children": true' in pending[0]["cleanup_context"]
    assert delete_app.history.delete_thread.await_count == 1

    await delete_app.main._retry_checkpoint_deletes(delete_app.app.state.app_state)

    assert await threads_db.pending_checkpoint_deletes() == []
    assert delete_app.history.delete_thread.await_count == 2
    assert [call.args[0] for call in saver.adelete_thread.await_args_list].count("child-a") == 2


@pytest.mark.asyncio
async def test_history_false_result_and_upload_failure_are_retried(threads_db, delete_app, monkeypatch):
    """A nonthrowing history failure and upload exception both preserve the intent."""
    await _create(threads_db)
    saver = _make_saver()
    delete_app.app.state.app_state.checkpointer = saver
    delete_app.history.delete_thread = AsyncMock(side_effect=[False, True, True])
    uploads = AsyncMock(side_effect=[RuntimeError("storage offline"), None, None])
    monkeypatch.setattr(delete_app.main, "delete_thread_uploads", uploads)

    async with _client(delete_app.app) as client:
        response = await client.delete("/api/conversations/thread-a")

    assert response.status_code == 200
    assert len(await threads_db.pending_checkpoint_deletes()) == 1
    await delete_app.main._retry_checkpoint_deletes(delete_app.app.state.app_state)
    assert await threads_db.pending_checkpoint_deletes() == []
    assert uploads.await_count == 2
    assert delete_app.history.delete_thread.await_count == 2


@pytest.mark.asyncio
async def test_knowledge_failure_is_retried(threads_db, delete_app, monkeypatch):
    await _create(threads_db)
    delete_app.app.state.app_state.checkpointer = _make_saver()
    knowledge = AsyncMock(side_effect=[RuntimeError("knowledge offline"), None])
    monkeypatch.setattr(delete_app.main, "_delete_session_knowledge_for_thread", knowledge)

    async with _client(delete_app.app) as client:
        response = await client.delete("/api/conversations/thread-a")

    assert response.status_code == 200
    assert len(await threads_db.pending_checkpoint_deletes()) == 1
    await delete_app.main._retry_checkpoint_deletes(delete_app.app.state.app_state)
    assert await threads_db.pending_checkpoint_deletes() == []
    assert knowledge.await_count == 2


@pytest.mark.asyncio
async def test_live_lease_defers_checkpoint_cleanup_until_released(threads_db, delete_app):
    await _create(threads_db)
    saver = _make_saver()
    delete_app.app.state.app_state.checkpointer = saver
    delete_app.lease.is_active = AsyncMock(side_effect=[True, False])

    async with _client(delete_app.app) as client:
        response = await client.delete("/api/conversations/thread-a")

    assert response.status_code == 200
    saver.adelete_thread.assert_not_awaited()
    assert len(await threads_db.pending_checkpoint_deletes()) == 1

    await delete_app.main._retry_checkpoint_deletes(delete_app.app.state.app_state)
    assert await threads_db.pending_checkpoint_deletes() == []
    saver.adelete_thread.assert_awaited_once()


# ── An upload authorized before deletion cannot outlive its cleanup ─────────


def _paused_upload_app(delete_app, monkeypatch):
    """Mount the upload route with a write that waits until ``release`` is set."""
    import asyncio

    main = delete_app.main
    started, release = asyncio.Event(), asyncio.Event()
    written = []

    async def _write(tid, filename, content):
        started.set()
        await release.wait()
        written.append(tid)
        return {"path": f"workspace/uploads/{filename}", "size_bytes": len(content)}

    monkeypatch.setattr(main, "upload_workspace_bytes", _write)
    monkeypatch.setattr(main, "workspace_tree_is_sandbox_backed", lambda: True)
    delete_app.app.post("/api/workspace/upload")(main.upload_workspace_file)
    return started, release, written


async def _upload(client):
    return await client.post(
        "/api/workspace/upload",
        params={"thread_id": "thread-a", "agent_id": AGENT_ID},
        files={"file": ("data.json", b'{"a": 1}', "application/json")},
    )


@pytest.mark.asyncio
async def test_upload_finishing_after_delete_queues_and_runs_cleanup(threads_db, delete_app, monkeypatch):
    import asyncio

    await _create(threads_db)
    delete_app.app.state.app_state.checkpointer = _make_saver()
    uploads = AsyncMock()
    monkeypatch.setattr(delete_app.main, "delete_thread_uploads", uploads)
    started, release, written = _paused_upload_app(delete_app, monkeypatch)

    async with _client(delete_app.app) as client:
        upload = asyncio.create_task(_upload(client))
        await started.wait()  # authorized, now writing
        assert (await client.delete("/api/conversations/thread-a")).status_code == 200
        assert uploads.await_count == 1
        assert await threads_db.pending_checkpoint_deletes() == []

        release.set()
        response = await upload

    assert written == ["thread-a"]
    assert response.status_code == 404
    assert uploads.await_count == 2, "the late write must be cleaned up again"
    assert await threads_db.pending_checkpoint_deletes() == []


@pytest.mark.asyncio
async def test_failed_cleanup_after_late_upload_stays_queued(threads_db, delete_app, monkeypatch):
    """The delete route acknowledges only its own event, so the cleanup that the
    late upload queued survives even when the delete finishes afterwards."""
    import asyncio

    await _create(threads_db)
    delete_app.app.state.app_state.checkpointer = _make_saver()
    started, release, _ = _paused_upload_app(delete_app, monkeypatch)
    upload_done = asyncio.Event()
    calls = []

    async def _delete_uploads(tid):
        calls.append(tid)
        if len(calls) == 1:
            # Delete's own cleanup: let the upload finish (and queue its
            # cleanup) before the delete route acknowledges.
            release.set()
            await upload_done.wait()
            return None
        raise RuntimeError("sandbox service offline")  # the upload's cleanup

    monkeypatch.setattr(delete_app.main, "delete_thread_uploads", _delete_uploads)

    async with _client(delete_app.app) as client:
        upload = asyncio.create_task(_upload(client))
        await started.wait()

        async def _finish_upload():
            response = await upload
            upload_done.set()
            return response

        upload_response, delete_response = await asyncio.gather(
            _finish_upload(), client.delete("/api/conversations/thread-a")
        )

    assert upload_response.status_code == 404
    assert delete_response.status_code == 200
    pending = await threads_db.pending_checkpoint_deletes()
    assert len(pending) == 1
    assert '"thread_id": "thread-a"' in pending[0]["cleanup_context"]


@pytest.mark.asyncio
async def test_upload_before_delete_needs_no_extra_cleanup(threads_db, delete_app, monkeypatch):
    await _create(threads_db)
    uploads = AsyncMock()
    monkeypatch.setattr(delete_app.main, "delete_thread_uploads", uploads)
    _, release, written = _paused_upload_app(delete_app, monkeypatch)
    release.set()

    async with _client(delete_app.app) as client:
        response = await _upload(client)

    assert response.status_code == 200
    assert written == ["thread-a"]
    uploads.assert_not_awaited()
    assert await threads_db.pending_checkpoint_deletes() == []
