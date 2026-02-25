"""
Persistent store for agent config versions.

This module manages agent configurations with version control:
- Single agent_id (e.g., 'cuga-default') with multiple versions
- Version column stores: 'draft', '1', '2', etc.
- Registry queries use format: 'agent_id--version' (e.g., 'cuga-default--draft')
- Automatic database migration for backward compatibility

Database Schema:
    agent_configs table (in cuga.db local / Postgres prod):
        - agent_id (TEXT), version (TEXT), config_json (TEXT), created_at, updated_at
"""

import json
import os
from datetime import datetime
from typing import Any

from cuga.backend.storage import get_storage


def _parse_agent_id(agent_id: str) -> str:
    if '--' in agent_id:
        return agent_id.split('--')[0]
    return agent_id


def _get_store():
    return get_storage().get_relational_store("config")


async def _ensure_schema(store) -> None:
    if type(store).__name__ == "ProdRelationalStore":
        await store.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_configs (
                agent_id TEXT NOT NULL,
                version TEXT NOT NULL DEFAULT 'draft',
                config_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP::text),
                updated_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP::text),
                PRIMARY KEY (agent_id, version)
            )
            """
        )
    else:
        await store.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_configs (
                agent_id TEXT NOT NULL,
                version TEXT NOT NULL DEFAULT 'draft',
                config_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (agent_id, version)
            )
            """
        )
    if type(store).__name__ == "LocalRelationalStore":
        try:
            rows = await store.fetchall("PRAGMA table_info(agent_configs)", ())
            columns = {r["name"] for r in rows}
        except Exception:
            columns = set()
        if "version" not in columns:
            await store.execute("ALTER TABLE agent_configs ADD COLUMN version TEXT NOT NULL DEFAULT 'draft'")
        if "created_at" not in columns:
            await store.execute(
                "ALTER TABLE agent_configs ADD COLUMN created_at TEXT NOT NULL DEFAULT (datetime('now'))"
            )
        if "version" not in columns:
            await store.execute(
                """
                CREATE TABLE agent_configs_new (
                    agent_id TEXT NOT NULL,
                    version TEXT NOT NULL DEFAULT 'draft',
                    config_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY (agent_id, version)
                )
                """
            )
            await store.execute(
                """
                INSERT INTO agent_configs_new (agent_id, version, config_json, created_at, updated_at)
                SELECT agent_id, 'draft', config_json, datetime('now'), updated_at
                FROM agent_configs
                """
            )
            await store.execute("DROP TABLE agent_configs")
            await store.execute("ALTER TABLE agent_configs_new RENAME TO agent_configs")
    await store.commit()


async def save_config(config: dict[str, Any], agent_id: str = "cuga-default") -> str:
    base_agent_id = _parse_agent_id(agent_id)
    store = _get_store()
    try:
        await _ensure_schema(store)
        row = await store.fetchone(
            """
            SELECT MAX(CAST(version AS INTEGER)) as max_ver
            FROM agent_configs
            WHERE agent_id = ? AND version != 'draft'
            """,
            (base_agent_id,),
        )
        max_ver = row["max_ver"] if row and "max_ver" in row else (row[0] if row else None)
        next_version = (max_ver or 0) + 1
        version_str = str(next_version)
        await store.execute(
            """
            INSERT INTO agent_configs (agent_id, version, config_json, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (base_agent_id, version_str, json.dumps(config)),
        )
        await store.commit()
        return version_str
    finally:
        await store.close()


async def load_config(
    version: str | None = None, agent_id: str = "cuga-default"
) -> tuple[dict[str, Any] | None, str | None]:
    base_agent_id = _parse_agent_id(agent_id)
    store = _get_store()
    try:
        await _ensure_schema(store)
        if version is not None and version != "draft":
            row = await store.fetchone(
                "SELECT config_json, version FROM agent_configs WHERE agent_id = ? AND version = ?",
                (base_agent_id, version),
            )
        else:
            row = await store.fetchone(
                """
                SELECT config_json, version FROM agent_configs
                WHERE agent_id = ? AND version != 'draft'
                ORDER BY CAST(version AS INTEGER) DESC LIMIT 1
                """,
                (base_agent_id,),
            )
        if not row:
            return None, None
        cj = row["config_json"] if isinstance(row, dict) else row[0]
        ver = row["version"] if isinstance(row, dict) else row[1]
        return json.loads(cj), ver
    finally:
        await store.close()


async def list_versions(agent_id: str = "cuga-default") -> list[dict[str, Any]]:
    base_agent_id = _parse_agent_id(agent_id)
    store = _get_store()
    try:
        await _ensure_schema(store)
        rows = await store.fetchall(
            """
            SELECT version, created_at FROM agent_configs
            WHERE agent_id = ? AND version != 'draft'
            ORDER BY CAST(version AS INTEGER) DESC LIMIT 100
            """,
            (base_agent_id,),
        )
        return [
            {
                "version": r["version"] if isinstance(r, dict) else r[0],
                "created_at": r["created_at"] if isinstance(r, dict) else r[1],
            }
            for r in rows
        ]
    finally:
        await store.close()


async def get_latest_version(agent_id: str = "cuga-default") -> tuple[str | None, str | None]:
    base_agent_id = _parse_agent_id(agent_id)
    store = _get_store()
    try:
        await _ensure_schema(store)
        row = await store.fetchone(
            """
            SELECT version, created_at FROM agent_configs
            WHERE agent_id = ? AND version != 'draft'
            ORDER BY CAST(version AS INTEGER) DESC LIMIT 1
            """,
            (base_agent_id,),
        )
        if not row:
            return None, None
        ver = row["version"] if isinstance(row, dict) else row[0]
        ca = row["created_at"] if isinstance(row, dict) else row[1]
        return ver, ca
    finally:
        await store.close()


async def save_draft(config: dict[str, Any], agent_id: str = "cuga-default") -> None:
    base_agent_id = _parse_agent_id(agent_id)
    store = _get_store()
    try:
        await _ensure_schema(store)
        now = datetime.utcnow().isoformat()
        await store.execute(
            """
            INSERT INTO agent_configs (agent_id, version, config_json, updated_at)
            VALUES (?, 'draft', ?, ?)
            ON CONFLICT(agent_id, version)
            DO UPDATE SET config_json = excluded.config_json, updated_at = excluded.updated_at
            """,
            (base_agent_id, json.dumps(config), now),
        )
        await store.commit()
    finally:
        await store.close()


async def load_draft(agent_id: str = "cuga-default") -> dict[str, Any] | None:
    base_agent_id = _parse_agent_id(agent_id)
    store = _get_store()
    try:
        await _ensure_schema(store)
        row = await store.fetchone(
            "SELECT config_json FROM agent_configs WHERE agent_id = ? AND version = 'draft'",
            (base_agent_id,),
        )
        if not row:
            return None
        cj = row["config_json"] if isinstance(row, dict) else row[0]
        return json.loads(cj)
    finally:
        await store.close()


async def get_agent_tools(agent_id: str, version: str = "draft") -> list[dict[str, Any]]:
    base_agent_id = _parse_agent_id(agent_id)
    if version == "draft":
        config = await load_draft(base_agent_id)
    else:
        config, _ = await load_config(version, base_agent_id)
    if not config:
        return []
    return config.get("tools", [])


async def list_agents_with_configs() -> list[dict[str, Any]]:
    store = _get_store()
    try:
        await _ensure_schema(store)
        rows = await store.fetchall(
            """
            SELECT DISTINCT agent_id, MAX(updated_at) as last_updated
            FROM agent_configs
            GROUP BY agent_id
            ORDER BY agent_id
            """,
            (),
        )
        return [
            {
                "agent_id": r["agent_id"] if isinstance(r, dict) else r[0],
                "last_updated": r["last_updated"] if isinstance(r, dict) else r[1],
            }
            for r in rows
        ]
    finally:
        await store.close()


async def delete_all_configs(agent_id: str = "cuga-default") -> int:
    base_agent_id = _parse_agent_id(agent_id)
    store = _get_store()
    try:
        await _ensure_schema(store)
        await store.execute("DELETE FROM agent_configs WHERE agent_id = ?", (base_agent_id,))
        await store.commit()
        return getattr(store, "_last_rowcount", 0)
    finally:
        await store.close()


def reset_config_db() -> None:
    from cuga.config import DBS_DIR

    path = os.path.join(DBS_DIR, "cuga.db")
    if os.path.exists(path):
        os.remove(path)
