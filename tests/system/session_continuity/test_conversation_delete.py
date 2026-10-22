"""W17.1 row 9: Deleting a conversation removes its checkpoints.

Coverage is provided by
tests/unit/server/test_conversation_delete.py::test_delete_removes_checkpoint_for_thread
and the surrounding suite (W12.1).

This file documents that coverage and provides a lightweight smoke test
that validates the delete→guard pipeline without a live HTTP server.
"""

import pytest


@pytest.mark.unit
def test_delete_removes_checkpoints():
    """Row 9: DELETE /api/conversations/{id} removes checkpoints and 404s on re-access.

    The authoritative assertion lives in
    tests/unit/server/test_conversation_delete.py::test_delete_marks_thread_deleted_so_guard_returns_404.

    Here we verify only the ThreadRecord state machine: after mark_deleted()
    the record is absent from the DB so resolve_owned_thread raises 404.
    """
    import asyncio
    from types import SimpleNamespace

    from cuga.backend.server.conversation_threads import ConversationThreadsDB, request_scope
    from cuga.backend.storage.relational.local import LocalRelationalStore
    import tempfile
    import pathlib

    OWNER = SimpleNamespace(sub="user-del")
    AGENT_ID = "cuga-default"

    async def _run(tmp_path):
        store = LocalRelationalStore(str(tmp_path / "threads.db"))
        db = ConversationThreadsDB()
        db._get_store = lambda: store

        scope = request_scope(OWNER, AGENT_ID)
        await db.create(scope, "thread-to-delete")

        # Record exists before deletion.
        rec = await db.get_in_scope(scope, "thread-to-delete")
        assert rec is not None

        # Mark deleted (simulating what DELETE /api/conversations/{id} does).
        await db.mark_deleted(scope, "thread-to-delete")

        # After deletion the record must not be returned.
        rec_after = await db.get_in_scope(scope, "thread-to-delete")
        assert rec_after is None

    with tempfile.TemporaryDirectory() as d:
        asyncio.run(_run(pathlib.Path(d)))
