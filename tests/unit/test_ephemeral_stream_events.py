# tests/unit/test_ephemeral_stream_events.py
"""Tests for ephemeral (Try-It-Out / X-Disable-History) stream-event persistence.

Test 1 - GC: orphaned stream_events (no conversation_history row) are cleaned up.
Test 2 - disable_history save path: events_only=True calls save_stream_events
          but not save_conversation.
"""
import asyncio
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_events(n: int = 1):
    return [{"event_name": "Answer", "event_data": f"data-{i}", "timestamp": "t", "sequence": i} for i in range(n)]


def _setup_in_memory_db():
    """Return a ConversationHistoryDB backed by an isolated temp SQLite file.

    We use a real temp file (not :memory:) because the LocalRelationalStore
    keeps a single sqlite3.Connection and the StorageFacade caches stores by
    name — two tests sharing a `:memory:` DB via the same cache slot would
    bleed state into each other.
    """
    import cuga.backend.storage.facade as _facade

    tmpfile = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmpfile.close()

    # Patch the module-level function so that StorageFacade._local_db_path()
    # returns our temp file path, then blow away any cached stores so the
    # ConversationHistoryDB gets a fresh connection.
    original = _facade._local_db_path
    _facade._local_db_path = lambda: tmpfile.name
    _facade.get_storage().invalidate_relational_stores()

    from cuga.backend.server.conversation_history import ConversationHistoryDB

    db = ConversationHistoryDB()
    db._schema_ensured = False  # force schema creation against new file

    return db, tmpfile.name, original, _facade


def _teardown_db(original_fn, tmpfile_name, _facade):
    import os

    _facade._local_db_path = original_fn
    _facade.get_storage().invalidate_relational_stores()
    try:
        os.unlink(tmpfile_name)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Test 1 – GC removes orphan rows, keeps rows anchored by conversation_history
# ---------------------------------------------------------------------------

def test_gc_removes_orphan_keeps_anchored():
    """
    thread_A: stream_events only (no conversation_history row)  -> GC must delete
    thread_B: stream_events + conversation_history row          -> GC must keep
    """
    db, tmpfile, orig, facade = _setup_in_memory_db()
    try:
        async def _run():
            # thread_A: orphan stream events
            await db.save_stream_events("agent", "thread_A", "user", _make_events(2))

            # thread_B: stream events + conversation history
            await db.save_stream_events("agent", "thread_B", "user", _make_events(3))
            await db.save_conversation("agent", "thread_B", 1, "user", [{"role": "user", "content": "hi"}])

            # older_than_days=0 makes every row eligible for GC
            removed = await db.gc_ephemeral_stream_events(older_than_days=0)

            assert removed == 1, f"Expected 1 row deleted, got {removed}"

            # thread_A events should be gone
            evts_a = await db.get_stream_events("agent", "thread_A", "user")
            assert evts_a is None, "thread_A stream events should have been GC'd"

            # thread_B events must survive
            evts_b = await db.get_stream_events("agent", "thread_B", "user")
            assert evts_b is not None, "thread_B stream events should be retained"
            assert len(evts_b.events) == 3

        asyncio.run(_run())
    finally:
        _teardown_db(orig, tmpfile, facade)


# ---------------------------------------------------------------------------
# Test 2 – disable_history path: events saved, conversation NOT saved
# ---------------------------------------------------------------------------

def test_disable_history_saves_events_not_conversation():
    """_save_conversation_and_events_async(..., events_only=True) must call
    save_stream_events but must NOT call save_conversation_to_db."""
    from cuga.backend.server.main import _save_conversation_and_events_async

    save_conv_calls = []
    save_events_calls = []

    async def fake_save_conv(agent_id, thread_id, state, user_id, user_attachments=None):
        save_conv_calls.append((agent_id, thread_id))

    async def fake_save_events(agent_id, thread_id, user_id, events):
        save_events_calls.append((agent_id, thread_id, len(events)))
        return True

    fake_db = MagicMock()
    fake_db.save_stream_events = AsyncMock(side_effect=fake_save_events)

    async def _run():
        with (
            patch("cuga.backend.server.main.save_conversation_to_db", side_effect=fake_save_conv),
            patch("cuga.backend.server.main.get_conversation_db", return_value=fake_db),
        ):
            # Use a plain MagicMock for state — the helper only passes it through
            # to save_conversation_to_db, which is patched out here.
            await _save_conversation_and_events_async(
                agent_id="agent",
                thread_id="t-tryitout",
                user_id="user",
                state=MagicMock(),
                events=_make_events(4),
                events_only=True,
            )

    asyncio.run(_run())

    assert save_conv_calls == [], "save_conversation_to_db must NOT be called in events_only mode"
    assert len(save_events_calls) == 1, "save_stream_events must be called once"
    assert save_events_calls[0] == ("agent", "t-tryitout", 4)
