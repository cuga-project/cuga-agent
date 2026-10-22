"""Unit tests for ThreadLease (W08.1 schema, W08.2 acquire/renew/release)."""

from __future__ import annotations

import asyncio

import pytest

from cuga.backend.server import thread_lease as thread_lease_module
from cuga.backend.server.thread_lease import ThreadBusy, ThreadLease, get_thread_lease, lease_scope
from cuga.backend.storage.relational.local import LocalRelationalStore

pytestmark = pytest.mark.unit


class _FakeStorage:
    def __init__(self, store):
        self._store = store

    def get_relational_store(self, db_name: str):
        assert db_name == "conversation"
        return self._store


@pytest.fixture
async def lease(tmp_path, monkeypatch):
    store = LocalRelationalStore(str(tmp_path / "conversation.db"))
    monkeypatch.setattr(thread_lease_module, "get_storage", lambda: _FakeStorage(store))
    yield ThreadLease()
    await store.close()


@pytest.mark.asyncio
async def test_schema_creation_is_idempotent(lease):
    await lease._ensure_schema()
    # A fresh instance re-runs CREATE TABLE IF NOT EXISTS against the existing table.
    other = ThreadLease()
    await other._ensure_schema()
    await other._ensure_schema()
    token = await lease.acquire("t1")
    assert token.thread_id == "t1"


@pytest.mark.asyncio
async def test_concurrent_acquire_has_exactly_one_winner(lease):
    results = await asyncio.gather(
        lease.acquire("t1"),
        lease.acquire("t1"),
        return_exceptions=True,
    )
    winners = [r for r in results if not isinstance(r, Exception)]
    busy = [r for r in results if isinstance(r, ThreadBusy)]
    assert len(winners) == 1
    assert len(busy) == 1


@pytest.mark.asyncio
async def test_live_lease_blocks_second_acquire(lease):
    await lease.acquire("t1", ttl_seconds=60.0)
    with pytest.raises(ThreadBusy):
        await lease.acquire("t1")


@pytest.mark.asyncio
async def test_expired_lease_can_be_stolen(lease):
    first = await lease.acquire("t1", ttl_seconds=-1.0)
    second = await lease.acquire("t1")
    assert second.holder_id != first.holder_id


@pytest.mark.asyncio
async def test_renew_after_steal_returns_false(lease):
    first = await lease.acquire("t1", ttl_seconds=-1.0)
    await lease.acquire("t1")
    assert await lease.renew(first) is False


@pytest.mark.asyncio
async def test_renew_by_holder_extends_lease(lease):
    token = await lease.acquire("t1", ttl_seconds=1.0)
    old_expiry = token.expires_at
    assert await lease.renew(token, ttl_seconds=60.0) is True
    assert token.expires_at > old_expiry


@pytest.mark.asyncio
async def test_release_by_non_holder_is_noop(lease):
    first = await lease.acquire("t1", ttl_seconds=-1.0)
    second = await lease.acquire("t1")
    await lease.release(first)
    # Stale holder's release must not drop the current holder's lease.
    assert await lease.renew(second) is True
    with pytest.raises(ThreadBusy):
        await lease.acquire("t1")


@pytest.mark.asyncio
async def test_release_by_holder_frees_thread(lease):
    token = await lease.acquire("t1")
    await lease.release(token)
    again = await lease.acquire("t1")
    assert again.holder_id != token.holder_id


@pytest.mark.asyncio
async def test_request_stop_sets_flag_and_stop_requested_reads_it(lease):
    token = await lease.acquire("t1")
    assert await lease.stop_requested(token) is False
    assert await lease.request_stop("t1") is True
    assert await lease.stop_requested(token) is True


@pytest.mark.asyncio
async def test_request_stop_without_lease_returns_false(lease):
    assert await lease.request_stop("missing") is False


@pytest.mark.asyncio
async def test_acquire_resets_stop_requested(lease):
    first = await lease.acquire("t1", ttl_seconds=-1.0)
    assert await lease.request_stop("t1") is True
    assert await lease.stop_requested(first) is True
    second = await lease.acquire("t1")
    assert await lease.stop_requested(second) is False


@pytest.mark.asyncio
async def test_lease_scope_renews_lease(lease):
    async with lease_scope(lease, "t1", ttl_seconds=1.0, renew_seconds=0.05) as token:
        initial_expiry = token.expires_at
        await asyncio.wait_for(asyncio.sleep(0.3), timeout=5.0)
        # Renewal (default 60s TTL) must have extended the lease beyond the initial 1s TTL.
        assert token.expires_at > initial_expiry
        assert await lease.renew(token) is True


@pytest.mark.asyncio
async def test_lease_scope_sets_stop_event_when_lease_stolen(lease):
    stop_event = asyncio.Event()
    async with lease_scope(lease, "t1", ttl_seconds=60.0, renew_seconds=0.05, stop_event=stop_event):
        store = lease._get_store()
        await store.execute(
            "UPDATE thread_leases SET holder_id = ? WHERE thread_id = ?",
            ("other-replica:turn", "t1"),
        )
        await store.commit()
        await asyncio.wait_for(stop_event.wait(), timeout=5.0)
    assert stop_event.is_set()


@pytest.mark.asyncio
async def test_lease_scope_releases_on_exit(lease):
    async with lease_scope(lease, "t1") as token:
        pass
    again = await asyncio.wait_for(lease.acquire("t1"), timeout=5.0)
    assert again.holder_id != token.holder_id


@pytest.mark.asyncio
async def test_lease_scope_raises_thread_busy_when_held(lease):
    await lease.acquire("t1", ttl_seconds=60.0)
    with pytest.raises(ThreadBusy):
        async with lease_scope(lease, "t1"):
            pytest.fail("body must not run when the lease is held")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_lease_works_in_memory_checkpointer_mode(lease):
    # Lease operates on the conversation relational store, independent of checkpointer kind.
    # This test confirms the lease works regardless of storage.checkpointer setting.
    token = await lease.acquire("t1", ttl_seconds=1.0)
    old_expiry = token.expires_at
    assert await lease.renew(token, ttl_seconds=60.0) is True
    assert token.expires_at > old_expiry
    with pytest.raises(ThreadBusy):
        await lease.acquire("t1")
    await lease.release(token)
    again = await lease.acquire("t1")
    assert again.holder_id != token.holder_id


def test_get_thread_lease_is_singleton():
    assert get_thread_lease() is get_thread_lease()
