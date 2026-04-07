"""SQLite metadata for knowledge (local storage.mode).

Uses :class:`cuga.backend.storage.relational.local.LocalRelationalStore` async I/O
(``await execute`` / ``fetchone`` / ``fetchall``) so callers do not block the event loop.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from cuga.backend.knowledge.metadata.base import utc_now_iso
from cuga.backend.storage.relational.local import LocalRelationalStore


class SqliteKnowledgeMetadata(LocalRelationalStore):
    def __init__(self, db_path: Path):
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        super().__init__(str(db_path))
        self._init_schema()

    def _on_connection_opened(self, conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")

    def _init_schema(self) -> None:
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS documents (
                collection TEXT NOT NULL,
                filename TEXT NOT NULL,
                chunk_count INTEGER DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'indexed',
                ingested_at TEXT NOT NULL,
                preview TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (collection, filename)
            );

            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                collection TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','running','completed','failed','cancelled')),
                total_files INTEGER NOT NULL DEFAULT 0,
                processed_files INTEGER NOT NULL DEFAULT 0,
                successful_files INTEGER NOT NULL DEFAULT 0,
                failed_files INTEGER NOT NULL DEFAULT 0,
                file_tasks_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS collection_config (
                collection TEXT PRIMARY KEY,
                embedding_provider TEXT NOT NULL,
                embedding_model TEXT NOT NULL,
                embedding_dim INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_tasks_collection_status
                ON tasks(collection, status, updated_at);
            CREATE INDEX IF NOT EXISTS idx_docs_collection
                ON documents(collection, status);
        """)
        try:
            conn.execute("ALTER TABLE documents ADD COLUMN preview TEXT NOT NULL DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        conn.commit()

    async def ensure_ready(self) -> None:
        return

    async def add_document(self, collection: str, filename: str, chunk_count: int, preview: str = "") -> None:
        now = utc_now_iso()
        await self.execute(
            """INSERT OR REPLACE INTO documents (collection, filename, chunk_count, status, ingested_at, preview)
               VALUES (?, ?, ?, 'indexed', ?, ?)""",
            (collection, filename, chunk_count, now, preview),
        )
        await self.commit()

    async def mark_deleting(self, collection: str, filename: str) -> bool:
        await self.execute(
            "UPDATE documents SET status='deleting' WHERE collection=? AND filename=?",
            (collection, filename),
        )
        ok = self._last_rowcount > 0
        await self.commit()
        return ok

    async def remove_document(self, collection: str, filename: str) -> None:
        await self.execute(
            "DELETE FROM documents WHERE collection=? AND filename=?",
            (collection, filename),
        )
        await self.commit()

    async def list_documents(self, collection: str) -> list[dict[str, Any]]:
        rows = await self.fetchall(
            "SELECT filename, chunk_count, status, ingested_at, preview FROM documents "
            "WHERE collection=? AND status != 'deleting' ORDER BY ingested_at DESC",
            (collection,),
        )
        return [dict(r) for r in rows]

    async def get_deleting_documents(self) -> list[dict[str, Any]]:
        rows = await self.fetchall(
            "SELECT collection, filename FROM documents WHERE status='deleting'",
        )
        return [dict(r) for r in rows]

    async def document_exists(self, collection: str, filename: str) -> bool:
        row = await self.fetchone(
            "SELECT 1 AS one FROM documents WHERE collection=? AND filename=? AND status != 'deleting'",
            (collection, filename),
        )
        return row is not None

    async def create_task(
        self, task_id: str, collection: str, total_files: int, file_tasks: dict[str, dict]
    ) -> dict[str, Any]:
        now = utc_now_iso()
        await self.execute(
            """INSERT INTO tasks (task_id, collection, status, total_files, processed_files,
               successful_files, failed_files, file_tasks_json, created_at, updated_at)
               VALUES (?, ?, 'pending', ?, 0, 0, 0, ?, ?, ?)""",
            (task_id, collection, total_files, json.dumps(file_tasks), now, now),
        )
        await self.commit()
        return await self.get_task(task_id)

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        row = await self.fetchone("SELECT * FROM tasks WHERE task_id=?", (task_id,))
        if not row:
            return None
        task = dict(row)
        task["file_tasks"] = json.loads(task.pop("file_tasks_json"))
        return task

    async def update_task(self, task_id: str, **kwargs: Any) -> None:
        now = utc_now_iso()
        if "file_tasks" in kwargs:
            kwargs["file_tasks_json"] = json.dumps(kwargs.pop("file_tasks"))
        kwargs["updated_at"] = now
        set_clause = ", ".join(f"{k}=?" for k in kwargs)
        values = list(kwargs.values()) + [task_id]
        await self.execute(f"UPDATE tasks SET {set_clause} WHERE task_id=?", values)
        await self.commit()

    async def list_tasks(self, collection: str | None = None) -> list[dict[str, Any]]:
        if collection:
            rows = await self.fetchall(
                "SELECT * FROM tasks WHERE collection=? ORDER BY created_at DESC",
                (collection,),
            )
        else:
            rows = await self.fetchall("SELECT * FROM tasks ORDER BY created_at DESC")
        result: list[dict[str, Any]] = []
        for r in rows:
            task = dict(r)
            task["file_tasks"] = json.loads(task.pop("file_tasks_json"))
            result.append(task)
        return result

    async def recover_stale_tasks(self) -> int:
        now = utc_now_iso()
        rows = await self.fetchall(
            "SELECT task_id, file_tasks_json FROM tasks WHERE status IN ('running', 'pending')",
        )
        count = 0
        for row in rows:
            task_id = row["task_id"]
            file_tasks = json.loads(row["file_tasks_json"])
            for ft in file_tasks.values():
                if ft["status"] in ("pending", "processing"):
                    ft["status"] = "failed"
                    ft["error"] = "interrupted by server restart"
            await self.execute(
                "UPDATE tasks SET status='failed', file_tasks_json=?, updated_at=? WHERE task_id=?",
                (json.dumps(file_tasks), now, task_id),
            )
            count += 1
        await self.commit()
        return count

    async def purge_old_tasks(self, max_age_days: int = 7) -> int:
        await self.execute(
            "DELETE FROM tasks WHERE updated_at < datetime('now', ?)",
            (f"-{max_age_days} days",),
        )
        n = self._last_rowcount
        await self.commit()
        return n

    async def get_collection_config(self, collection: str) -> dict[str, Any] | None:
        return await self.fetchone("SELECT * FROM collection_config WHERE collection=?", (collection,))

    async def set_collection_config(
        self, collection: str, embedding_provider: str, embedding_model: str, embedding_dim: int
    ) -> None:
        now = utc_now_iso()
        await self.execute(
            """INSERT OR IGNORE INTO collection_config
               (collection, embedding_provider, embedding_model, embedding_dim, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (collection, embedding_provider, embedding_model, embedding_dim, now),
        )
        await self.commit()

    async def list_all_collection_configs(self) -> list[str]:
        rows = await self.fetchall("SELECT collection FROM collection_config")
        return [dict(r)["collection"] for r in rows]

    async def delete_collection_metadata(self, collection: str) -> None:
        await self.execute("DELETE FROM documents WHERE collection=?", (collection,))
        await self.execute("DELETE FROM tasks WHERE collection=?", (collection,))
        await self.execute("DELETE FROM collection_config WHERE collection=?", (collection,))
        await self.commit()

    async def get_setting(self, key: str, default: str = "") -> str:
        row = await self.fetchone("SELECT value FROM settings WHERE key=?", (key,))
        return row["value"] if row else default

    async def set_setting(self, key: str, value: str) -> None:
        await self.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )
        await self.commit()

    async def get_all_settings(self) -> dict[str, str]:
        rows = await self.fetchall("SELECT key, value FROM settings")
        return {dict(r)["key"]: dict(r)["value"] for r in rows}


MetadataDB = SqliteKnowledgeMetadata
