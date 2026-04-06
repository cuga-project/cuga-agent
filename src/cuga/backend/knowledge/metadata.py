"""SQLite metadata store for knowledge engine.

Tracks ingestion tasks, document inventory, collection configs, and settings.
The LangChain Indexing API handles chunk-level dedup; this module handles
document-level operations (list, delete, status) and task progress tracking.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class MetadataDB:
    """Lightweight SQLite store for knowledge metadata."""

    def __init__(self, db_path: Path):
        self._db_path = str(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self):
        with self._conn() as conn:
            conn.executescript("""
                PRAGMA journal_mode=WAL;
                PRAGMA busy_timeout=5000;

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
            # Migration: add preview column to existing databases
            try:
                conn.execute("ALTER TABLE documents ADD COLUMN preview TEXT NOT NULL DEFAULT ''")
            except sqlite3.OperationalError:
                pass  # Column already exists

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # --- Documents ---

    def add_document(self, collection: str, filename: str, chunk_count: int, preview: str = "") -> None:
        now = _now()
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO documents (collection, filename, chunk_count, status, ingested_at, preview)
                   VALUES (?, ?, ?, 'indexed', ?, ?)""",
                (collection, filename, chunk_count, now, preview),
            )

    def mark_deleting(self, collection: str, filename: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE documents SET status='deleting' WHERE collection=? AND filename=?",
                (collection, filename),
            )
            return cur.rowcount > 0

    def remove_document(self, collection: str, filename: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM documents WHERE collection=? AND filename=?",
                (collection, filename),
            )

    def list_documents(self, collection: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT filename, chunk_count, status, ingested_at, preview FROM documents "
                "WHERE collection=? AND status != 'deleting' ORDER BY ingested_at DESC",
                (collection,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_deleting_documents(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT collection, filename FROM documents WHERE status='deleting'"
            ).fetchall()
            return [dict(r) for r in rows]

    def document_exists(self, collection: str, filename: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM documents WHERE collection=? AND filename=? AND status != 'deleting'",
                (collection, filename),
            ).fetchone()
            return row is not None

    # --- Tasks ---

    def create_task(
        self, task_id: str, collection: str, total_files: int, file_tasks: dict[str, dict]
    ) -> dict[str, Any]:
        now = _now()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO tasks (task_id, collection, status, total_files, processed_files,
                   successful_files, failed_files, file_tasks_json, created_at, updated_at)
                   VALUES (?, ?, 'pending', ?, 0, 0, 0, ?, ?, ?)""",
                (task_id, collection, total_files, json.dumps(file_tasks), now, now),
            )
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if not row:
                return None
            task = dict(row)
            task["file_tasks"] = json.loads(task.pop("file_tasks_json"))
            return task

    def update_task(self, task_id: str, **kwargs) -> None:
        now = _now()
        if "file_tasks" in kwargs:
            kwargs["file_tasks_json"] = json.dumps(kwargs.pop("file_tasks"))
        kwargs["updated_at"] = now
        set_clause = ", ".join(f"{k}=?" for k in kwargs)
        values = list(kwargs.values()) + [task_id]
        with self._conn() as conn:
            conn.execute(f"UPDATE tasks SET {set_clause} WHERE task_id=?", values)

    def list_tasks(self, collection: str | None = None) -> list[dict[str, Any]]:
        with self._conn() as conn:
            if collection:
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE collection=? ORDER BY created_at DESC",
                    (collection,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
            result = []
            for r in rows:
                task = dict(r)
                task["file_tasks"] = json.loads(task.pop("file_tasks_json"))
                result.append(task)
            return result

    def recover_stale_tasks(self) -> int:
        """Mark stale running/pending tasks as failed on startup (crash recovery)."""
        now = _now()
        with self._conn() as conn:
            # Get running or pending tasks — both are orphaned after a restart
            rows = conn.execute(
                "SELECT task_id, file_tasks_json FROM tasks WHERE status IN ('running', 'pending')"
            ).fetchall()
            count = 0
            for row in rows:
                task_id = row["task_id"]
                file_tasks = json.loads(row["file_tasks_json"])
                # Mark pending/running files as failed
                for ft in file_tasks.values():
                    if ft["status"] in ("pending", "processing"):
                        ft["status"] = "failed"
                        ft["error"] = "interrupted by server restart"
                conn.execute(
                    "UPDATE tasks SET status='failed', file_tasks_json=?, updated_at=? WHERE task_id=?",
                    (json.dumps(file_tasks), now, task_id),
                )
                count += 1
            return count

    def purge_old_tasks(self, max_age_days: int = 7) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM tasks WHERE updated_at < datetime('now', ?)",
                (f"-{max_age_days} days",),
            )
            return cur.rowcount

    # --- Collection Config ---

    def get_collection_config(self, collection: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM collection_config WHERE collection=?", (collection,)).fetchone()
            return dict(row) if row else None

    def set_collection_config(
        self, collection: str, embedding_provider: str, embedding_model: str, embedding_dim: int
    ) -> None:
        now = _now()
        with self._conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO collection_config
                   (collection, embedding_provider, embedding_model, embedding_dim, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (collection, embedding_provider, embedding_model, embedding_dim, now),
            )

    def list_all_collection_configs(self) -> list[str]:
        """List all collection names that have a config entry."""
        with self._conn() as conn:
            rows = conn.execute("SELECT collection FROM collection_config").fetchall()
            return [r["collection"] for r in rows]

    def delete_collection_metadata(self, collection: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM documents WHERE collection=?", (collection,))
            conn.execute("DELETE FROM tasks WHERE collection=?", (collection,))
            conn.execute("DELETE FROM collection_config WHERE collection=?", (collection,))

    # --- Settings ---

    def get_setting(self, key: str, default: str = "") -> str:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )

    def get_all_settings(self) -> dict[str, str]:
        with self._conn() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
            return {r["key"]: r["value"] for r in rows}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
