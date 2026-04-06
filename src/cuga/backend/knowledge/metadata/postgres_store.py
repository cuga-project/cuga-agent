"""PostgreSQL metadata for knowledge (storage.mode=prod) via asyncpg pool (ProdRelationalStore)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import psycopg

from cuga.backend.knowledge.metadata.base import iso_cutoff_days_ago, utc_now_iso
from cuga.backend.storage.relational.prod import ProdRelationalStore

_DOC = "cuga_knowledge_meta_documents"
_TASK = "cuga_knowledge_meta_tasks"
_COLL = "cuga_knowledge_meta_collection_config"
_SET = "cuga_knowledge_meta_settings"

_TASK_UPDATE_COLS = frozenset(
    {
        "status",
        "total_files",
        "processed_files",
        "successful_files",
        "failed_files",
        "file_tasks_json",
        "updated_at",
    }
)

_SCHEMA_STATEMENTS = (
    f"""
            CREATE TABLE IF NOT EXISTS {_DOC} (
                collection TEXT NOT NULL,
                filename TEXT NOT NULL,
                chunk_count BIGINT NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'indexed',
                ingested_at TEXT NOT NULL,
                preview TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (collection, filename)
            )
            """,
    f"""
            CREATE TABLE IF NOT EXISTS {_TASK} (
                task_id TEXT PRIMARY KEY,
                collection TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','running','completed','failed','cancelled')),
                total_files BIGINT NOT NULL DEFAULT 0,
                processed_files BIGINT NOT NULL DEFAULT 0,
                successful_files BIGINT NOT NULL DEFAULT 0,
                failed_files BIGINT NOT NULL DEFAULT 0,
                file_tasks_json TEXT NOT NULL DEFAULT '{{}}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
    f"""
            CREATE TABLE IF NOT EXISTS {_COLL} (
                collection TEXT PRIMARY KEY,
                embedding_provider TEXT NOT NULL,
                embedding_model TEXT NOT NULL,
                embedding_dim BIGINT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
    f"""
            CREATE TABLE IF NOT EXISTS {_SET} (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """,
    f"CREATE INDEX IF NOT EXISTS idx_cuga_kn_meta_task_cs ON {_TASK}(collection, status, updated_at)",
    f"CREATE INDEX IF NOT EXISTS idx_cuga_kn_meta_doc_cs ON {_DOC}(collection, status)",
)


class PostgresKnowledgeMetadata(ProdRelationalStore):
    def __init__(self, postgres_url: str):
        super().__init__(postgres_url, "knowledge_metadata")
        self._schema_initialized = False
        self._schema_lock = asyncio.Lock()

    async def ensure_ready(self) -> None:
        if self._schema_initialized:
            return
        async with self._schema_lock:
            if self._schema_initialized:
                return
            for stmt in _SCHEMA_STATEMENTS:
                await self.execute(stmt.strip())
            await self.commit()
            self._schema_initialized = True

    async def add_document(self, collection: str, filename: str, chunk_count: int, preview: str = "") -> None:
        now = utc_now_iso()
        await self.execute(
            f"""
            INSERT INTO {_DOC} (collection, filename, chunk_count, status, ingested_at, preview)
            VALUES (?, ?, ?, 'indexed', ?, ?)
            ON CONFLICT (collection, filename) DO UPDATE SET
                chunk_count = EXCLUDED.chunk_count,
                status = EXCLUDED.status,
                ingested_at = EXCLUDED.ingested_at,
                preview = EXCLUDED.preview
            """,
            (collection, filename, chunk_count, now, preview),
        )
        await self.commit()

    async def mark_deleting(self, collection: str, filename: str) -> bool:
        await self.execute(
            f"UPDATE {_DOC} SET status = 'deleting' WHERE collection = ? AND filename = ?",
            (collection, filename),
        )
        ok = self._last_rowcount > 0
        await self.commit()
        return ok

    async def remove_document(self, collection: str, filename: str) -> None:
        await self.execute(
            f"DELETE FROM {_DOC} WHERE collection = ? AND filename = ?",
            (collection, filename),
        )
        await self.commit()

    async def list_documents(self, collection: str) -> list[dict[str, Any]]:
        return await self.fetchall(
            f"SELECT filename, chunk_count, status, ingested_at, preview FROM {_DOC} "
            f"WHERE collection = ? AND status != 'deleting' ORDER BY ingested_at DESC",
            (collection,),
        )

    async def get_deleting_documents(self) -> list[dict[str, Any]]:
        return await self.fetchall(f"SELECT collection, filename FROM {_DOC} WHERE status = 'deleting'")

    async def document_exists(self, collection: str, filename: str) -> bool:
        row = await self.fetchone(
            f"SELECT 1 AS one FROM {_DOC} "
            f"WHERE collection = ? AND filename = ? AND status != 'deleting' LIMIT 1",
            (collection, filename),
        )
        return row is not None

    async def create_task(
        self, task_id: str, collection: str, total_files: int, file_tasks: dict[str, dict]
    ) -> dict[str, Any]:
        now = utc_now_iso()
        await self.execute(
            f"""
            INSERT INTO {_TASK} (
                task_id, collection, status, total_files, processed_files,
                successful_files, failed_files, file_tasks_json, created_at, updated_at
            ) VALUES (?, ?, 'pending', ?, 0, 0, 0, ?, ?, ?)
            """,
            (task_id, collection, total_files, json.dumps(file_tasks), now, now),
        )
        await self.commit()
        return await self.get_task(task_id)

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        row = await self.fetchone(f"SELECT * FROM {_TASK} WHERE task_id = ?", (task_id,))
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
        cols = [k for k in kwargs if k in _TASK_UPDATE_COLS]
        if not cols:
            return
        set_clause = ", ".join(f"{k} = ?" for k in cols)
        values = [kwargs[k] for k in cols] + [task_id]
        await self.execute(f"UPDATE {_TASK} SET {set_clause} WHERE task_id = ?", values)
        await self.commit()

    async def list_tasks(self, collection: str | None = None) -> list[dict[str, Any]]:
        if collection:
            rows = await self.fetchall(
                f"SELECT * FROM {_TASK} WHERE collection = ? ORDER BY created_at DESC",
                (collection,),
            )
        else:
            rows = await self.fetchall(f"SELECT * FROM {_TASK} ORDER BY created_at DESC")
        out: list[dict[str, Any]] = []
        for r in rows:
            task = dict(r)
            task["file_tasks"] = json.loads(task.pop("file_tasks_json"))
            out.append(task)
        return out

    async def recover_stale_tasks(self) -> int:
        now = utc_now_iso()
        rows = await self.fetchall(
            f"SELECT task_id, file_tasks_json FROM {_TASK} WHERE status IN ('running', 'pending')",
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
                f"UPDATE {_TASK} SET status = 'failed', file_tasks_json = ?, updated_at = ? WHERE task_id = ?",
                (json.dumps(file_tasks), now, task_id),
            )
            count += 1
        await self.commit()
        return count

    async def purge_old_tasks(self, max_age_days: int = 7) -> int:
        cutoff = iso_cutoff_days_ago(max_age_days)
        await self.execute(f"DELETE FROM {_TASK} WHERE updated_at < ?", (cutoff,))
        n = self._last_rowcount
        await self.commit()
        return n

    async def get_collection_config(self, collection: str) -> dict[str, Any] | None:
        return await self.fetchone(f"SELECT * FROM {_COLL} WHERE collection = ?", (collection,))

    async def set_collection_config(
        self, collection: str, embedding_provider: str, embedding_model: str, embedding_dim: int
    ) -> None:
        now = utc_now_iso()
        await self.execute(
            f"""
            INSERT INTO {_COLL} (collection, embedding_provider, embedding_model, embedding_dim, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (collection) DO NOTHING
            """,
            (collection, embedding_provider, embedding_model, embedding_dim, now),
        )
        await self.commit()

    async def list_all_collection_configs(self) -> list[str]:
        rows = await self.fetchall(f"SELECT collection FROM {_COLL}")
        return [r["collection"] for r in rows]

    async def delete_collection_metadata(self, collection: str) -> None:
        await self.execute(f"DELETE FROM {_DOC} WHERE collection = ?", (collection,))
        await self.execute(f"DELETE FROM {_TASK} WHERE collection = ?", (collection,))
        await self.execute(f"DELETE FROM {_COLL} WHERE collection = ?", (collection,))
        await self.commit()

    async def get_setting(self, key: str, default: str = "") -> str:
        row = await self.fetchone(f"SELECT value FROM {_SET} WHERE key = ?", (key,))
        return row["value"] if row else default

    async def set_setting(self, key: str, value: str) -> None:
        await self.execute(
            f"""
            INSERT INTO {_SET} (key, value) VALUES (?, ?)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """,
            (key, value),
        )
        await self.commit()

    async def get_all_settings(self) -> dict[str, str]:
        rows = await self.fetchall(f"SELECT key, value FROM {_SET}")
        return {r["key"]: r["value"] for r in rows}


def truncate_knowledge_metadata_tables(postgres_url: str) -> None:
    """Remove all knowledge metadata rows (demo reset). Does not drop vector tables."""
    with psycopg.connect(postgres_url) as conn:
        conn.execute(f"TRUNCATE {_DOC}, {_TASK}, {_COLL}, {_SET}")
        conn.commit()
