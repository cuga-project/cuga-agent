"""W17.1 rows 7 and 8: turn-prelude concurrency and missing-checkpoint behaviour.

Row 7 (concurrent /stream posts → one 200, one 409) is covered by
tests/unit/server/test_thread_guard.py::test_concurrent_stream_posts_one_200_one_409_conflict.

Row 8 (missing checkpoint on an active thread → ThreadUnavailable("missing")) is
covered by the parametrized matrix in
tests/unit/server/test_turn_prelude.py::test_status_checkpoint_matrix
(case: status=ACTIVE, checkpoint=absent, ckpt_kind=sqlite).

This file documents that coverage and provides lightweight pass-through
assertions that can run without the full server stack.
"""

import pytest


@pytest.mark.unit
def test_two_simultaneous_sends_one_turn_one_conflict():
    """Row 7: Two simultaneous /stream posts on one thread give one 200 and one 409.

    The authoritative assertion (which runs a real asyncio.gather race) lives in
    tests/unit/server/test_thread_guard.py::test_concurrent_stream_posts_one_200_one_409_conflict.

    Here we verify only the ThreadBusy exception that underlies the 409 response,
    confirming the lease mechanism rejects a second concurrent acquire.
    """
    import asyncio
    from cuga.backend.server.thread_lease import ThreadBusy, ThreadLease

    async def _run(tmp_path):
        from cuga.backend.storage.relational.local import LocalRelationalStore

        store = LocalRelationalStore(str(tmp_path / "leases.db"))
        lease = ThreadLease()
        lease._get_store = lambda: store

        # First acquire succeeds.
        await lease.acquire("busy-thread")

        # Second acquire on the same thread must raise ThreadBusy.
        with pytest.raises(ThreadBusy):
            await lease.acquire("busy-thread")

    import tempfile
    import pathlib

    with tempfile.TemporaryDirectory() as d:
        asyncio.run(_run(pathlib.Path(d)))


@pytest.mark.unit
def test_missing_checkpoint_returns_unavailable():
    """Row 8: Missing checkpoint on an active thread → ThreadUnavailable("missing").

    The authoritative parametrized assertion lives in
    tests/unit/server/test_turn_prelude.py::test_status_checkpoint_matrix
    (case: status=ACTIVE, checkpoint=absent, ckpt_kind=sqlite).

    Here we verify the same property through run_turn_prelude directly.
    """
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from cuga.backend.server.conversation_threads import ThreadRecord, ThreadStatus
    from cuga.backend.server.thread_lease import LeaseToken
    from cuga.backend.server.turn_prelude import ThreadUnavailable, run_turn_prelude

    THREAD_ID = "thread-missing"

    record = ThreadRecord(
        thread_id=THREAD_ID,
        tenant_id="t",
        instance_id="i",
        agent_id="a",
        user_id="u",
        status=ThreadStatus.ACTIVE,
        config_agent_hash="",
        config_runtime_ver="",
        pending_interrupt=False,
        latest_checkpoint_id="",
        projected_checkpoint_id="",
        created_at="now",
        updated_at="now",
    )

    # Empty snapshot (no values, no next) simulates an absent checkpoint.
    empty_snap = SimpleNamespace(values={}, next=())

    token = LeaseToken(thread_id=THREAD_ID, holder_id="h", expires_at=0.0)
    lease = MagicMock()
    lease.acquire = AsyncMock(return_value=token)
    lease.release = AsyncMock()

    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=empty_snap)

    async def _run():
        with pytest.raises(ThreadUnavailable) as exc:
            await run_turn_prelude(
                record=record,
                thread_id=THREAD_ID,
                graph=graph,
                lease=lease,
                checkpointer_kind="sqlite",
            )
        assert exc.value.reason == "missing"
        lease.release.assert_awaited_once_with(token)

    asyncio.run(_run())
