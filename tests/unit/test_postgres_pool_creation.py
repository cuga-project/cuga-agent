"""Concurrent first requests share one Postgres pool per store instead of each creating one."""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock

import pytest

from cuga.backend.storage.embedding.base import EmbeddingSchemaConfig
from cuga.backend.storage.embedding.prod import ProdEmbeddingStore
from cuga.backend.storage.relational import prod as relational_prod
from cuga.backend.storage.relational.prod import ProdRelationalStore

pytestmark = pytest.mark.unit


def _fake_pool() -> MagicMock:
    connection = MagicMock()
    connection.execute = AsyncMock()
    acquire = MagicMock()
    acquire.__aenter__ = AsyncMock(return_value=connection)
    acquire.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire)
    pool.close = AsyncMock()
    return pool


@pytest.mark.asyncio
async def test_relational_store_creates_one_pool_for_concurrent_first_calls(monkeypatch):
    created = []

    async def create_pool(*args, **kwargs):
        await asyncio.sleep(0.01)  # yield so every caller reaches the pool check
        created.append(_fake_pool())
        return created[-1]

    monkeypatch.setattr(relational_prod, "asyncpg", MagicMock(create_pool=create_pool))
    store = ProdRelationalStore("postgresql://x/y", "db")

    pools = await asyncio.gather(*(store._get_pool() for _ in range(50)))

    assert len(created) == 1
    assert all(pool is created[0] for pool in pools)


@pytest.mark.asyncio
async def test_embedding_store_creates_one_pool_and_prepares_it_before_sharing(monkeypatch):
    created = []
    prepared = []

    async def create_pool(*args, **kwargs):
        await asyncio.sleep(0.01)
        created.append(_fake_pool())
        return created[-1]

    async def ensure_table(pool=None):
        await asyncio.sleep(0.01)
        prepared.append(pool)

    monkeypatch.setattr("asyncpg.create_pool", create_pool)
    monkeypatch.setattr("pgvector.asyncpg.register_vector", AsyncMock())
    schema = EmbeddingSchemaConfig(embedding_dim=4, id_column="id", metadata_columns={}, auxiliary_columns={})
    store = ProdEmbeddingStore("postgresql://x/y", "docs", schema)
    monkeypatch.setattr(store, "_ensure_table", ensure_table)

    async def get_pool_and_record_prepared():
        pool = await store._get_pool()
        return pool, list(prepared)

    results = await asyncio.gather(*(get_pool_and_record_prepared() for _ in range(20)))

    assert len(created) == 1
    for pool, prepared_when_returned in results:
        assert pool is created[0]
        # No caller got the pool before the table was prepared.
        assert prepared_when_returned == [created[0]]


@pytest.mark.asyncio
async def test_embedding_store_closes_the_pool_when_preparation_fails(monkeypatch):
    pool = _fake_pool()

    async def create_pool(*args, **kwargs):
        return pool

    monkeypatch.setattr("asyncpg.create_pool", create_pool)
    monkeypatch.setattr("pgvector.asyncpg.register_vector", AsyncMock())
    schema = EmbeddingSchemaConfig(embedding_dim=4, id_column="id", metadata_columns={}, auxiliary_columns={})
    store = ProdEmbeddingStore("postgresql://x/y", "docs", schema)
    monkeypatch.setattr(store, "_ensure_table", AsyncMock(side_effect=RuntimeError("boom")))

    with pytest.raises(RuntimeError):
        await store._get_pool()

    pool.close.assert_awaited_once()
    assert store._pool is None


def _run_in_thread_loops(store_factory):
    """Call ``_get_pool`` from two threads, each with its own event loop, at the same time."""
    results: list = []
    errors: list = []
    started = threading.Barrier(2)

    def worker(store):
        async def go():
            started.wait(timeout=5)
            return await store._get_pool()

        try:
            results.append(asyncio.run(go()))
        except BaseException as exc:  # noqa: BLE001 - reported to the test below
            errors.append(exc)

    store = store_factory()
    threads = [threading.Thread(target=worker, args=(store,), daemon=True) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    return threads, results, errors


def test_relational_store_pool_creation_does_not_hang_across_event_loops(monkeypatch):
    async def create_pool(*args, **kwargs):
        await asyncio.sleep(0.05)
        return _fake_pool()

    monkeypatch.setattr(relational_prod, "asyncpg", MagicMock(create_pool=create_pool))

    threads, results, errors = _run_in_thread_loops(lambda: ProdRelationalStore("postgresql://x/y", "db"))

    assert not any(t.is_alive() for t in threads), "a caller hung waiting for a lock on another loop"
    assert errors == []
    assert len(results) == 2


def test_embedding_store_pool_creation_does_not_hang_across_event_loops(monkeypatch):
    async def create_pool(*args, **kwargs):
        await asyncio.sleep(0.05)
        return _fake_pool()

    monkeypatch.setattr("asyncpg.create_pool", create_pool)
    monkeypatch.setattr("pgvector.asyncpg.register_vector", AsyncMock())
    schema = EmbeddingSchemaConfig(embedding_dim=4, id_column="id", metadata_columns={}, auxiliary_columns={})

    def factory():
        store = ProdEmbeddingStore("postgresql://x/y", "docs", schema)
        store._ensure_table = AsyncMock()
        return store

    threads, results, errors = _run_in_thread_loops(factory)

    assert not any(t.is_alive() for t in threads), "a caller hung waiting for a lock on another loop"
    assert errors == []
    assert len(results) == 2
