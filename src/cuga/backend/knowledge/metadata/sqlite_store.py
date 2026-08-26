"""SQLite metadata for knowledge (local storage.mode).

Uses :class:`cuga.backend.storage.relational.local.LocalRelationalStore` async I/O
(``await execute`` / ``fetchone`` / ``fetchall``) so callers do not block the event loop.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from cuga.backend.knowledge.metadata.base import (
    current_scope,
    mark_file_tasks_interrupted,
    normalize_file_tasks,
    utc_now_iso,
)
from cuga.backend.storage.relational.local import LocalRelationalStore

_SCHEMA_SQL = """
            CREATE TABLE IF NOT EXISTS documents (
                tenant_id TEXT NOT NULL DEFAULT '',
                instance_id TEXT NOT NULL DEFAULT '',
                collection TEXT NOT NULL,
                filename TEXT NOT NULL,
                chunk_count INTEGER DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'indexed',
                ingested_at TEXT NOT NULL,
                preview TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (tenant_id, instance_id, collection, filename)
            );

            CREATE TABLE IF NOT EXISTS tasks (
                tenant_id TEXT NOT NULL DEFAULT '',
                instance_id TEXT NOT NULL DEFAULT '',
                task_id TEXT NOT NULL,
                collection TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','running','completed','failed','cancelled')),
                total_files INTEGER NOT NULL DEFAULT 0,
                processed_files INTEGER NOT NULL DEFAULT 0,
                successful_files INTEGER NOT NULL DEFAULT 0,
                failed_files INTEGER NOT NULL DEFAULT 0,
                file_tasks_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (tenant_id, instance_id, task_id)
            );

            CREATE TABLE IF NOT EXISTS collection_config (
                tenant_id TEXT NOT NULL DEFAULT '',
                instance_id TEXT NOT NULL DEFAULT '',
                collection TEXT NOT NULL,
                embedding_provider TEXT NOT NULL,
                embedding_model TEXT NOT NULL,
                embedding_dim INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (tenant_id, instance_id, collection)
            );

            CREATE TABLE IF NOT EXISTS settings (
                tenant_id TEXT NOT NULL DEFAULT '',
                instance_id TEXT NOT NULL DEFAULT '',
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY (tenant_id, instance_id, key)
            );

        """

_INDEX_SQL = """
            CREATE INDEX IF NOT EXISTS idx_tasks_collection_status
                ON tasks(tenant_id, instance_id, collection, status, updated_at);
            CREATE INDEX IF NOT EXISTS idx_docs_collection
                ON documents(tenant_id, instance_id, collection, status);
        """

_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    (
        "documents",
        """CREATE TABLE documents (
                tenant_id TEXT NOT NULL DEFAULT '',
                instance_id TEXT NOT NULL DEFAULT '',
                collection TEXT NOT NULL,
                filename TEXT NOT NULL,
                chunk_count INTEGER DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'indexed',
                ingested_at TEXT NOT NULL,
                preview TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (tenant_id, instance_id, collection, filename)
            )""",
        "INSERT INTO documents (tenant_id, instance_id, collection, filename, chunk_count, status, ingested_at, preview) "
        "SELECT '', '', collection, filename, chunk_count, status, ingested_at, "
        "COALESCE(preview, '') FROM documents__old",
    ),
    (
        "tasks",
        """CREATE TABLE tasks (
                tenant_id TEXT NOT NULL DEFAULT '',
                instance_id TEXT NOT NULL DEFAULT '',
                task_id TEXT NOT NULL,
                collection TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','running','completed','failed','cancelled')),
                total_files INTEGER NOT NULL DEFAULT 0,
                processed_files INTEGER NOT NULL DEFAULT 0,
                successful_files INTEGER NOT NULL DEFAULT 0,
                failed_files INTEGER NOT NULL DEFAULT 0,
                file_tasks_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (tenant_id, instance_id, task_id)
            )""",
        "INSERT INTO tasks (tenant_id, instance_id, task_id, collection, status, total_files, "
        "processed_files, successful_files, failed_files, file_tasks_json, created_at, updated_at) "
        "SELECT '', '', task_id, collection, status, total_files, processed_files, successful_files, "
        "failed_files, file_tasks_json, created_at, updated_at FROM tasks__old",
    ),
    (
        "collection_config",
        """CREATE TABLE collection_config (
                tenant_id TEXT NOT NULL DEFAULT '',
                instance_id TEXT NOT NULL DEFAULT '',
                collection TEXT NOT NULL,
                embedding_provider TEXT NOT NULL,
                embedding_model TEXT NOT NULL,
                embedding_dim INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (tenant_id, instance_id, collection)
            )""",
        "INSERT INTO collection_config (tenant_id, instance_id, collection, embedding_provider, "
        "embedding_model, embedding_dim, created_at) "
        "SELECT '', '', collection, embedding_provider, embedding_model, embedding_dim, created_at "
        "FROM collection_config__old",
    ),
    (
        "settings",
        """CREATE TABLE settings (
                tenant_id TEXT NOT NULL DEFAULT '',
                instance_id TEXT NOT NULL DEFAULT '',
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY (tenant_id, instance_id, key)
            )""",
        "INSERT INTO settings (tenant_id, instance_id, key, value) "
        "SELECT '', '', key, value FROM settings__old",
    ),
)


class SqliteKnowledgeMetadata(LocalRelationalStore):
    def __init__(self, db_path: Path):
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        super().__init__(str(db_path))
        self._init_schema()

    def _scope(self) -> tuple[str, str]:
        return current_scope()

    def _on_connection_opened(self, conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")

    def _init_schema(self) -> None:
        conn = self._get_conn()
        conn.executescript(_SCHEMA_SQL)
        try:
            conn.execute("ALTER TABLE documents ADD COLUMN preview TEXT NOT NULL DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        self._migrate_scope_columns(conn)
        conn.executescript(_INDEX_SQL)
        conn.commit()

    def _migrate_scope_columns(self, conn: sqlite3.Connection) -> None:
        for table, create_sql, copy_sql in _MIGRATIONS:
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
            if not cols or "tenant_id" in cols:
                continue
            conn.execute(f"ALTER TABLE {table} RENAME TO {table}__old")
            conn.execute(create_sql)
            conn.execute(copy_sql)
            conn.execute(f"DROP TABLE {table}__old")

    async def ensure_ready(self) -> None:
        return

    async def add_document(self, collection: str, filename: str, chunk_count: int, preview: str = "") -> None:
        now = utc_now_iso()
        tenant_id, inst_id = self._scope()
        await self.execute(
            """INSERT OR REPLACE INTO documents
               (tenant_id, instance_id, collection, filename, chunk_count, status, ingested_at, preview)
               VALUES (?, ?, ?, ?, ?, 'indexed', ?, ?)""",
            (tenant_id, inst_id, collection, filename, chunk_count, now, preview),
        )
        await self.commit()

    async def mark_deleting(self, collection: str, filename: str) -> bool:
        tenant_id, inst_id = self._scope()
        await self.execute(
            "UPDATE documents SET status='deleting' "
            "WHERE tenant_id=? AND instance_id=? AND collection=? AND filename=?",
            (tenant_id, inst_id, collection, filename),
        )
        ok = self._last_rowcount > 0
        await self.commit()
        return ok

    async def remove_document(self, collection: str, filename: str) -> None:
        tenant_id, inst_id = self._scope()
        await self.execute(
            "DELETE FROM documents WHERE tenant_id=? AND instance_id=? AND collection=? AND filename=?",
            (tenant_id, inst_id, collection, filename),
        )
        await self.commit()

    async def list_documents(self, collection: str) -> list[dict[str, Any]]:
        tenant_id, inst_id = self._scope()
        rows = await self.fetchall(
            "SELECT filename, chunk_count, status, ingested_at, preview FROM documents "
            "WHERE tenant_id=? AND instance_id=? AND collection=? AND status != 'deleting' "
            "ORDER BY ingested_at DESC",
            (tenant_id, inst_id, collection),
        )
        return [dict(r) for r in rows]

    async def get_deleting_documents(self) -> list[dict[str, Any]]:
        tenant_id, inst_id = self._scope()
        rows = await self.fetchall(
            "SELECT collection, filename FROM documents "
            "WHERE tenant_id=? AND instance_id=? AND status='deleting'",
            (tenant_id, inst_id),
        )
        return [dict(r) for r in rows]

    async def document_exists(self, collection: str, filename: str) -> bool:
        tenant_id, inst_id = self._scope()
        row = await self.fetchone(
            "SELECT 1 AS one FROM documents "
            "WHERE tenant_id=? AND instance_id=? AND collection=? AND filename=? AND status != 'deleting'",
            (tenant_id, inst_id, collection, filename),
        )
        return row is not None

    async def create_task(
        self, task_id: str, collection: str, total_files: int, file_tasks: dict[str, dict]
    ) -> dict[str, Any]:
        now = utc_now_iso()
        tenant_id, inst_id = self._scope()
        await self.execute(
            """INSERT INTO tasks (tenant_id, instance_id, task_id, collection, status, total_files,
               processed_files, successful_files, failed_files, file_tasks_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'pending', ?, 0, 0, 0, ?, ?, ?)""",
            (tenant_id, inst_id, task_id, collection, total_files, json.dumps(file_tasks), now, now),
        )
        await self.commit()
        return await self.get_task(task_id)

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        tenant_id, inst_id = self._scope()
        row = await self.fetchone(
            "SELECT * FROM tasks WHERE tenant_id=? AND instance_id=? AND task_id=?",
            (tenant_id, inst_id, task_id),
        )
        if not row:
            return None
        task = dict(row)
        task["file_tasks"] = json.loads(task.pop("file_tasks_json"))
        return task

    async def update_task(self, task_id: str, **kwargs: Any) -> None:
        now = utc_now_iso()
        tenant_id, inst_id = self._scope()
        if "file_tasks" in kwargs:
            kwargs["file_tasks_json"] = json.dumps(kwargs.pop("file_tasks"))
        kwargs["updated_at"] = now
        set_clause = ", ".join(f"{k}=?" for k in kwargs)
        values = list(kwargs.values()) + [tenant_id, inst_id, task_id]
        await self.execute(
            f"UPDATE tasks SET {set_clause} WHERE tenant_id=? AND instance_id=? AND task_id=?",
            values,
        )
        await self.commit()

    async def list_tasks(self, collection: str | None = None) -> list[dict[str, Any]]:
        tenant_id, inst_id = self._scope()
        if collection:
            rows = await self.fetchall(
                "SELECT * FROM tasks WHERE tenant_id=? AND instance_id=? AND collection=? "
                "ORDER BY created_at DESC",
                (tenant_id, inst_id, collection),
            )
        else:
            rows = await self.fetchall(
                "SELECT * FROM tasks WHERE tenant_id=? AND instance_id=? ORDER BY created_at DESC",
                (tenant_id, inst_id),
            )
        result: list[dict[str, Any]] = []
        for r in rows:
            task = dict(r)
            task["file_tasks"] = json.loads(task.pop("file_tasks_json"))
            result.append(task)
        return result

    async def recover_stale_tasks(self) -> int:
        now = utc_now_iso()
        tenant_id, inst_id = self._scope()
        rows = await self.fetchall(
            "SELECT task_id, file_tasks_json FROM tasks "
            "WHERE tenant_id=? AND instance_id=? AND status IN ('running', 'pending')",
            (tenant_id, inst_id),
        )
        count = 0
        for row in rows:
            task_id = row["task_id"]
            file_tasks = mark_file_tasks_interrupted(normalize_file_tasks(row["file_tasks_json"]))
            await self.execute(
                "UPDATE tasks SET status='failed', file_tasks_json=?, updated_at=? "
                "WHERE tenant_id=? AND instance_id=? AND task_id=?",
                (json.dumps(file_tasks), now, tenant_id, inst_id, task_id),
            )
            count += 1
        await self.commit()
        return count

    async def purge_old_tasks(self, max_age_days: int = 7) -> int:
        tenant_id, inst_id = self._scope()
        await self.execute(
            "DELETE FROM tasks WHERE tenant_id=? AND instance_id=? AND updated_at < datetime('now', ?)",
            (tenant_id, inst_id, f"-{max_age_days} days"),
        )
        n = self._last_rowcount
        await self.commit()
        return n

    async def get_collection_config(self, collection: str) -> dict[str, Any] | None:
        tenant_id, inst_id = self._scope()
        return await self.fetchone(
            "SELECT * FROM collection_config WHERE tenant_id=? AND instance_id=? AND collection=?",
            (tenant_id, inst_id, collection),
        )

    async def set_collection_config(
        self, collection: str, embedding_provider: str, embedding_model: str, embedding_dim: int
    ) -> None:
        now = utc_now_iso()
        tenant_id, inst_id = self._scope()
        await self.execute(
            """INSERT OR IGNORE INTO collection_config
               (tenant_id, instance_id, collection, embedding_provider, embedding_model, embedding_dim, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (tenant_id, inst_id, collection, embedding_provider, embedding_model, embedding_dim, now),
        )
        await self.commit()

    async def list_all_collection_configs(self) -> list[str]:
        tenant_id, inst_id = self._scope()
        rows = await self.fetchall(
            "SELECT collection FROM collection_config WHERE tenant_id=? AND instance_id=?",
            (tenant_id, inst_id),
        )
        return [dict(r)["collection"] for r in rows]

    async def delete_collection_metadata(self, collection: str) -> None:
        tenant_id, inst_id = self._scope()
        await self.execute(
            "DELETE FROM documents WHERE tenant_id=? AND instance_id=? AND collection=?",
            (tenant_id, inst_id, collection),
        )
        await self.execute(
            "DELETE FROM tasks WHERE tenant_id=? AND instance_id=? AND collection=?",
            (tenant_id, inst_id, collection),
        )
        await self.execute(
            "DELETE FROM collection_config WHERE tenant_id=? AND instance_id=? AND collection=?",
            (tenant_id, inst_id, collection),
        )
        await self.commit()

    async def get_setting(self, key: str, default: str = "") -> str:
        tenant_id, inst_id = self._scope()
        row = await self.fetchone(
            "SELECT value FROM settings WHERE tenant_id=? AND instance_id=? AND key=?",
            (tenant_id, inst_id, key),
        )
        return row["value"] if row else default

    async def set_setting(self, key: str, value: str) -> None:
        tenant_id, inst_id = self._scope()
        await self.execute(
            "INSERT OR REPLACE INTO settings (tenant_id, instance_id, key, value) VALUES (?, ?, ?, ?)",
            (tenant_id, inst_id, key, value),
        )
        await self.commit()

    async def get_all_settings(self) -> dict[str, str]:
        tenant_id, inst_id = self._scope()
        rows = await self.fetchall(
            "SELECT key, value FROM settings WHERE tenant_id=? AND instance_id=?",
            (tenant_id, inst_id),
        )
        return {dict(r)["key"]: dict(r)["value"] for r in rows}


MetadataDB = SqliteKnowledgeMetadata
