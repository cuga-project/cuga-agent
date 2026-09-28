"""Service-instance-scoped destructive cleanup helpers."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cuga.backend.storage.facade import get_storage_connection_params
from cuga.config import DBS_DIR, settings

_KNOWLEDGE_METADATA_TABLES = {
    "documents",
    "tasks",
    "collection_config",
    "settings",
    "cuga_knowledge_meta_documents",
    "cuga_knowledge_meta_tasks",
    "cuga_knowledge_meta_collection_config",
    "cuga_knowledge_meta_settings",
}


@dataclass(frozen=True)
class ServiceInstanceCleanupResult:
    service_instance_id: str
    dry_run: bool
    deleted_records: int
    tables: dict[str, int]


def _quote_sqlite_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _quote_pg_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _is_excluded_table(table_name: str) -> bool:
    return table_name in _KNOWLEDGE_METADATA_TABLES or table_name.endswith("__fts") or "__fts_" in table_name


def _load_sqlite_vec(conn: sqlite3.Connection) -> None:
    try:
        import sqlite_vec
    except ImportError:
        return

    conn.enable_load_extension(True)
    try:
        sqlite_vec.load(conn)
    finally:
        conn.enable_load_extension(False)


def _sqlite_tables_with_instance_id(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    tables: list[str] = []
    for (table_name,) in rows:
        if _is_excluded_table(table_name):
            continue
        columns = conn.execute(f"PRAGMA table_info({_quote_sqlite_identifier(table_name)})").fetchall()
        if any(col[1] == "instance_id" for col in columns):
            tables.append(table_name)
    return tables


async def _delete_sqlite_service_instance_records(
    local_db_path: str,
    service_instance_id: str,
    *,
    dry_run: bool,
) -> dict[str, int]:
    def _delete_sync() -> dict[str, int]:
        conn = sqlite3.connect(local_db_path)
        try:
            _load_sqlite_vec(conn)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            tables = _sqlite_tables_with_instance_id(conn)
            deleted: dict[str, int] = {}
            for table_name in tables:
                if dry_run:
                    cur = conn.execute(
                        f"SELECT COUNT(*) FROM {_quote_sqlite_identifier(table_name)} WHERE instance_id = ?",
                        (service_instance_id,),
                    )
                    deleted[table_name] = int(cur.fetchone()[0])
                    continue
                cur = conn.execute(
                    f"DELETE FROM {_quote_sqlite_identifier(table_name)} WHERE instance_id = ?",
                    (service_instance_id,),
                )
                deleted[table_name] = max(cur.rowcount, 0)
            if dry_run:
                conn.rollback()
            else:
                conn.commit()
            return deleted
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    import asyncio

    return await asyncio.to_thread(_delete_sync)


async def _postgres_tables_with_instance_id(pool: Any) -> list[tuple[str, str]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.table_schema, c.table_name
            FROM information_schema.columns c
            JOIN information_schema.tables t
              ON t.table_schema = c.table_schema
             AND t.table_name = c.table_name
            WHERE c.column_name = 'instance_id'
              AND t.table_type = 'BASE TABLE'
              AND c.table_schema NOT IN ('information_schema', 'pg_catalog')
              AND c.table_name NOT IN (
                  'documents',
                  'tasks',
                  'collection_config',
                  'settings',
                  'cuga_knowledge_meta_documents',
                  'cuga_knowledge_meta_tasks',
                  'cuga_knowledge_meta_collection_config',
                  'cuga_knowledge_meta_settings'
              )
              AND c.table_name NOT LIKE '%\\_\\_fts' ESCAPE '\\'
              AND c.table_name NOT LIKE '%\\_\\_fts\\_%' ESCAPE '\\'
            ORDER BY c.table_schema, c.table_name
            """
        )
    return [(row["table_schema"], row["table_name"]) for row in rows]


async def _delete_postgres_service_instance_records(
    postgres_url: str,
    service_instance_id: str,
    *,
    dry_run: bool,
) -> dict[str, int]:
    import asyncpg

    pool = await asyncpg.create_pool(postgres_url, min_size=1, max_size=1, command_timeout=60)
    try:
        tables = await _postgres_tables_with_instance_id(pool)
        deleted: dict[str, int] = {}
        async with pool.acquire() as conn:
            async with conn.transaction():
                for schema, table_name in tables:
                    table_ref = f"{_quote_pg_identifier(schema)}.{_quote_pg_identifier(table_name)}"
                    key = table_name if schema == "public" else f"{schema}.{table_name}"
                    if dry_run:
                        count = await conn.fetchval(
                            f"SELECT COUNT(*) FROM {table_ref} WHERE instance_id = $1",
                            service_instance_id,
                        )
                        deleted[key] = int(count or 0)
                        continue
                    result = await conn.execute(
                        f"DELETE FROM {table_ref} WHERE instance_id = $1",
                        service_instance_id,
                    )
                    try:
                        deleted[key] = int(result.split()[-1])
                    except (ValueError, IndexError):
                        deleted[key] = 0
        return deleted
    finally:
        await pool.close()


def _configured_storage_targets() -> list[tuple[str, str]]:
    """Resolve primary, knowledge, and policy databases using their factory settings.

    Local targets are canonicalized to avoid purging a shared file twice. Optional
    local stores that have never been created contain no records to purge.
    """
    from cuga.backend.knowledge.vector_store import KNOWLEDGE_LOCAL_VECTORS_DB

    mode, local_db_path, postgres_url = get_storage_connection_params()
    knowledge = getattr(settings, "knowledge", None) or {}
    targets: list[tuple[str, str]] = []
    if mode == "prod":
        if not postgres_url:
            raise ValueError("storage.postgres_url is required when storage.mode=prod")
        targets.append(("prod", postgres_url.strip()))
        knowledge_url = (knowledge.get("pgvector_connection_string", "") or "").strip()
        targets.append(("prod", knowledge_url or postgres_url.strip()))
    else:
        if not local_db_path:
            raise ValueError("storage.local_db_path is required when storage.mode=local")
        targets.append(("local", str(Path(local_db_path).resolve())))
        persist_dir = knowledge.get("persist_dir", "") or Path.cwd() / ".cuga" / "knowledge"
        knowledge_path = Path(persist_dir) / KNOWLEDGE_LOCAL_VECTORS_DB
        if knowledge_path.exists():
            targets.append(("local", str(knowledge_path.resolve())))

    policy = getattr(settings, "policy", None)
    policy_path = (getattr(policy, "policy_db_path", "") or "").strip()
    if policy_path:
        path = Path(policy_path)
        if not path.is_absolute():
            path = Path(DBS_DIR) / path
        if path.exists():
            targets.append(("local", str(path.resolve())))
    return list(dict.fromkeys(targets))


async def delete_service_instance_records(
    service_instance_id: str,
    *,
    dry_run: bool = False,
) -> ServiceInstanceCleanupResult:
    """Purge the exact instance ID from every configured storage target.

    Counts for matching table names are summed across databases. Each database
    commits independently; a later failure can leave earlier targets purged.
    Knowledge metadata and unscoped FTS sidecars remain excluded.
    """
    if not service_instance_id or not service_instance_id.strip():
        raise ValueError("service_instance_id is required")

    deleted: dict[str, int] = {}
    for mode, connection in _configured_storage_targets():
        cleanup = (
            _delete_postgres_service_instance_records
            if mode == "prod"
            else _delete_sqlite_service_instance_records
        )
        counts = await cleanup(connection, service_instance_id, dry_run=dry_run)
        for table, count in counts.items():
            deleted[table] = deleted.get(table, 0) + count

    return ServiceInstanceCleanupResult(
        service_instance_id=service_instance_id,
        dry_run=dry_run,
        deleted_records=sum(deleted.values()),
        tables=deleted,
    )
