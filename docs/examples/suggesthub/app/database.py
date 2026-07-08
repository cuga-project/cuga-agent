from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "suggesthub.db"


DDL = """
CREATE TABLE IF NOT EXISTS suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    category TEXT NOT NULL,
    location TEXT NOT NULL,
    impact TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'New',
    author_name TEXT NOT NULL DEFAULT 'Anonymous IBMer',
    anonymous INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS votes (
    suggestion_id INTEGER NOT NULL REFERENCES suggestions(id) ON DELETE CASCADE,
    visitor_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (suggestion_id, visitor_id)
);

CREATE TABLE IF NOT EXISTS status_updates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    suggestion_id INTEGER NOT NULL REFERENCES suggestions(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    response TEXT NOT NULL,
    manager_name TEXT NOT NULL DEFAULT 'Facilities Manager',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS bob_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    category TEXT NOT NULL,
    location TEXT NOT NULL,
    impact TEXT NOT NULL,
    similar_suggestion_ids TEXT NOT NULL DEFAULT '',
    approved INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
"""


def get_db_path() -> str:
    return os.path.abspath(os.getenv("SUGGESTHUB_DB_PATH", str(DEFAULT_DB_PATH)))


def connect(path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or get_db_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def db_session(path: str | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(path)
    try:
        yield conn
    finally:
        conn.close()


def init_db(reset: bool = False, path: str | None = None) -> None:
    db_path = path or get_db_path()
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    if reset and os.path.exists(db_path):
        os.remove(db_path)
    with db_session(db_path) as conn:
        conn.executescript(DDL)
        conn.commit()


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict]:
    return [dict(row) for row in rows]
