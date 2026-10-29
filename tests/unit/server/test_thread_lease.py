"""Per-thread lease: one turn per conversation at a time, across replicas (#774)."""

from __future__ import annotations

import asyncio

import pytest

from cuga.backend.server import thread_lease as thread_lease_module
from cuga.backend.server.thread_lease import ThreadBusy, ThreadLease, keep_lease
from cuga.backend.storage.relational.local import LocalRelationalStore

pytestmark = pytest.mark.unit


@pytest.fixture
async def lease(tmp_path, monkeypatch):
    store = LocalRelationalStore(str(tmp_path / "conversation.db"))
    monkeypatch.setattr(
        thread_lease_module,
        "get_storage",
        lambda: type("S", (), {"get_relational_store": lambda _s, _n: store})(),
    )
    yield ThreadLease()
    await store.close()


async def test_concurrent_acquire_has_exactly_one_winner(lease):
    results = await asyncio.gather(*(lease.acquire("t1") for _ in range(5)), return_exceptions=True)
    assert sum(not isinstance(r, Exception) for r in results) == 1
    assert sum(isinstance(r, ThreadBusy) for r in results) == 4


async def test_expired_lease_is_taken_over_and_the_old_holder_loses_it(lease):
    old = await lease.acquire("t1", ttl_seconds=-1.0)
    new = await lease.acquire("t1")
    assert await lease.renew(old) is False
    await lease.release(old)  # a non-holder's release is a no-op
    with pytest.raises(ThreadBusy):
        await lease.acquire("t1")
    await lease.release(new)
    await lease.acquire("t1")


async def test_keep_lease_renews_releases_and_stops_the_turn_when_lost(lease):
    token = await lease.acquire("t1", ttl_seconds=0.1)
    async with keep_lease(lease, token, renew_seconds=0.02):
        await asyncio.sleep(0.15)  # longer than the TTL: only renewal keeps it
        with pytest.raises(ThreadBusy):
            await lease.acquire("t1")
    await lease.release(await lease.acquire("t1"))  # released on exit

    stop = asyncio.Event()
    token = await lease.acquire("t1")
    async with keep_lease(lease, token, stop_event=stop, renew_seconds=0.02):
        store = lease._get_store()
        await store.execute("UPDATE thread_leases SET holder_id = 'other' WHERE thread_id = 't1'")
        await store.commit()
        await asyncio.wait_for(stop.wait(), timeout=5.0)
