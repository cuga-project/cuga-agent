"""Shared fixtures for session continuity system tests (W17.1)."""

# These tests are slow system tests that start a real server process.
# They run in the 'extras' CI job.
# Individual test functions are marked @pytest.mark.slow

import pytest


@pytest.fixture
def continuity_client(tmp_path, monkeypatch):
    """A TestClient backed by a durable SQLite checkpointer in tmp_path.

    Sets CUGA_CHECKPOINT_DB_PATH so the server-under-test uses an isolated
    SQLite file rather than an in-memory or shared store.  Full server-restart
    tests (rows 1, 5) build on top of this fixture and are marked
    @pytest.mark.slow so they are skipped in the fast unit suite.
    """

    monkeypatch.setenv("CUGA_CHECKPOINT_DB_PATH", str(tmp_path / "checkpoints.db"))
    yield  # subclasses that need a TestClient import and build it themselves
