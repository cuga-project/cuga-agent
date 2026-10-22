"""W17.1 rows 1 and 5: conversation continues after restart; sidebar count stable.

These are slow system tests that require a full server restart cycle.
They are marked @pytest.mark.slow and skipped in the fast suite.
Full implementation is deferred to a later iteration; the stubs here
establish the test IDs and document the expected behaviour.
"""

import pytest


@pytest.mark.slow
def test_completed_conversation_continues_after_restart():
    """Row 1: Completed conversation continues after restart with messages.

    Implementation plan:
    1. Start server with storage.checkpointer=durable and a tmp SQLite path.
    2. POST /stream → complete one turn, capture thread_id.
    3. Shut down the server process.
    4. Start a new server process against the same SQLite path.
    5. POST /stream with the same X-Thread-ID.
    6. Assert the response references prior context (history is preserved).
    """
    pytest.skip("Requires full server restart setup — implement in a later iteration")


@pytest.mark.slow
def test_thread_sidebar_count_stable_on_resume():
    """Row 5: Thread sidebar has exactly one entry after resume.

    After resuming a completed thread the sidebar must not duplicate the entry.
    Implementation plan mirrors row 1; assertion checks
    GET /api/conversations returns a single item for the thread.
    """
    pytest.skip("Requires full server restart setup — implement in a later iteration")
