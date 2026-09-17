"""Unit tests for ConversationHistoryDB.save_stream_events merge behavior."""

from __future__ import annotations

import json

import pytest

from cuga.backend.server.conversation_history import ConversationHistoryDB
from cuga.backend.storage.relational.local import LocalRelationalStore

pytestmark = pytest.mark.unit


def _make_db(tmp_path) -> ConversationHistoryDB:
    store = LocalRelationalStore(str(tmp_path / "conversation.db"))
    db = ConversationHistoryDB()
    db._get_store = lambda: store
    return db


def _event(name: str, sequence: int) -> dict:
    return {
        "event_name": name,
        "event_data": f"data-{name}",
        "timestamp": "2026-01-01T00:00:00",
        "sequence": sequence,
    }


@pytest.mark.asyncio
async def test_save_stream_events_appends_and_resequences(tmp_path):
    db = _make_db(tmp_path)

    assert await db.save_stream_events("agent", "thread", "user", [_event("UserMessage", 0)])
    assert await db.save_stream_events("agent", "thread", "user", [_event("Answer", 0)])

    history = await db.get_stream_events("agent", "thread", "user")
    assert history is not None
    assert [e.event_name for e in history.events] == ["UserMessage", "Answer"]
    assert [e.sequence for e in history.events] == [0, 1]


@pytest.mark.asyncio
async def test_save_stream_events_tolerates_non_dict_entries_in_stored_row(tmp_path):
    """A corrupted row containing non-dict entries must not break persistence.

    Regression: max() over e.get("sequence") raised AttributeError on non-dict
    entries, the outer except swallowed it, and the thread's persistence
    silently failed on every subsequent save.
    """
    db = _make_db(tmp_path)

    assert await db.save_stream_events("agent", "thread", "user", [_event("UserMessage", 0)])

    # Corrupt the stored row with non-dict junk alongside a valid event.
    store = db._get_store()
    corrupted = json.dumps(["junk-string", 42, None, _event("UserMessage", 0)])
    await store.execute(
        "UPDATE stream_events SET events = ? WHERE agent_id = ? AND thread_id = ? AND user_id = ?",
        (corrupted, "agent", "thread", "user"),
    )
    await store.commit()

    assert await db.save_stream_events("agent", "thread", "user", [_event("Answer", 0)])

    history = await db.get_stream_events("agent", "thread", "user")
    assert history is not None
    # Junk entries are dropped; valid events survive with monotonic sequences.
    assert [e.event_name for e in history.events] == ["UserMessage", "Answer"]
    assert [e.sequence for e in history.events] == [0, 1]


@pytest.mark.asyncio
async def test_save_stream_events_tolerates_non_list_stored_payload(tmp_path):
    db = _make_db(tmp_path)

    assert await db.save_stream_events("agent", "thread", "user", [_event("UserMessage", 0)])

    store = db._get_store()
    await store.execute(
        "UPDATE stream_events SET events = ? WHERE agent_id = ? AND thread_id = ? AND user_id = ?",
        (json.dumps({"not": "a list"}), "agent", "thread", "user"),
    )
    await store.commit()

    assert await db.save_stream_events("agent", "thread", "user", [_event("Answer", 0)])

    history = await db.get_stream_events("agent", "thread", "user")
    assert history is not None
    assert [e.event_name for e in history.events] == ["Answer"]
    assert [e.sequence for e in history.events] == [0]


@pytest.mark.asyncio
async def test_get_thread_owners_for_agent_returns_distinct_scoped_keys(tmp_path):
    db = _make_db(tmp_path)

    assert await db.save_conversation("agent-a", "thread-a", 1, "user-a", [])
    assert await db.save_conversation("agent-a", "thread-a", 2, "user-a", [])
    assert await db.save_conversation("agent-a", "thread-a", 1, "user-b", [])
    assert await db.save_conversation("agent-b", "thread-b", 1, "user-a", [])

    assert await db.get_thread_owners_for_agent("agent-a") == {
        ("thread-a", "user-a"),
        ("thread-a", "user-b"),
    }


@pytest.mark.asyncio
async def test_thread_deletion_outbox_is_atomic_and_owner_scoped(tmp_path):
    db = _make_db(tmp_path)
    await db.save_stream_events("agent", "thread", "user", [_event("UserMessage", 0)])
    await db.save_stream_events("agent", "thread", "other", [_event("UserMessage", 0)])
    store = db._get_store()
    await store.execute(
        "CREATE TRIGGER reject_delete BEFORE DELETE ON stream_events BEGIN SELECT RAISE(ABORT, 'crash'); END"
    )
    await store.commit()
    assert not await db.delete_thread("agent", "thread", "user")
    assert await db.pending_source_deletions() == []
    assert await db.get_stream_events("agent", "thread", "user") is not None
    await store.execute("DROP TRIGGER reject_delete")
    await store.commit()
    assert await db.delete_thread("agent", "thread", "user")
    assert await db.delete_thread("agent", "thread", "user")
    events = await db.pending_source_deletions()
    assert len(events) == 1
    assert events[0]["user_id"] == "user"
    assert await db.get_stream_events("agent", "thread", "other") is not None
    await db.acknowledge_source_deletion(events[0]["event_id"])
    assert await db.pending_source_deletions() == []
    await store.close()


@pytest.mark.asyncio
async def test_source_deletion_delivery_retries_until_acknowledged(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    from cuga.backend.evolve.integration import EvolveIntegration
    from cuga.backend.evolve.deleted_sources import deliver_source_deletions

    db = _make_db(tmp_path)
    await db.save_stream_events("agent", "thread", "user", [_event("UserMessage", 0)])
    await db.delete_thread("agent", "thread", "user")
    monkeypatch.setattr("cuga.backend.server.conversation_history.get_conversation_db", lambda: db)
    monkeypatch.setattr(EvolveIntegration, "is_enabled", lambda: True)
    call = AsyncMock(side_effect=[RuntimeError("offline"), {"recorded": True}])
    monkeypatch.setattr(EvolveIntegration, "_call_structured_tool", call)
    with pytest.raises(RuntimeError):
        await deliver_source_deletions()
    assert len(await db.pending_source_deletions()) == 1
    await deliver_source_deletions()
    assert await db.pending_source_deletions() == []
    assert call.call_args_list[0] == call.call_args_list[1]
    await db._get_store().close()
