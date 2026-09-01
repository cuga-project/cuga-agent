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
