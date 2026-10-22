"""Durable record of the remote sandboxes created for each conversation thread.

The executor's interpreter cache is process-local, so after a restart or on
another replica it cannot find a thread's sandbox. Deleting a conversation
uses these rows to kill the exact sandboxes that hold its files.
"""

from __future__ import annotations

from datetime import datetime, timezone

_schema_store = None


def _get_store():
    from cuga.backend.storage import get_storage

    return get_storage().get_relational_store("conversation")


async def _ensure_schema(store) -> None:
    global _schema_store
    if _schema_store is store:
        return
    await store.execute("""
        CREATE TABLE IF NOT EXISTS thread_sandboxes (
            thread_id  TEXT NOT NULL,
            sandbox_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (thread_id, sandbox_id)
        )
    """)
    await store.commit()
    _schema_store = store


async def record_thread_sandbox(thread_id: str, sandbox_id: str) -> None:
    store = _get_store()
    await _ensure_schema(store)
    await store.execute(
        "INSERT INTO thread_sandboxes (thread_id, sandbox_id, created_at) VALUES (?, ?, ?) "
        "ON CONFLICT (thread_id, sandbox_id) DO NOTHING",
        (thread_id, sandbox_id, datetime.now(timezone.utc).isoformat()),
    )
    await store.commit()


async def thread_sandbox_ids(thread_id: str) -> list[str]:
    store = _get_store()
    await _ensure_schema(store)
    rows = await store.fetchall(
        "SELECT sandbox_id FROM thread_sandboxes WHERE thread_id = ? ORDER BY created_at",
        (thread_id,),
    )
    return [r["sandbox_id"] for r in rows]


async def forget_thread_sandbox(thread_id: str, sandbox_id: str) -> None:
    store = _get_store()
    await _ensure_schema(store)
    await store.execute(
        "DELETE FROM thread_sandboxes WHERE thread_id = ? AND sandbox_id = ?",
        (thread_id, sandbox_id),
    )
    await store.commit()
