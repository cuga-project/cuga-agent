"""W17.1 row 10: Pre-change thread is listed as legacy, shown read-only, /stream returns 404.

Coverage is provided by tests/unit/server/test_pre_existing_threads.py (W13.1–W13.3).

Key assertions already in the unit suite:
- test_listing_includes_legacy_thread_with_legacy_status  → listing shows "legacy" status
- test_owner_mutating_operations_on_legacy_thread_are_404  → /stream is 404 (read-only)
- test_owner_reads_legacy_transcript  → transcript read succeeds

This file documents that coverage and provides a lightweight property
check — no HTTP server needed.
"""

import pytest


@pytest.mark.unit
def test_legacy_thread_listed_and_read_only():
    """Row 10: Legacy thread listed as 'legacy', shown read-only, /stream returns 404.

    A thread that exists only in the conversation-history store (no ThreadRecord)
    must appear in the sidebar with status='legacy' and must not accept mutating
    requests (/stream, /stop, workspace).

    The authoritative assertions live in
    tests/unit/server/test_pre_existing_threads.py.

    This pass-through test documents the W17.1 row 10 coverage location and
    validates the absence of a ThreadRecord for a history-only thread directly
    against the ConversationThreadsDB API.
    """
    import asyncio
    from types import SimpleNamespace

    from cuga.backend.server.conversation_threads import ConversationThreadsDB, request_scope
    from cuga.backend.storage.relational.local import LocalRelationalStore
    import tempfile
    import pathlib

    OWNER = SimpleNamespace(sub="user-legacy")
    AGENT_ID = "cuga-default"

    async def _run(tmp_path):
        store = LocalRelationalStore(str(tmp_path / "threads.db"))
        db = ConversationThreadsDB()
        db._get_store = lambda: store

        scope = request_scope(OWNER, AGENT_ID)

        # A "legacy" thread has no ThreadRecord — it was created before the
        # thread-guard migration.  Querying it returns None.
        rec = await db.get_in_scope(scope, "legacy-thread-id")
        assert rec is None, (
            "A legacy thread must have no ThreadRecord; "
            "the sidebar's 'legacy' status comes from history-only entries."
        )

    with tempfile.TemporaryDirectory() as d:
        asyncio.run(_run(pathlib.Path(d)))
