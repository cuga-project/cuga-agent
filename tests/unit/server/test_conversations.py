"""Continuing a conversation over ``/stream`` with server-issued IDs (#774, design §6, §11.1, §12)."""

import asyncio
import uuid
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.unit


def _events(response) -> list[str]:
    return [line.split(":", 1)[1].strip() for line in response.text.splitlines() if line.startswith("event:")]


async def test_a_created_conversation_continues_on_its_id(server):
    thread_id = await server.create()

    for query in ("first", "second"):
        response = await server.send(thread_id, query)
        assert response.status_code == 200, response.text
        assert "Error" not in _events(response)

    snapshot = await server.graph.aget_state({"configurable": {"thread_id": server.key(thread_id)}})
    assert snapshot.values["url"] == "2"  # the second turn continued from the first one's state
    assert await server.db.get_thread_status(thread_id, "alice", "cuga-default") == "active"


async def test_stream_without_a_thread_id_creates_one_and_announces_it_first(server):
    response = await server.send(None)

    assert response.status_code == 200, response.text
    assert _events(response)[0] == "thread_created"
    rows = await server.db._get_store().fetchall("SELECT thread_id, user_id FROM runtime_thread_owners")
    assert len(rows) == 1 and rows[0]["user_id"] == "alice"
    assert rows[0]["thread_id"] in response.text.split("event: thread_created", 1)[1].split("event:", 1)[0]


@pytest.mark.parametrize(
    "case", ["unknown", "other_user", "legacy", "deleted", "lookup_error", "fresh_other_user"]
)
async def test_threads_the_caller_cannot_continue_all_get_the_same_404(server, monkeypatch, case):
    thread_id = str(uuid.uuid4())
    as_user = "alice"
    if case in ("other_user", "fresh_other_user"):
        thread_id = await server.create()
        if case == "other_user":
            assert (await server.send(thread_id)).status_code == 200
        as_user = "bob"  # a brand-new thread with no turns yet is protected too
    elif case == "legacy":
        await server.db.save_conversation("cuga-default", thread_id, 1, "alice", [])
    elif case == "deleted":
        thread_id = await server.create()
        assert (await server.client.delete(f"/api/conversations/{thread_id}")).status_code == 200
    elif case == "lookup_error":
        monkeypatch.setattr(server.db, "get_thread_status", AsyncMock(side_effect=RuntimeError("db down")))
    reference = await server.send(str(uuid.uuid4()))
    turns_before = list(server.turns)

    response = await server.send(thread_id, "attack", as_user=as_user)

    assert response.status_code == 404
    assert response.content == reference.content
    assert server.turns == turns_before


async def test_two_sends_at_once_give_one_turn_and_one_conflict(server):
    thread_id = await server.create()
    gate = server.hold_turns()
    first = asyncio.create_task(server.send(thread_id, "first"))
    await asyncio.wait_for(gate["started"].wait(), timeout=10)

    second = await server.send(thread_id, "second")
    gate["release"].set()

    assert second.status_code == 409
    assert second.json() == {"error": "conflict", "retry_after_ms": 2000}
    assert (await first).status_code == 200
    assert server.turns == ["first"]
    assert (await server.send(thread_id, "third")).status_code == 200  # the lease was released


async def test_listing_marks_history_without_a_conversation_record_as_legacy(server):
    active = await server.create()
    await server.send(active)
    message = [{"role": "user", "content": "x"}]
    await server.db.save_conversation("cuga-default", active, 1, "alice", message)
    await server.db.save_conversation("cuga-default", "from-before", 1, "alice", message)

    listed = {c["id"]: c["status"] for c in (await server.client.get("/api/conversations")).json()}

    assert listed == {active: "active", "from-before": "legacy"}


async def test_delete_removes_checkpoint_and_history_and_the_id_cannot_return(server):
    thread_id = await server.create()
    await server.send(thread_id)

    response = await server.client.delete(f"/api/conversations/{thread_id}")

    assert response.status_code == 200, response.text
    snapshot = await server.graph.aget_state({"configurable": {"thread_id": server.key(thread_id)}})
    assert not snapshot.values
    assert await server.db.get_thread_history(thread_id) == []
    assert await server.db.get_thread_status(thread_id, "alice", "cuga-default") == "deleted"
    assert (await server.send(thread_id)).status_code == 404


async def test_delete_waits_for_a_running_turn(server):
    thread_id = await server.create()
    gate = server.hold_turns()
    turn = asyncio.create_task(server.send(thread_id))
    await asyncio.wait_for(gate["started"].wait(), timeout=10)

    busy = await server.client.delete(f"/api/conversations/{thread_id}")
    gate["release"].set()
    await turn

    assert busy.status_code == 409
    assert (await server.client.delete(f"/api/conversations/{thread_id}")).status_code == 200


async def test_legacy_delete_removes_only_the_callers_own_history(server):
    await server.db.save_conversation("cuga-default", "shared", 1, "alice", [])
    await server.db.save_conversation("cuga-default", "shared", 1, "bob", [])

    assert (await server.client.delete("/api/conversations/shared")).status_code == 200
    server.main._delete_session_knowledge_for_thread.assert_not_awaited()  # keyed by thread only

    assert await server.db.get_latest_version("cuga-default", "shared", "alice") == 0
    assert await server.db.get_latest_version("cuga-default", "shared", "bob") == 1
    server.user["sub"] = "carol"
    assert (await server.client.delete("/api/conversations/shared")).status_code == 404


def test_entry_points_never_share_a_web_conversations_checkpoint(monkeypatch):
    from cuga.backend.server import thread_scope

    keys = {
        entry: thread_scope.checkpoint_thread_id("t", "u", "a", entry)
        for entry in ("web", "run", "a2a", "ext")
    }
    assert len(set(keys.values())) == 4
    # Web keys are unchanged, so checkpoints written before this change keep resolving.
    assert keys["web"] == thread_scope.checkpoint_thread_id("t", "u", "a")


async def test_owner_rows_from_before_the_status_column_become_legacy(tmp_path):
    from cuga.backend.server.conversation_history import ConversationHistoryDB, _instance_id, _tenant_id
    from cuga.backend.storage.relational.local import LocalRelationalStore

    store = LocalRelationalStore(str(tmp_path / "old.db"))
    await store.execute("""CREATE TABLE runtime_thread_owners (
        tenant_id TEXT NOT NULL, instance_id TEXT NOT NULL, thread_id TEXT NOT NULL,
        user_id TEXT NOT NULL, agent_id TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY (tenant_id, instance_id, thread_id))""")
    await store.execute(
        "INSERT INTO runtime_thread_owners VALUES (?, ?, 'old', 'alice', 'cuga-default', 'then')",
        (_tenant_id(), _instance_id()),
    )
    await store.commit()
    db = ConversationHistoryDB()
    db._get_store = lambda: store
    try:
        assert await db.get_thread_status("old", "alice", "cuga-default") == "legacy"
        db._schema_ensured = False
        await db._ensure_schema()  # running the migration again is harmless
    finally:
        await store.close()
