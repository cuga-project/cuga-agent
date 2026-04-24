import os
import sqlite3
from typing import Generator


def get_crm_db_path() -> str:
    env_path = os.getenv("DYNACONF_CRM_DB_PATH")
    if env_path:
        return os.path.abspath(env_path)
    return os.path.abspath(os.path.join(os.getcwd(), "crm_tmp", "crm_db_default"))


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(get_crm_db_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


_DDL = """
CREATE TABLE IF NOT EXISTS accounts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    industry        TEXT,
    website         TEXT,
    phone           TEXT,
    address         TEXT,
    city            TEXT,
    state           TEXT,
    country         TEXT,
    region          TEXT,
    annual_revenue  REAL,
    employee_count  INTEGER,
    created_at      TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS leads (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name  TEXT NOT NULL,
    last_name   TEXT NOT NULL,
    email       TEXT NOT NULL,
    phone       TEXT,
    company     TEXT,
    job_title   TEXT,
    industry    TEXT,
    source      TEXT,
    status      TEXT DEFAULT 'new',
    score       INTEGER DEFAULT 0,
    notes       TEXT,
    created_at  TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS contacts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name  TEXT NOT NULL,
    last_name   TEXT NOT NULL,
    email       TEXT NOT NULL,
    phone       TEXT,
    job_title   TEXT,
    department  TEXT,
    is_primary  INTEGER DEFAULT 0,
    account_id  INTEGER NOT NULL REFERENCES accounts(id),
    created_at  TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS opportunities (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    description TEXT,
    value       REAL NOT NULL,
    currency    TEXT DEFAULT 'USD',
    stage       TEXT DEFAULT 'prospecting',
    probability REAL DEFAULT 0.0,
    close_date  TEXT,
    account_id  INTEGER NOT NULL REFERENCES accounts(id),
    created_at  TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
    updated_at  TEXT
);
"""


def init_db() -> None:
    db_path = get_crm_db_path()
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    if os.path.exists(db_path):
        os.remove(db_path)

    conn = _connect()
    try:
        conn.executescript(_DDL)
        conn.commit()
        from .crud import account_crud

        if account_crud.count(conn) == 0:
            from .seed_data import seed_database

            seed_database(conn)
    finally:
        conn.close()
