"""
Persistent store for agent config versions.

This module manages agent configurations with version control:
- Single agent_id (e.g., 'cuga-default') with multiple versions
- Version column stores: 'draft', '1', '2', etc.
- Registry queries use format: 'agent_id--version' (e.g., 'cuga-default--draft')
- Automatic database migration for backward compatibility

Database Schema:
    agent_configs table:
        - agent_id (TEXT, PRIMARY KEY part 1): Base agent identifier
        - version (TEXT, PRIMARY KEY part 2): 'draft' or version number
        - config_json (TEXT): JSON configuration data
        - created_at (TEXT): Creation timestamp
        - updated_at (TEXT): Last update timestamp

Uses SQLite; can be switched to Postgres/py-pglite later.
"""

import json
import os
import sqlite3
from typing import Any

from cuga.config import DBS_DIR


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


def _db_path() -> str:
    os.makedirs(DBS_DIR, exist_ok=True)
    return os.path.join(DBS_DIR, "manage_config.db")


def _get_conn() -> sqlite3.Connection:
    path = _db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

    # Single unified table for all agent configurations
    conn.execute(
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

    # Migration: Add missing columns if they don't exist
    # Check if version column exists
    cursor = conn.execute("PRAGMA table_info(agent_configs)")
    columns = {row[1] for row in cursor.fetchall()}

    if "version" not in columns:
        # Old schema detected - need to migrate
        # 1. Add version column with default 'draft'
        conn.execute("ALTER TABLE agent_configs ADD COLUMN version TEXT NOT NULL DEFAULT 'draft'")

    if "created_at" not in columns:
        # 2. Add created_at column
        conn.execute(
            "ALTER TABLE agent_configs ADD COLUMN created_at TEXT NOT NULL DEFAULT (datetime('now'))"
        )

    # If we added version column, we need to recreate the table with proper primary key
    if "version" not in columns:
        # Create new table with correct schema
        conn.execute(
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
        # Copy data from old table (all records become 'draft' version)
        conn.execute(
            """
            INSERT INTO agent_configs_new (agent_id, version, config_json, created_at, updated_at)
            SELECT agent_id, 'draft', config_json, datetime('now'), updated_at
            FROM agent_configs
            """
        )
        # Drop old table and rename new one
        conn.execute("DROP TABLE agent_configs")
        conn.execute("ALTER TABLE agent_configs_new RENAME TO agent_configs")

    conn.commit()
    return conn


def save_config(config: dict[str, Any], agent_id: str = "cuga-default") -> str:
    """Save a new published version of config for an agent. Returns version string."""
    # Parse agent_id to remove version suffix if present
    base_agent_id = _parse_agent_id(agent_id)

    conn = _get_conn()
    try:
        # Get next version number for this agent
        row = conn.execute(
            """
            SELECT MAX(CAST(version AS INTEGER)) as max_ver
            FROM agent_configs
            WHERE agent_id = ? AND version != 'draft'
            """,
            (base_agent_id,),
        ).fetchone()
        next_version = (row["max_ver"] or 0) + 1 if row else 1
        version_str = str(next_version)

        conn.execute(
            """
            INSERT INTO agent_configs (agent_id, version, config_json, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            """,
            (base_agent_id, version_str, json.dumps(config)),
        )
        conn.commit()
        return version_str
    finally:
        conn.close()


def load_config(
    version: str | None = None, agent_id: str = "cuga-default"
) -> tuple[dict[str, Any] | None, str | None]:
    """Load a specific version or latest published version for an agent."""
    # Parse agent_id to remove version suffix if present
    base_agent_id = _parse_agent_id(agent_id)

    conn = _get_conn()
    try:
        if version is not None and version != "draft":
            row = conn.execute(
                "SELECT config_json, version FROM agent_configs WHERE agent_id = ? AND version = ?",
                (base_agent_id, version),
            ).fetchone()
        else:
            # Get latest published version (not draft)
            row = conn.execute(
                """
                SELECT config_json, version FROM agent_configs
                WHERE agent_id = ? AND version != 'draft'
                ORDER BY CAST(version AS INTEGER) DESC LIMIT 1
                """,
                (base_agent_id,),
            ).fetchone()
        if not row:
            return None, None
        return json.loads(row["config_json"]), row["version"]
    finally:
        conn.close()


def list_versions(agent_id: str = "cuga-default") -> list[dict[str, Any]]:
    """List all published versions for an agent (excludes draft)."""
    # Parse agent_id to remove version suffix if present
    base_agent_id = _parse_agent_id(agent_id)

    conn = _get_conn()
    try:
        rows = conn.execute(
            """
            SELECT version, created_at FROM agent_configs
            WHERE agent_id = ? AND version != 'draft'
            ORDER BY CAST(version AS INTEGER) DESC LIMIT 100
            """,
            (base_agent_id,),
        ).fetchall()
        return [{"version": r["version"], "created_at": r["created_at"]} for r in rows]
    finally:
        conn.close()


def get_latest_version(agent_id: str = "cuga-default") -> tuple[str | None, str | None]:
    """Return (version, created_at) for the latest published config, or (None, None)."""
    # Parse agent_id to remove version suffix if present
    base_agent_id = _parse_agent_id(agent_id)

    conn = _get_conn()
    try:
        row = conn.execute(
            """
            SELECT version, created_at FROM agent_configs
            WHERE agent_id = ? AND version != 'draft'
            ORDER BY CAST(version AS INTEGER) DESC LIMIT 1
            """,
            (base_agent_id,),
        ).fetchone()
        if not row:
            return None, None
        return row["version"], row["created_at"]
    finally:
        conn.close()


def save_draft(config: dict[str, Any], agent_id: str = "cuga-default") -> None:
    """Save draft config for an agent."""
    # Parse agent_id to remove version suffix if present
    base_agent_id = _parse_agent_id(agent_id)

    conn = _get_conn()
    try:
        conn.execute(
            """
            INSERT INTO agent_configs (agent_id, version, config_json, updated_at)
            VALUES (?, 'draft', ?, datetime('now'))
            ON CONFLICT(agent_id, version)
            DO UPDATE SET config_json = excluded.config_json, updated_at = datetime('now')
            """,
            (base_agent_id, json.dumps(config)),
        )
        conn.commit()
    finally:
        conn.close()


def load_draft(agent_id: str = "cuga-default") -> dict[str, Any] | None:
    """Load draft config for an agent."""
    # Parse agent_id to remove version suffix if present
    base_agent_id = _parse_agent_id(agent_id)

    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT config_json FROM agent_configs WHERE agent_id = ? AND version = 'draft'",
            (base_agent_id,),
        ).fetchone()
        if not row:
            return None
        return json.loads(row["config_json"])
    finally:
        conn.close()


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
    conn = _get_conn()
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT agent_id, MAX(updated_at) as last_updated
            FROM agent_configs
            GROUP BY agent_id
            ORDER BY agent_id
            """
        ).fetchall()
        return [
            {
                "agent_id": row["agent_id"],
                "last_updated": row["last_updated"],
            }
            for row in rows
        ]
    finally:
        conn.close()
