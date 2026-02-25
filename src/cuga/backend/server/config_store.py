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
    """
    Parse agent_id to extract base agent name, removing version suffix if present.

    The system uses a single agent_id (e.g., 'cuga-default') with different versions
    stored in the database. For registry queries, the format is 'agent_id--version'.

    Examples:
        'cuga-default' -> 'cuga-default'
        'cuga-default--draft' -> 'cuga-default'
        'cuga-default--1' -> 'cuga-default'
        'cuga-default--23' -> 'cuga-default'

    Args:
        agent_id: Agent ID that may include version suffix (e.g., 'cuga-default--draft' or 'cuga-default--1')

    Returns:
        Base agent ID without version suffix
    """
    # Split on '--' separator to extract base agent_id
    if '--' in agent_id:
        return agent_id.split('--')[0]
    return agent_id


def _get_store():
    return get_storage().get_relational_store("config")


def _ensure_schema(store) -> None:
    if type(store).__name__ == "ProdRelationalStore":
        store.execute(
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
        store.execute(
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
            rows = store.fetchall("PRAGMA table_info(agent_configs)", ())
            columns = {row[1] for row in rows}
        except Exception:
            columns = set()
        if "version" not in columns:
            store.execute("ALTER TABLE agent_configs ADD COLUMN version TEXT NOT NULL DEFAULT 'draft'")
        if "created_at" not in columns:
            store.execute(
                "ALTER TABLE agent_configs ADD COLUMN created_at TEXT NOT NULL DEFAULT (datetime('now'))"
            )
        if "version" not in columns:
            store.execute(
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
            store.execute(
                """
                INSERT INTO agent_configs_new (agent_id, version, config_json, created_at, updated_at)
                SELECT agent_id, 'draft', config_json, datetime('now'), updated_at
                FROM agent_configs
                """
            )
            store.execute("DROP TABLE agent_configs")
            store.execute("ALTER TABLE agent_configs_new RENAME TO agent_configs")
    store.commit()


def save_config(config: dict[str, Any], agent_id: str = "cuga-default") -> str:
    """Save a new published version of config for an agent. Returns version string."""
    base_agent_id = _parse_agent_id(agent_id)
    store = _get_store()
    try:
        _ensure_schema(store)
        row = store.fetchone(
            """
            SELECT MAX(CAST(version AS INTEGER)) as max_ver
            FROM agent_configs
            WHERE agent_id = ? AND version != 'draft'
            """,
            (base_agent_id,),
        )
        max_ver = row["max_ver"] if row and hasattr(row, "keys") else (row[0] if row else None)
        next_version = (max_ver or 0) + 1
        version_str = str(next_version)
        store.execute(
            """
            INSERT INTO agent_configs (agent_id, version, config_json, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            """,
            (base_agent_id, version_str, json.dumps(config)),
        )
        store.commit()
        return version_str
    finally:
        store.close()


def load_config(
    version: str | None = None, agent_id: str = "cuga-default"
) -> tuple[dict[str, Any] | None, str | None]:
    """Load a specific version or latest published version for an agent."""
    base_agent_id = _parse_agent_id(agent_id)
    store = _get_store()
    try:
        _ensure_schema(store)
        if version is not None and version != "draft":
            row = store.fetchone(
                "SELECT config_json, version FROM agent_configs WHERE agent_id = ? AND version = ?",
                (base_agent_id, version),
            )
        else:
            row = store.fetchone(
                """
                SELECT config_json, version FROM agent_configs
                WHERE agent_id = ? AND version != 'draft'
                ORDER BY CAST(version AS INTEGER) DESC LIMIT 1
                """,
                (base_agent_id,),
            )
        if not row:
            return None, None
        cj = row["config_json"] if hasattr(row, "keys") else row[0]
        ver = row["version"] if hasattr(row, "keys") else row[1]
        return json.loads(cj), ver
    finally:
        store.close()


def list_versions(agent_id: str = "cuga-default") -> list[dict[str, Any]]:
    """List all published versions for an agent (excludes draft)."""
    base_agent_id = _parse_agent_id(agent_id)
    store = _get_store()
    try:
        _ensure_schema(store)
        rows = store.fetchall(
            """
            SELECT version, created_at FROM agent_configs
            WHERE agent_id = ? AND version != 'draft'
            ORDER BY CAST(version AS INTEGER) DESC LIMIT 100
            """,
            (base_agent_id,),
        )
        return [
            {
                "version": r["version"] if hasattr(r, "keys") else r[0],
                "created_at": r["created_at"] if hasattr(r, "keys") else r[1],
            }
            for r in rows
        ]
    finally:
        store.close()


def get_latest_version(agent_id: str = "cuga-default") -> tuple[str | None, str | None]:
    """Return (version, created_at) for the latest published config, or (None, None)."""
    base_agent_id = _parse_agent_id(agent_id)
    store = _get_store()
    try:
        _ensure_schema(store)
        row = store.fetchone(
            """
            SELECT version, created_at FROM agent_configs
            WHERE agent_id = ? AND version != 'draft'
            ORDER BY CAST(version AS INTEGER) DESC LIMIT 1
            """,
            (base_agent_id,),
        )
        if not row:
            return None, None
        ver = row["version"] if hasattr(row, "keys") else row[0]
        ca = row["created_at"] if hasattr(row, "keys") else row[1]
        return ver, ca
    finally:
        store.close()


def save_draft(config: dict[str, Any], agent_id: str = "cuga-default") -> None:
    """Save draft config for an agent."""
    base_agent_id = _parse_agent_id(agent_id)
    store = _get_store()
    try:
        _ensure_schema(store)
        now = datetime.utcnow().isoformat()
        store.execute(
            """
            INSERT INTO agent_configs (agent_id, version, config_json, updated_at)
            VALUES (?, 'draft', ?, ?)
            ON CONFLICT(agent_id, version)
            DO UPDATE SET config_json = excluded.config_json, updated_at = excluded.updated_at
            """,
            (base_agent_id, json.dumps(config), now),
        )
        store.commit()
    finally:
        store.close()


def load_draft(agent_id: str = "cuga-default") -> dict[str, Any] | None:
    """Load draft config for an agent."""
    base_agent_id = _parse_agent_id(agent_id)
    store = _get_store()
    try:
        _ensure_schema(store)
        row = store.fetchone(
            "SELECT config_json FROM agent_configs WHERE agent_id = ? AND version = 'draft'",
            (base_agent_id,),
        )
        if not row:
            return None
        cj = row["config_json"] if hasattr(row, "keys") else row[0]
        return json.loads(cj)
    finally:
        store.close()


# ============================================================================
# Agent-specific Config Management (by agent_id)
# ============================================================================


def get_agent_tools(agent_id: str, version: str = "draft") -> list[dict[str, Any]]:
    """
    Get tools list from agent config. Returns empty list if no config or no tools.

    Args:
        agent_id: The agent ID (may include version suffix like 'cuga-default--draft' or 'cuga-default--1')
        version: Version to load ('draft' or version number). Defaults to 'draft'.
    """
    # Parse agent_id to remove version suffix if present
    base_agent_id = _parse_agent_id(agent_id)

    if version == "draft":
        config = load_draft(base_agent_id)
    else:
        config, _ = load_config(version, base_agent_id)

    if not config:
        return []
    return config.get("tools", [])


def list_agents_with_configs() -> list[dict[str, Any]]:
    """List all unique agents that have configs (any version)."""
    store = _get_store()
    try:
        _ensure_schema(store)
        rows = store.fetchall(
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
                "agent_id": r["agent_id"] if hasattr(r, "keys") else r[0],
                "last_updated": r["last_updated"] if hasattr(r, "keys") else r[1],
            }
            for r in rows
        ]
    finally:
        store.close()


def delete_all_configs(agent_id: str = "cuga-default") -> int:
    """Delete all configs for an agent (draft and all versions). Returns count deleted."""
    base_agent_id = _parse_agent_id(agent_id)
    store = _get_store()
    try:
        _ensure_schema(store)
        store.execute("DELETE FROM agent_configs WHERE agent_id = ?", (base_agent_id,))
        store.commit()
        return getattr(store, "_last_rowcount", 0)
    finally:
        store.close()


def reset_config_db() -> None:
    """Reset config db by deleting the database file. Next access will recreate it."""
    from cuga.config import DBS_DIR

    path = os.path.join(DBS_DIR, "cuga.db")
    if os.path.exists(path):
        os.remove(path)
