from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from docs.examples.suggesthub.app.seed import reset_seeded_database


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "suggesthub-test.db"
    monkeypatch.setenv("SUGGESTHUB_DB_PATH", str(path))
    reset_seeded_database(str(path))
    return str(path)


@pytest.fixture
def client(db_path):
    from docs.examples.suggesthub.app.main import app

    with TestClient(app) as test_client:
        yield test_client
