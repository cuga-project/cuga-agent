"""
Thread Lease Module

Cross-replica, per-thread lease used to guarantee that only one turn runs on a
conversation thread at a time. Leases live in the ``thread_leases`` table of the
same relational store as the conversation history
(``get_storage().get_relational_store("conversation")``), so they work with both
local SQLite and prod Postgres.

``holder_id`` is ``f"{replica_id}:{turn_uuid}"`` where ``replica_id`` is generated
once per process.
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
logger.info(f"thread_lease replica_id={REPLICA_ID}")


@dataclass
class LeaseToken:
    thread_id: str
    holder_id: str
    expires_at: float


class ThreadBusy(Exception):
    """Another live holder owns the lease for this thread."""


class ThreadLease:
    def __init__(self):
        self._schema_ensured = False
        self._schema_store = None

    def _get_store(self):
        return get_storage().get_relational_store("conversation")

    async def _ensure_schema(self):
        store = self._get_store()
        if self._schema_ensured and self._schema_store is store:
            return
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
        self._schema_ensured = True
        self._schema_store = store

    async def _holder_of(self, store, thread_id: str) -> Optional[str]:
        row = await store.fetchone("SELECT holder_id FROM thread_leases WHERE thread_id = ?", (thread_id,))
        return row["holder_id"] if row else None

    async def acquire(self, thread_id: str, ttl_seconds: float = 60.0) -> LeaseToken:
        """Take the lease if free or expired; raise ThreadBusy if a live holder owns it."""
        await self._ensure_schema()
        store = self._get_store()
        now = time.time()
        new_holder_id = f"{REPLICA_ID}:{uuid.uuid4().hex}"
        expires_at = now + ttl_seconds
        await store.execute(
            """
            INSERT INTO thread_leases(thread_id, holder_id, expires_at, stop_requested, updated_at)
            VALUES (?, ?, ?, 0, ?)
            ON CONFLICT(thread_id) DO UPDATE
              SET holder_id = excluded.holder_id, expires_at = excluded.expires_at,
                  stop_requested = 0, updated_at = excluded.updated_at
              WHERE thread_leases.expires_at < ?
            """,
            (thread_id, new_holder_id, expires_at, now, now),
        )
        await store.commit()
        if await self._holder_of(store, thread_id) != new_holder_id:
            raise ThreadBusy(thread_id)
        return LeaseToken(thread_id=thread_id, holder_id=new_holder_id, expires_at=expires_at)

    async def renew(self, token: LeaseToken, ttl_seconds: float = 60.0) -> bool:
        """Extend the lease. Returns False if this holder no longer owns it."""
        await self._ensure_schema()
        store = self._get_store()
        now = time.time()
        expires_at = now + ttl_seconds
        await store.execute(
            "UPDATE thread_leases SET expires_at = ?, updated_at = ? WHERE thread_id = ? AND holder_id = ?",
            (expires_at, now, token.thread_id, token.holder_id),
        )
        await store.commit()
        # The store's shared _last_rowcount can be clobbered by concurrent statements,
        # so confirm ownership by reading the holder back instead.
        if await self._holder_of(store, token.thread_id) != token.holder_id:
            return False
        token.expires_at = expires_at
        return True

    async def release(self, token: LeaseToken) -> None:
        """Delete the lease only if this token's holder still owns it."""
        await self._ensure_schema()
        store = self._get_store()
        await store.execute(
            "DELETE FROM thread_leases WHERE thread_id = ? AND holder_id = ?",
            (token.thread_id, token.holder_id),
        )
        await store.commit()

    async def stop_requested(self, token: LeaseToken) -> bool:
        await self._ensure_schema()
        store = self._get_store()
        row = await store.fetchone(
            "SELECT stop_requested FROM thread_leases WHERE thread_id = ? AND holder_id = ?",
            (token.thread_id, token.holder_id),
        )
        return bool(row and row["stop_requested"])

    async def is_active(self, thread_id: str) -> bool:
        """Whether a live holder can still write this thread's checkpoint."""
        await self._ensure_schema()
        row = await self._get_store().fetchone(
            "SELECT expires_at FROM thread_leases WHERE thread_id = ?", (thread_id,)
        )
        return bool(row and row["expires_at"] > time.time())

    async def request_stop(self, thread_id: str) -> bool:
        """Flag the thread's lease for stop regardless of holder. True if a lease row exists."""
        await self._ensure_schema()
        store = self._get_store()
        await store.execute(
            "UPDATE thread_leases SET stop_requested = 1 WHERE thread_id = ?",
            (thread_id,),
        )
        await store.commit()
        # Confirm via read-back rather than the shared _last_rowcount (see renew).
        row = await store.fetchone(
            "SELECT stop_requested FROM thread_leases WHERE thread_id = ?", (thread_id,)
        )
        return bool(row and row["stop_requested"])


async def _renew_loop(
    token: LeaseToken,
    lease: ThreadLease,
    renew_seconds: float,
    stop_event: Optional[asyncio.Event],
) -> None:
    while True:
        await asyncio.sleep(renew_seconds)
        still_held = await lease.renew(token)
        if not still_held:
            if stop_event is not None:
                stop_event.set()
            return


@asynccontextmanager
async def lease_scope(
    lease: ThreadLease,
    thread_id: str,
    ttl_seconds: float = 60.0,
    renew_seconds: float = 20.0,
    stop_event: Optional[asyncio.Event] = None,
):
    """Acquire a lease, keep it renewed while the body runs, and release on exit.

    Raises ThreadBusy if acquisition fails. If the lease is lost during renewal,
    ``stop_event`` (when provided) is set. Release is shielded from cancellation.
    """
    token = await lease.acquire(thread_id, ttl_seconds)
    renew_task = asyncio.create_task(_renew_loop(token, lease, renew_seconds, stop_event))
    try:
        yield token
    finally:
        renew_task.cancel()
        try:
            await renew_task
        except asyncio.CancelledError:
            pass
        await asyncio.shield(lease.release(token))


_thread_lease: Optional[ThreadLease] = None


def get_thread_lease() -> ThreadLease:
    global _thread_lease
    if _thread_lease is None:
        _thread_lease = ThreadLease()
    return _thread_lease
