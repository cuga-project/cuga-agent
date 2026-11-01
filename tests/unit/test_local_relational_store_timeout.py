"""The shared SQLite connection waits as long as the batch connection when the file is locked."""

from __future__ import annotations

import sqlite3

import pytest

from cuga.backend.storage.relational.local import LocalRelationalStore

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_main_connection_waits_30_seconds_for_a_locked_database(tmp_path):
    store = LocalRelationalStore(str(tmp_path / "store.db"))
    try:
        row = await store.fetchone("PRAGMA busy_timeout")
        assert row == {"timeout": 30000}
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_writes_still_work_and_wal_is_on(tmp_path):
    path = tmp_path / "store.db"
    store = LocalRelationalStore(str(path))
    try:
        await store.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        await store.execute("INSERT INTO t (v) VALUES (?)", ("a",))
        await store.commit()
        assert await store.fetchall("SELECT v FROM t") == [{"v": "a"}]
    finally:
        await store.close()

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
