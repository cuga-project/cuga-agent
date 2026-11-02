"""Per-thread lease: best-effort serialization of turns on a conversation, across replicas (#774).

Leases live in the ``thread_leases`` table of the conversation relational store, so they work
on local SQLite and prod Postgres alike. A lease expires after ``storage.thread_lease_ttl_seconds``
unless renewed; an expired lease can be taken over, so a crashed replica never blocks a thread.
Clock skew between replicas is tolerated because the TTL is several renew intervals long.

Known limit: checkpoint writes are not fenced. A holder paused beyond the TTL (a stalled event
loop or VM) can keep writing until its next renewal fails and its turn sees the stop event, so it
may overlap with the holder that took over. Fencing is left to the cross-replica safety work.
"""

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Optional

from loguru import logger

from cuga.backend.storage import get_storage

REPLICA_ID = uuid.uuid4().hex[:12]
_STOP_POLL_SECONDS = 2.0


@dataclass
class LeaseToken:
    thread_id: str
    holder_id: str


class ThreadBusy(Exception):
    """Another live holder owns the lease for this thread."""


def _ttl_seconds() -> float:
    from cuga.config import settings

    return float(settings.storage.thread_lease_ttl_seconds)


def _renew_seconds() -> float:
    from cuga.config import settings

    return float(settings.storage.thread_lease_renew_seconds)


class ThreadLease:
    def __init__(self):
        self._schema_store = None

    def _get_store(self):
        return get_storage().get_relational_store("conversation")

    async def _store(self):
        store = self._get_store()
        if self._schema_store is not store:
            await store.execute("""
                CREATE TABLE IF NOT EXISTS thread_leases (
                    thread_id      TEXT PRIMARY KEY,
                    holder_id      TEXT NOT NULL,
                    expires_at     REAL NOT NULL,
                    stop_requested INTEGER NOT NULL DEFAULT 0,
                    updated_at     REAL NOT NULL
                )
            """)
            await store.commit()
            self._schema_store = store
        return store

    async def _holder_of(self, store, thread_id: str) -> Optional[str]:
        row = await store.fetchone("SELECT holder_id FROM thread_leases WHERE thread_id = ?", (thread_id,))
        return row["holder_id"] if row else None

    async def acquire(self, thread_id: str, ttl_seconds: Optional[float] = None) -> LeaseToken:
        """Take the lease if it is free or expired; raise ``ThreadBusy`` otherwise."""
        store = await self._store()
        now = time.time()
        holder_id = f"{REPLICA_ID}:{uuid.uuid4().hex}"
        await store.execute(
            """
            INSERT INTO thread_leases(thread_id, holder_id, expires_at, stop_requested, updated_at)
            VALUES (?, ?, ?, 0, ?)
            ON CONFLICT(thread_id) DO UPDATE
              SET holder_id = excluded.holder_id, expires_at = excluded.expires_at,
                  stop_requested = 0, updated_at = excluded.updated_at
              WHERE thread_leases.expires_at < ?
            """,
            (thread_id, holder_id, now + (ttl_seconds or _ttl_seconds()), now, now),
        )
        await store.commit()
        # Read the holder back: the store's shared rowcount is not safe under concurrency.
        if await self._holder_of(store, thread_id) != holder_id:
            raise ThreadBusy(thread_id)
        return LeaseToken(thread_id=thread_id, holder_id=holder_id)

    async def renew(self, token: LeaseToken, ttl_seconds: Optional[float] = None) -> bool:
        """Extend the lease. ``False`` means another holder took it over."""
        store = await self._store()
        now = time.time()
        await store.execute(
            "UPDATE thread_leases SET expires_at = ?, updated_at = ? WHERE thread_id = ? AND holder_id = ?",
            (now + (ttl_seconds or _ttl_seconds()), now, token.thread_id, token.holder_id),
        )
        await store.commit()
        return await self._holder_of(store, token.thread_id) == token.holder_id

    async def release(self, token: LeaseToken) -> None:
        """Delete the lease if this token still holds it. Safe to call more than once."""
        store = await self._store()
        await store.execute(
            "DELETE FROM thread_leases WHERE thread_id = ? AND holder_id = ?",
            (token.thread_id, token.holder_id),
        )
        await store.commit()

    async def request_stop(self, thread_id: str) -> None:
        """Ask the replica running a turn on this thread to stop it. A no-op if no lease is held."""
        store = await self._store()
        await store.execute(
            "UPDATE thread_leases SET stop_requested = 1, updated_at = ? WHERE thread_id = ?",
            (time.time(), thread_id),
        )
        await store.commit()

    async def stop_requested(self, token: LeaseToken) -> bool:
        """Whether a stop was requested for the turn this token holds the lease for."""
        store = await self._store()
        row = await store.fetchone(
            "SELECT stop_requested FROM thread_leases WHERE thread_id = ? AND holder_id = ?",
            (token.thread_id, token.holder_id),
        )
        return bool(row and row["stop_requested"])


@asynccontextmanager
async def keep_lease(
    lease: ThreadLease,
    token: LeaseToken,
    stop_event: Optional[asyncio.Event] = None,
    renew_seconds: Optional[float] = None,
):
    """Renew an acquired lease while the body runs, then release it, also on cancellation.

    If the lease is lost, or another replica requests a stop, ``stop_event`` is set so the running
    turn stops.
    """

    async def _renew_loop() -> None:
        interval = renew_seconds or _renew_seconds()
        while True:
            await asyncio.sleep(interval)
            try:
                held = await lease.renew(token)
            except Exception:
                logger.exception("lease renewal failed")
                held = False
            if not held:
                logger.error("lease lost for a running turn; stopping it")
                if stop_event is not None:
                    stop_event.set()
                return

    async def _stop_poll_loop() -> None:
        while True:
            await asyncio.sleep(_STOP_POLL_SECONDS)
            try:
                requested = await lease.stop_requested(token)
            except Exception:
                logger.exception("stop request poll failed")
                continue
            if requested:
                logger.info("stop requested by another replica; stopping the turn")
                stop_event.set()
                return

    tasks = [asyncio.create_task(_renew_loop())]
    if stop_event is not None:
        tasks.append(asyncio.create_task(_stop_poll_loop()))
    try:
        yield token
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        await asyncio.shield(lease.release(token))


_thread_lease: Optional[ThreadLease] = None


def get_thread_lease() -> ThreadLease:
    global _thread_lease
    if _thread_lease is None:
        _thread_lease = ThreadLease()
    return _thread_lease
