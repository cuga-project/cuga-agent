"""Shared unit-test fixtures.

Unit tests must never open the real ``src/cuga/dbs/cuga.db``. A running
``cuga start`` app holds that file in SQLite WAL mode, and a second process
opening the same DB and issuing ``PRAGMA journal_mode=WAL`` fails with
``sqlite3.OperationalError: disk I/O error`` — which surfaces as flaky failures
in the real-engine tests (config store / conversation history / manage routes)
whenever the dev app is up. Integration tests already isolate via ``tmp_path``;
this does the same, automatically, for every unit test.
"""

import pytest


@pytest.fixture(autouse=True)
def _isolate_local_storage_db(tmp_path, monkeypatch):
    """Point the local SQLite storage at a per-test temp DB.

    Redirects ``storage.facade._local_db_path`` (the single choke point every
    relational/embedding/policy store resolves its path through) and drops any
    store cached against the real path, so the next access reopens against the
    isolated temp DB. Restores + re-invalidates on teardown so no temp-path
    store leaks into the next test.
    """
    import cuga.backend.storage.facade as facade

    db_path = str(tmp_path / "cuga.db")
    monkeypatch.setattr(facade, "_local_db_path", lambda: db_path)
    try:
        facade.get_storage().invalidate_relational_stores()
    except Exception:
        pass
    yield
    try:
        facade.get_storage().invalidate_relational_stores()
    except Exception:
        pass
