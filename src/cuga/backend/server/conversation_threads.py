"""
Conversation Threads Persistence Module

Stores lightweight thread-level metadata (status, checkpoint IDs, config hash)
for the Continue-a-Conversation feature (issue #774).

Uses the storage layer (get_storage().get_relational_store("conversation")) for
local SQLite or prod Postgres — same store as conversation_history.py.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

DEFAULT_USER_ID = "default_user"


class ThreadStatus:
    NEW = "new"
    ACTIVE = "active"
    EXPIRED = "expired"
    DELETED = "deleted"


@dataclass(frozen=True)
class Scope:
    tenant_id: str
    instance_id: str
    agent_id: str
    user_id: str


def request_scope(current_user: Optional[Any], agent_id: str) -> Scope:
    from cuga.config import get_service_instance_id, get_tenant_id  # noqa: PLC0415

    return Scope(
        tenant_id=get_tenant_id(),
        instance_id=get_service_instance_id(),
        agent_id=agent_id,
        user_id=current_user.sub if current_user else DEFAULT_USER_ID,
    )


@dataclass
class ThreadRecord:
    thread_id: str
    tenant_id: str
    instance_id: str
    agent_id: str
    user_id: str
    status: str  # ThreadStatus value
    config_agent_hash: str
    config_runtime_ver: str
    pending_interrupt: bool
    latest_checkpoint_id: str
    projected_checkpoint_id: str
    created_at: str
    updated_at: str


class ThreadIdConflict(Exception):
    """Raised when a thread_id already exists in the store."""


def _get_store():
    from cuga.backend.storage import get_storage

    return get_storage().get_relational_store("conversation")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_record(row: dict) -> ThreadRecord:
    return ThreadRecord(
        thread_id=row["thread_id"],
        tenant_id=row["tenant_id"],
        instance_id=row["instance_id"],
        agent_id=row["agent_id"],
        user_id=row["user_id"],
        status=row["status"],
        config_agent_hash=row["config_agent_hash"],
        config_runtime_ver=row["config_runtime_ver"],
        pending_interrupt=bool(row["pending_interrupt"]),
        latest_checkpoint_id=row["latest_checkpoint_id"],
        projected_checkpoint_id=row["projected_checkpoint_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class ConversationThreadsDB:
    def __init__(self) -> None:
        self._schema_ensured = False
        self._schema_store = None

    def _get_store(self):
        return _get_store()

    async def _ensure_schema(self) -> None:
        store = self._get_store()
        if self._schema_ensured and self._schema_store is store:
            return
        await store.execute("""
            CREATE TABLE IF NOT EXISTS conversation_threads (
                thread_id            TEXT PRIMARY KEY,
                tenant_id            TEXT NOT NULL,
                instance_id          TEXT NOT NULL,
                agent_id             TEXT NOT NULL,
                user_id              TEXT NOT NULL,
                status               TEXT NOT NULL,
                config_agent_hash    TEXT NOT NULL DEFAULT '',
                config_runtime_ver   TEXT NOT NULL DEFAULT '',
                pending_interrupt    INTEGER NOT NULL DEFAULT 0,
                latest_checkpoint_id TEXT NOT NULL DEFAULT '',
                projected_checkpoint_id TEXT NOT NULL DEFAULT '',
                created_at           TEXT NOT NULL,
                updated_at           TEXT NOT NULL
            )
        """)
        await store.execute(
            "CREATE INDEX IF NOT EXISTS idx_ct_scope ON conversation_threads"
            "(tenant_id, instance_id, agent_id, user_id, updated_at)"
        )
        await store.commit()
        self._schema_ensured = True
        self._schema_store = store

    async def create(
        self,
        scope: Scope,
        thread_id: str,
        status: str = ThreadStatus.NEW,
    ) -> ThreadRecord:
        await self._ensure_schema()
        store = self._get_store()
        now = _now()
        try:
            await store.execute(
                """
                INSERT INTO conversation_threads
                    (thread_id, tenant_id, instance_id, agent_id, user_id,
                     status, config_agent_hash, config_runtime_ver,
                     pending_interrupt, latest_checkpoint_id,
                     projected_checkpoint_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, '', '', 0, '', '', ?, ?)
                """,
                (
                    thread_id,
                    scope.tenant_id,
                    scope.instance_id,
                    scope.agent_id,
                    scope.user_id,
                    status,
                    now,
                    now,
                ),
            )
            await store.commit()
        except sqlite3.IntegrityError:
            raise ThreadIdConflict(thread_id)
        except Exception as exc:
            # asyncpg UniqueViolationError — import lazily to avoid hard dep
            try:
                import asyncpg  # noqa: PLC0415

                if isinstance(exc, asyncpg.errors.UniqueViolationError):
                    raise ThreadIdConflict(thread_id) from exc
            except ImportError:
                pass
            raise
        return ThreadRecord(
            thread_id=thread_id,
            tenant_id=scope.tenant_id,
            instance_id=scope.instance_id,
            agent_id=scope.agent_id,
            user_id=scope.user_id,
            status=status,
            config_agent_hash="",
            config_runtime_ver="",
            pending_interrupt=False,
            latest_checkpoint_id="",
            projected_checkpoint_id="",
            created_at=now,
            updated_at=now,
        )

    async def get_in_scope(self, scope: Scope, thread_id: str) -> Optional[ThreadRecord]:
        await self._ensure_schema()
        store = self._get_store()
        row = await store.fetchone(
            """
            SELECT * FROM conversation_threads
            WHERE tenant_id = ? AND instance_id = ? AND agent_id = ?
              AND user_id = ? AND thread_id = ?
              AND status <> 'deleted'
            """,
            (scope.tenant_id, scope.instance_id, scope.agent_id, scope.user_id, thread_id),
        )
        if row is None:
            return None
        return _row_to_record(row)

    async def mark_turn_committed(
        self,
        scope: Scope,
        thread_id: str,
        *,
        checkpoint_id: str,
        agent_hash: str,
        runtime_ver: str,
        pending_interrupt: bool,
    ) -> None:
        await self._ensure_schema()
        store = self._get_store()
        now = _now()
        await store.execute(
            """
            UPDATE conversation_threads
               SET status = 'active',
                   config_agent_hash = ?,
                   config_runtime_ver = ?,
                   pending_interrupt = ?,
                   latest_checkpoint_id = ?,
                   updated_at = ?
             WHERE tenant_id = ? AND instance_id = ? AND agent_id = ?
               AND user_id = ? AND thread_id = ?
               AND status NOT IN ('deleted', 'expired')
            """,
            (
                agent_hash,
                runtime_ver,
                1 if pending_interrupt else 0,
                checkpoint_id,
                now,
                scope.tenant_id,
                scope.instance_id,
                scope.agent_id,
                scope.user_id,
                thread_id,
            ),
        )
        await store.commit()

    async def mark_projected(self, scope: Scope, thread_id: str, checkpoint_id: str) -> None:
        await self._ensure_schema()
        store = self._get_store()
        now = _now()
        await store.execute(
            """
            UPDATE conversation_threads
               SET projected_checkpoint_id = ?,
                   updated_at = ?
             WHERE tenant_id = ? AND instance_id = ? AND agent_id = ?
               AND user_id = ? AND thread_id = ?
               AND status NOT IN ('deleted', 'expired')
            """,
            (
                checkpoint_id,
                now,
                scope.tenant_id,
                scope.instance_id,
                scope.agent_id,
                scope.user_id,
                thread_id,
            ),
        )
        await store.commit()

    async def mark_deleted(self, scope: Scope, thread_id: str) -> None:
        await self._ensure_schema()
        store = self._get_store()
        now = _now()
        await store.execute(
            """
            UPDATE conversation_threads
               SET status = 'deleted',
                   updated_at = ?
             WHERE tenant_id = ? AND instance_id = ? AND agent_id = ?
               AND user_id = ? AND thread_id = ?
            """,
            (
                now,
                scope.tenant_id,
                scope.instance_id,
                scope.agent_id,
                scope.user_id,
                thread_id,
            ),
        )
        await store.commit()

    async def list_for_scope(self, scope: Scope, limit: int = 200) -> list[ThreadRecord]:
        await self._ensure_schema()
        store = self._get_store()
        rows = await store.fetchall(
            """
            SELECT * FROM conversation_threads
            WHERE tenant_id = ? AND instance_id = ? AND agent_id = ?
              AND user_id = ?
              AND status <> 'deleted'
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (scope.tenant_id, scope.instance_id, scope.agent_id, scope.user_id, limit),
        )
        return [_row_to_record(r) for r in rows]


_conversation_threads_db: Optional[ConversationThreadsDB] = None


def get_conversation_threads_db() -> ConversationThreadsDB:
    global _conversation_threads_db
    if _conversation_threads_db is None:
        _conversation_threads_db = ConversationThreadsDB()
    return _conversation_threads_db
