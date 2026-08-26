"""PostgreSQL metadata for knowledge (storage.mode=prod) via asyncpg pool (ProdRelationalStore)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import psycopg

from cuga.backend.knowledge.metadata.base import (
    current_scope,
    iso_cutoff_days_ago,
    mark_file_tasks_interrupted,
    normalize_file_tasks,
    utc_now_iso,
)
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

_TABLE_PKS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (_DOC, ("tenant_id", "instance_id", "collection", "filename")),
    (_TASK, ("tenant_id", "instance_id", "task_id")),
    (_COLL, ("tenant_id", "instance_id", "collection")),
    (_SET, ("tenant_id", "instance_id", "key")),
)

_SCHEMA_STATEMENTS = (
    f"""
            CREATE TABLE IF NOT EXISTS {_DOC} (
                tenant_id TEXT NOT NULL DEFAULT '',
                instance_id TEXT NOT NULL DEFAULT '',
                collection TEXT NOT NULL,
                filename TEXT NOT NULL,
                chunk_count BIGINT NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'indexed',
                ingested_at TEXT NOT NULL,
                preview TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (tenant_id, instance_id, collection, filename)
            )
            """,
    f"""
            CREATE TABLE IF NOT EXISTS {_TASK} (
                tenant_id TEXT NOT NULL DEFAULT '',
                instance_id TEXT NOT NULL DEFAULT '',
                task_id TEXT NOT NULL,
                collection TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','running','completed','failed','cancelled')),
                total_files BIGINT NOT NULL DEFAULT 0,
                processed_files BIGINT NOT NULL DEFAULT 0,
                successful_files BIGINT NOT NULL DEFAULT 0,
                failed_files BIGINT NOT NULL DEFAULT 0,
                file_tasks_json TEXT NOT NULL DEFAULT '{{}}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (tenant_id, instance_id, task_id)
            )
            """,
    f"""
            CREATE TABLE IF NOT EXISTS {_COLL} (
                tenant_id TEXT NOT NULL DEFAULT '',
                instance_id TEXT NOT NULL DEFAULT '',
                collection TEXT NOT NULL,
                embedding_provider TEXT NOT NULL,
                embedding_model TEXT NOT NULL,
                embedding_dim BIGINT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (tenant_id, instance_id, collection)
            )
            """,
    f"""
            CREATE TABLE IF NOT EXISTS {_SET} (
                tenant_id TEXT NOT NULL DEFAULT '',
                instance_id TEXT NOT NULL DEFAULT '',
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY (tenant_id, instance_id, key)
            )
            """,
)

_INDEX_STATEMENTS = (
    "DROP INDEX IF EXISTS idx_cuga_kn_meta_task_cs",
    "DROP INDEX IF EXISTS idx_cuga_kn_meta_doc_cs",
    f"CREATE INDEX IF NOT EXISTS idx_cuga_kn_meta_task_cs ON {_TASK}(tenant_id, instance_id, collection, status, updated_at)",
    f"CREATE INDEX IF NOT EXISTS idx_cuga_kn_meta_doc_cs ON {_DOC}(tenant_id, instance_id, collection, status)",
)


class PostgresKnowledgeMetadata(ProdRelationalStore):
    def __init__(self, postgres_url: str):
        super().__init__(postgres_url, "knowledge_metadata")
        self._schema_initialized = False
        self._schema_lock = asyncio.Lock()

    def _scope(self) -> tuple[str, str]:
        return current_scope()

    async def ensure_ready(self) -> None:
        if self._schema_initialized:
            return
        async with self._schema_lock:
            if self._schema_initialized:
                return
            for stmt in _SCHEMA_STATEMENTS:
                await self.execute(stmt.strip())
            await self._migrate_scope_columns()
            for stmt in _INDEX_STATEMENTS:
                await self.execute(stmt.strip())
            await self.commit()
            self._schema_initialized = True

    async def _migrate_scope_columns(self) -> None:
        """Add tenant/instance columns and composite PKs on tables created before isolation."""
        for table, pk_cols in _TABLE_PKS:
            await self.execute(
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT ''"
            )
            await self.execute(
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS instance_id TEXT NOT NULL DEFAULT ''"
            )
            current = await self.fetchall(
                """
                SELECT a.attname AS attname
                FROM pg_index i
                JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                WHERE i.indrelid = to_regclass(?) AND i.indisprimary
                ORDER BY array_position(i.indkey, a.attnum)
                """,
                (table,),
            )
            current_pk = tuple(r["attname"] for r in current)
            if current_pk == pk_cols:
                continue
            await self.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {table}_pkey")
            await self.execute(f"ALTER TABLE {table} ADD PRIMARY KEY ({', '.join(pk_cols)})")

    async def add_document(self, collection: str, filename: str, chunk_count: int, preview: str = "") -> None:
        now = utc_now_iso()
        tenant_id, inst_id = self._scope()
        await self.execute(
            f"""
            INSERT INTO {_DOC} (tenant_id, instance_id, collection, filename, chunk_count, status, ingested_at, preview)
            VALUES (?, ?, ?, ?, ?, 'indexed', ?, ?)
            ON CONFLICT (tenant_id, instance_id, collection, filename) DO UPDATE SET
                chunk_count = EXCLUDED.chunk_count,
                status = EXCLUDED.status,
                ingested_at = EXCLUDED.ingested_at,
                preview = EXCLUDED.preview
            """,
            (tenant_id, inst_id, collection, filename, chunk_count, now, preview),
        )
        await self.commit()

    async def mark_deleting(self, collection: str, filename: str) -> bool:
        tenant_id, inst_id = self._scope()
        await self.execute(
            f"UPDATE {_DOC} SET status = 'deleting' "
            f"WHERE tenant_id = ? AND instance_id = ? AND collection = ? AND filename = ?",
            (tenant_id, inst_id, collection, filename),
        )
        ok = self._last_rowcount > 0
        await self.commit()
        return ok

    async def remove_document(self, collection: str, filename: str) -> None:
        tenant_id, inst_id = self._scope()
        await self.execute(
            f"DELETE FROM {_DOC} WHERE tenant_id = ? AND instance_id = ? AND collection = ? AND filename = ?",
            (tenant_id, inst_id, collection, filename),
        )
        await self.commit()

    async def list_documents(self, collection: str) -> list[dict[str, Any]]:
        tenant_id, inst_id = self._scope()
        return await self.fetchall(
            f"SELECT filename, chunk_count, status, ingested_at, preview FROM {_DOC} "
            f"WHERE tenant_id = ? AND instance_id = ? AND collection = ? AND status != 'deleting' "
            f"ORDER BY ingested_at DESC",
            (tenant_id, inst_id, collection),
        )

    async def get_deleting_documents(self) -> list[dict[str, Any]]:
        tenant_id, inst_id = self._scope()
        return await self.fetchall(
            f"SELECT collection, filename FROM {_DOC} "
            f"WHERE tenant_id = ? AND instance_id = ? AND status = 'deleting'",
            (tenant_id, inst_id),
        )

    async def document_exists(self, collection: str, filename: str) -> bool:
        tenant_id, inst_id = self._scope()
        row = await self.fetchone(
            f"SELECT 1 AS one FROM {_DOC} "
            f"WHERE tenant_id = ? AND instance_id = ? AND collection = ? AND filename = ? "
            f"AND status != 'deleting' LIMIT 1",
            (tenant_id, inst_id, collection, filename),
        )
        return row is not None

    async def create_task(
        self, task_id: str, collection: str, total_files: int, file_tasks: dict[str, dict]
    ) -> dict[str, Any]:
        now = utc_now_iso()
        tenant_id, inst_id = self._scope()
        await self.execute(
            f"""
            INSERT INTO {_TASK} (
                tenant_id, instance_id, task_id, collection, status, total_files, processed_files,
                successful_files, failed_files, file_tasks_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'pending', ?, 0, 0, 0, ?, ?, ?)
            """,
            (tenant_id, inst_id, task_id, collection, total_files, json.dumps(file_tasks), now, now),
        )
        await self.commit()
        return await self.get_task(task_id)

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        tenant_id, inst_id = self._scope()
        row = await self.fetchone(
            f"SELECT * FROM {_TASK} WHERE tenant_id = ? AND instance_id = ? AND task_id = ?",
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
        cols = [k for k in kwargs if k in _TASK_UPDATE_COLS]
        if not cols:
            return
        set_clause = ", ".join(f"{k} = ?" for k in cols)
        values = [kwargs[k] for k in cols] + [tenant_id, inst_id, task_id]
        await self.execute(
            f"UPDATE {_TASK} SET {set_clause} WHERE tenant_id = ? AND instance_id = ? AND task_id = ?",
            values,
        )
        await self.commit()

    async def list_tasks(self, collection: str | None = None) -> list[dict[str, Any]]:
        tenant_id, inst_id = self._scope()
        if collection:
            rows = await self.fetchall(
                f"SELECT * FROM {_TASK} WHERE tenant_id = ? AND instance_id = ? AND collection = ? "
                f"ORDER BY created_at DESC",
                (tenant_id, inst_id, collection),
            )
        else:
            rows = await self.fetchall(
                f"SELECT * FROM {_TASK} WHERE tenant_id = ? AND instance_id = ? ORDER BY created_at DESC",
                (tenant_id, inst_id),
            )
        out: list[dict[str, Any]] = []
        for r in rows:
            task = dict(r)
            task["file_tasks"] = json.loads(task.pop("file_tasks_json"))
            out.append(task)
        return out

    async def recover_stale_tasks(self) -> int:
        now = utc_now_iso()
        tenant_id, inst_id = self._scope()
        rows = await self.fetchall(
            f"SELECT task_id, file_tasks_json FROM {_TASK} "
            f"WHERE tenant_id = ? AND instance_id = ? AND status IN ('running', 'pending')",
            (tenant_id, inst_id),
        )
        count = 0
        for row in rows:
            task_id = row["task_id"]
            file_tasks = mark_file_tasks_interrupted(normalize_file_tasks(row["file_tasks_json"]))
            await self.execute(
                f"UPDATE {_TASK} SET status = ?, file_tasks_json = ?, updated_at = ? "
                f"WHERE tenant_id = ? AND instance_id = ? AND task_id = ?",
                ("failed", json.dumps(file_tasks), now, tenant_id, inst_id, task_id),
            )
            count += 1
        await self.commit()
        return count

    async def purge_old_tasks(self, max_age_days: int = 7) -> int:
        cutoff = iso_cutoff_days_ago(max_age_days)
        tenant_id, inst_id = self._scope()
        await self.execute(
            f"DELETE FROM {_TASK} WHERE tenant_id = ? AND instance_id = ? AND updated_at < ?",
            (tenant_id, inst_id, cutoff),
        )
        n = self._last_rowcount
        await self.commit()
        return n

    async def get_collection_config(self, collection: str) -> dict[str, Any] | None:
        tenant_id, inst_id = self._scope()
        return await self.fetchone(
            f"SELECT * FROM {_COLL} WHERE tenant_id = ? AND instance_id = ? AND collection = ?",
            (tenant_id, inst_id, collection),
        )

    async def set_collection_config(
        self, collection: str, embedding_provider: str, embedding_model: str, embedding_dim: int
    ) -> None:
        now = utc_now_iso()
        tenant_id, inst_id = self._scope()
        await self.execute(
            f"""
            INSERT INTO {_COLL} (
                tenant_id, instance_id, collection, embedding_provider, embedding_model, embedding_dim, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (tenant_id, instance_id, collection) DO NOTHING
            """,
            (tenant_id, inst_id, collection, embedding_provider, embedding_model, embedding_dim, now),
        )
        await self.commit()

    async def list_all_collection_configs(self) -> list[str]:
        tenant_id, inst_id = self._scope()
        rows = await self.fetchall(
            f"SELECT collection FROM {_COLL} WHERE tenant_id = ? AND instance_id = ?",
            (tenant_id, inst_id),
        )
        return [r["collection"] for r in rows]

    async def delete_collection_metadata(self, collection: str) -> None:
        tenant_id, inst_id = self._scope()
        await self.execute(
            f"DELETE FROM {_DOC} WHERE tenant_id = ? AND instance_id = ? AND collection = ?",
            (tenant_id, inst_id, collection),
        )
        await self.execute(
            f"DELETE FROM {_TASK} WHERE tenant_id = ? AND instance_id = ? AND collection = ?",
            (tenant_id, inst_id, collection),
        )
        await self.execute(
            f"DELETE FROM {_COLL} WHERE tenant_id = ? AND instance_id = ? AND collection = ?",
            (tenant_id, inst_id, collection),
        )
        await self.commit()

    async def get_setting(self, key: str, default: str = "") -> str:
        tenant_id, inst_id = self._scope()
        row = await self.fetchone(
            f"SELECT value FROM {_SET} WHERE tenant_id = ? AND instance_id = ? AND key = ?",
            (tenant_id, inst_id, key),
        )
        return row["value"] if row else default

    async def set_setting(self, key: str, value: str) -> None:
        tenant_id, inst_id = self._scope()
        await self.execute(
            f"""
            INSERT INTO {_SET} (tenant_id, instance_id, key, value) VALUES (?, ?, ?, ?)
            ON CONFLICT (tenant_id, instance_id, key) DO UPDATE SET value = EXCLUDED.value
            """,
            (tenant_id, inst_id, key, value),
        )
        await self.commit()

    async def get_all_settings(self) -> dict[str, str]:
        tenant_id, inst_id = self._scope()
        rows = await self.fetchall(
            f"SELECT key, value FROM {_SET} WHERE tenant_id = ? AND instance_id = ?",
            (tenant_id, inst_id),
        )
        return {r["key"]: r["value"] for r in rows}


def truncate_knowledge_metadata_tables(postgres_url: str) -> None:
    """Remove all knowledge metadata rows (demo reset). Does not drop vector tables."""
    with psycopg.connect(postgres_url) as conn:
        conn.execute(f"TRUNCATE {_DOC}, {_TASK}, {_COLL}, {_SET}")
        conn.commit()
