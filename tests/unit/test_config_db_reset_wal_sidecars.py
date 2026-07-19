"""reset_config_db() must clear the SQLite WAL sidecars, not just the main file.

SQLite in WAL mode is a three-file unit: cuga.db, cuga.db-wal, cuga.db-shm.
Removing only the main DB leaves an orphaned -wal/-shm pair that still describes
pages the recreated file no longer has. The next connection then reads past EOF
and raises SQLITE_IOERR_SHORT_READ, which surfaces as the opaque
"disk I/O error" that broke `cuga start demo_knowledge`.
"""

import sqlite3

import pytest


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


def test_reset_config_db_removes_wal_sidecars(dbs_dir, monkeypatch):
    from cuga.backend.server import config_store

    db = dbs_dir / "cuga.db"
    _make_wal_db(str(db))

    # Simulate the real-world state: a hard kill left the sidecars orphaned.
    (dbs_dir / "cuga.db-wal").touch(exist_ok=True)
    (dbs_dir / "cuga.db-shm").touch(exist_ok=True)

    monkeypatch.setattr(
        config_store,
        "get_storage",
        lambda: type("S", (), {"invalidate_relational_stores": lambda self: None})(),
    )
    config_store.reset_config_db()

    assert not db.exists(), "main DB should be removed"
    assert not (dbs_dir / "cuga.db-wal").exists(), "orphaned -wal must be removed"
    assert not (dbs_dir / "cuga.db-shm").exists(), "orphaned -shm must be removed"
