"""Unit tests for StorageFacade relational-store cache management."""

from __future__ import annotations

import pytest

from cuga.backend.storage.facade import StorageFacade


def test_invalidate_relational_stores_closes_and_clears():
    facade = StorageFacade()
    closed = {"value": False}

    class FakeStore:
        def close_sync(self):
            closed["value"] = True

    facade._relational_stores["config"] = FakeStore()
    facade.invalidate_relational_stores()

    assert closed["value"] is True
    assert facade._relational_stores == {}


def test_invalidate_relational_stores_tolerates_stores_without_close_sync():
    facade = StorageFacade()

    class PoolStore:  # e.g. prod asyncpg store, no sync close
        pass

    facade._relational_stores["config"] = PoolStore()
    facade.invalidate_relational_stores()  # must not raise

    assert facade._relational_stores == {}


@pytest.mark.asyncio
async def test_local_store_close_sync_resets_connection(tmp_path):
    from cuga.backend.storage.relational.local import LocalRelationalStore

    store = LocalRelationalStore(str(tmp_path / "t.db"))
    await store.execute("CREATE TABLE t (id INTEGER)")
    assert store._conn is not None

    store.close_sync()
    assert store._conn is None
