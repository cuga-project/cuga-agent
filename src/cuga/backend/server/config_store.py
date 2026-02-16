"""Persistent store for agent config versions. Uses SQLite; can be switched to Postgres/py-pglite later."""

import json
import os
import sqlite3
from typing import Any

from cuga.config import DBS_DIR


def _db_path() -> str:
    os.makedirs(DBS_DIR, exist_ok=True)
    return os.path.join(DBS_DIR, "manage_config.db")


def _get_conn() -> sqlite3.Connection:
    path = _db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_config (
            version INTEGER PRIMARY KEY AUTOINCREMENT,
            config_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_config_draft (
            id INTEGER PRIMARY KEY,
            config_json TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()
    return conn


def save_config(config: dict[str, Any]) -> int:
    conn = _get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO agent_config (config_json) VALUES (?)",
            (json.dumps(config),),
        )
        conn.commit()
        return cur.lastrowid or 0
    finally:
        conn.close()


def load_config(version: int | None = None) -> tuple[dict[str, Any] | None, int | None]:
    conn = _get_conn()
    try:
        if version is not None:
            row = conn.execute(
                "SELECT config_json, version FROM agent_config WHERE version = ?",
                (version,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT config_json, version FROM agent_config ORDER BY version DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None, None
        return json.loads(row["config_json"]), row["version"]
    finally:
        conn.close()


def list_versions() -> list[dict[str, Any]]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT version, created_at FROM agent_config ORDER BY version DESC LIMIT 100"
        ).fetchall()
        return [{"version": r["version"], "created_at": r["created_at"]} for r in rows]
    finally:
        conn.close()


def get_latest_version() -> tuple[int | None, str | None]:
    """Return (version, created_at) for the latest config, or (None, None)."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT version, created_at FROM agent_config ORDER BY version DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None, None
        return row["version"], row["created_at"]
    finally:
        conn.close()


def save_draft(config: dict[str, Any]) -> None:
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO agent_config_draft (id, config_json, updated_at) VALUES (1, ?, datetime('now'))",
            (json.dumps(config),),
        )
        conn.commit()
    finally:
        conn.close()


def load_draft() -> dict[str, Any] | None:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT config_json FROM agent_config_draft WHERE id = 1").fetchone()
        if not row:
            return None
        return json.loads(row["config_json"])
    finally:
        conn.close()
