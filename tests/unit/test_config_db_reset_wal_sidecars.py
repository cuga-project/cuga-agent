"""reset_config_db() resets config in place and keeps conversations.

Config tables are dropped; conversation tables survive because their
checkpoints live in a separate file (#774). When the file cannot be cleaned in
place it is removed as a three-file WAL unit: cuga.db, cuga.db-wal and
cuga.db-shm. Removing only the main DB leaves an orphaned -wal/-shm pair that
still describes pages the recreated file no longer has. The next connection
then reads past EOF and raises SQLITE_IOERR_SHORT_READ, which surfaces as the
opaque "disk I/O error" that broke `cuga start demo_knowledge`.
"""

import sqlite3

import pytest

pytestmark = pytest.mark.unit


def _make_wal_db(path: str) -> None:
    """Create a real WAL-mode DB, leaving -wal/-shm on disk (no checkpoint)."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, blob TEXT)")
    conn.executemany("INSERT INTO t (blob) VALUES (?)", [(f"row-{i}" * 200,) for i in range(200)])
    conn.commit()
    conn.close()


@pytest.fixture()
def dbs_dir(tmp_path, monkeypatch):
    d = tmp_path / "dbs"
    d.mkdir()
    monkeypatch.setenv("CUGA_DBS_DIR", str(d))
    import cuga.config as cuga_config

    monkeypatch.setattr(cuga_config, "DBS_DIR", str(d), raising=False)
    return d


def _reset(monkeypatch):
    from cuga.backend.server import config_store

    monkeypatch.setattr(
        config_store,
        "get_storage",
        lambda: type("S", (), {"invalidate_relational_stores": lambda self: None})(),
    )
    config_store.reset_config_db()


def _tables(path) -> set[str]:
    conn = sqlite3.connect(str(path))
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        conn.close()


def test_reset_keeps_conversations_and_drops_config(dbs_dir, monkeypatch):
    import sqlite_vec

    db = dbs_dir / "cuga.db"
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.execute("CREATE TABLE agent_configs (agent_id TEXT)")
    conn.execute("CREATE TABLE secrets (id TEXT)")
    conn.execute("CREATE VIRTUAL TABLE cuga_policies USING vec0(embedding float[4])")
    conn.execute("CREATE TABLE conversation_threads (thread_id TEXT)")
    conn.execute("CREATE TABLE conversation_history (thread_id TEXT)")
    conn.execute("INSERT INTO agent_configs VALUES ('cuga-default')")
    conn.execute("INSERT INTO conversation_threads VALUES ('t1')")
    conn.execute("INSERT INTO conversation_history VALUES ('t1')")
    conn.commit()
    conn.close()

    _reset(monkeypatch)

    assert db.exists()
    remaining = _tables(db)
    assert {"conversation_threads", "conversation_history"} <= remaining
    assert not any(name.startswith(("agent_configs", "secrets", "cuga_policies")) for name in remaining)
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT thread_id FROM conversation_threads").fetchall() == [("t1",)]
    assert conn.execute("SELECT thread_id FROM conversation_history").fetchall() == [("t1",)]
    conn.close()


def test_reset_with_stranded_wal_sidecars_leaves_usable_db(dbs_dir, monkeypatch):
    db = dbs_dir / "cuga.db"
    _make_wal_db(str(db))

    # Simulate the real-world state: a hard kill left the sidecars behind.
    (dbs_dir / "cuga.db-wal").touch(exist_ok=True)
    (dbs_dir / "cuga.db-shm").touch(exist_ok=True)

    _reset(monkeypatch)

    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE agent_configs (agent_id TEXT)")
    conn.execute("INSERT INTO agent_configs VALUES ('fresh')")
    conn.commit()
    assert "t" not in _tables(db)
    conn.close()


def test_unreadable_db_is_removed_with_sidecars(dbs_dir, monkeypatch):
    db = dbs_dir / "cuga.db"
    db.write_bytes(b"not a sqlite database" * 100)
    (dbs_dir / "cuga.db-wal").touch()
    (dbs_dir / "cuga.db-shm").touch()

    _reset(monkeypatch)

    assert not db.exists(), "main DB should be removed"
    assert not (dbs_dir / "cuga.db-wal").exists(), "orphaned -wal must be removed"
    assert not (dbs_dir / "cuga.db-shm").exists(), "orphaned -shm must be removed"


def test_orphaned_sidecars_without_db_are_removed(dbs_dir, monkeypatch):
    (dbs_dir / "cuga.db-wal").touch()
    (dbs_dir / "cuga.db-shm").touch()

    _reset(monkeypatch)

    assert not (dbs_dir / "cuga.db-wal").exists()
    assert not (dbs_dir / "cuga.db-shm").exists()
