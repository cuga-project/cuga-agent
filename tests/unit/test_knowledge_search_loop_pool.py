"""Dense searches on Postgres await the store on the caller's event loop and share that loop's
pool per database, instead of opening and closing a pool on a new loop for every search."""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import AsyncMock

import pytest
from asyncpg.exceptions import UndefinedTableError

from cuga.backend.knowledge.storage.adapter import StorageBackedKnowledgeVectorStore
from cuga.backend.knowledge.vector_store_base import VectorStoreAdapter
from cuga.backend.storage.embedding import prod

pytestmark = pytest.mark.unit

_ROW = ("chunk-1", "chunk text", '{"source": "a.pdf", "filename": "a.pdf", "page": 1}', 0.25)


class _Embeddings:
    def embed_query(self, text: str) -> list[float]:
        return [0.1] * 384

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 384 for _ in texts]


class _Connection:
    def __init__(self) -> None:
        self.fetch = AsyncMock(return_value=[_ROW])
        self.fetchval = AsyncMock(return_value=True)  # the table and its index exist
        self.execute = AsyncMock()


class _Pool:
    def __init__(self, create_kwargs: dict) -> None:
        self.create_kwargs = create_kwargs
        self.connection = _Connection()
        self.close = AsyncMock()

    @contextlib.asynccontextmanager
    async def _acquire(self):
        yield self.connection

    def acquire(self):
        return self._acquire()


@pytest.fixture
def created_pools(monkeypatch) -> list[_Pool]:
    created: list[_Pool] = []

    async def new_pool(postgres_url, **kwargs):
        created.append(_Pool(kwargs))
        return created[-1]

    monkeypatch.setattr(prod, "_new_pool", new_pool)
    monkeypatch.setattr(prod, "_loop_pools", type(prod._loop_pools)())
    monkeypatch.setattr(prod, "_loop_pool_holders", type(prod._loop_pool_holders)())
    return created


def _adapter(collection: str) -> StorageBackedKnowledgeVectorStore:
    return StorageBackedKnowledgeVectorStore(collection, _Embeddings(), postgres_url="postgresql://u@h/db")


def test_searches_on_one_loop_share_one_bounded_pool_across_collections(created_pools):
    adapters = [_adapter(f"kb_{i}") for i in range(3)]

    async def main():
        results = await asyncio.gather(*(a.asearch("question", k=3) for a in adapters for _ in range(10)))
        await prod.close_loop_pools()
        return results

    results = asyncio.run(main())

    assert len(created_pools) == 1
    pool = created_pools[0]
    assert pool.create_kwargs["max_size"] == prod.LOOP_POOL_MAX_SIZE
    assert pool.create_kwargs["application_name"] == prod.LOOP_POOL_APPLICATION_NAME
    assert pool.connection.fetch.await_count == 30
    assert all(len(r) == 1 and r[0][0].page_content == "chunk text" for r in results)
    pool.close.assert_awaited_once()


def test_each_loop_gets_its_own_pool_and_closes_it(created_pools):
    adapter = _adapter("kb_loops")

    async def one_loop():
        await adapter.asearch("question")
        await adapter.asearch("question")
        await prod.close_loop_pools()

    asyncio.run(one_loop())
    asyncio.run(one_loop())

    assert len(created_pools) == 2
    for pool in created_pools:
        pool.close.assert_awaited_once()
    # A search runs no DDL and no table check: only the search itself.
    assert [p.connection.fetch.await_count for p in created_pools] == [2, 2]
    checks_and_ddl = [
        p.connection.fetchval.await_count + p.connection.execute.await_count for p in created_pools
    ]
    assert checks_and_ddl == [0, 0]


def test_a_missing_table_reads_as_empty_and_is_not_created(created_pools):
    adapter = _adapter("kb_dropped")

    async def main():
        await adapter.asearch("question")
        pool = created_pools[0]
        pool.connection.fetch.side_effect = UndefinedTableError("relation does not exist")
        assert await adapter.asearch("question") == []
        pool.connection.fetch.side_effect = None
        assert len(await adapter.asearch("question")) == 1
        await prod.close_loop_pools()
        return pool

    pool = asyncio.run(main())

    # During a reindex in another process, creating the table again would use the old vector size.
    assert pool.connection.fetchval.await_count == 0
    assert pool.connection.execute.await_count == 0


def test_a_search_running_while_the_collection_is_dropped_finds_nothing(created_pools):
    adapter = _adapter("kb_inflight")

    async def main():
        await adapter.asearch("question")
        store = adapter._search_store
        adapter._search_store = None  # what drop() leaves for later searches
        created_pools[0].connection.fetch.side_effect = UndefinedTableError("relation does not exist")
        rows = await store.search([0.1] * 384, 3, {"tenant_id": "", "instance_id": ""})
        await prod.close_loop_pools()
        return rows

    assert asyncio.run(main()) == []
    # No DDL ran: the dropped table is not created again by the search that was running.
    assert created_pools[0].connection.execute.await_count == 0


def test_a_store_without_the_loop_pool_still_fails_on_a_missing_table(created_pools):
    from cuga.backend.knowledge.storage.schema import knowledge_embedding_schema

    store = prod.ProdEmbeddingStore("postgresql://u@h/db", "kb_policy", knowledge_embedding_schema(384))

    async def main():
        pool = _Pool({})
        pool.connection.fetch.side_effect = UndefinedTableError("relation does not exist")
        store._pools[asyncio.get_running_loop()] = pool
        await store.search([0.1] * 384, 3, {})

    with pytest.raises(UndefinedTableError):
        asyncio.run(main())


def test_an_adapter_without_an_async_store_searches_in_a_worker_thread():
    class _SyncOnly(VectorStoreAdapter):
        def add_documents(self, documents, stage_timings=None):
            return {"num_added": 0, "num_skipped": 0}

        def search(self, query, k=10):
            return [(query, float(k))]

        def delete_by_source(self, source_id):
            pass

        def drop(self):
            pass

    assert asyncio.run(_SyncOnly().asearch("question", k=2)) == [("question", 2.0)]


class _Engine:
    """An owner of the loop's pools, as a KnowledgeEngine is."""


def test_the_loop_pool_closes_when_the_last_engine_releases_it(created_pools):
    # One engine per CugaAgent; agents on one loop share its pool. Closing it under another
    # engine would fail that engine's searches.
    adapter = _adapter("kb_holders")
    first, second = _Engine(), _Engine()

    async def main():
        prod.hold_loop_pools(first)
        prod.hold_loop_pools(second)
        await adapter.asearch("question")
        await prod.release_loop_pools(first)
        open_after_first = created_pools[0].close.await_count == 0
        await adapter.asearch("question")
        await prod.release_loop_pools(second)
        return open_after_first

    assert asyncio.run(main()) is True
    assert len(created_pools) == 1  # the second engine kept using the same pool
    created_pools[0].close.assert_awaited_once()


def test_an_engine_dropped_without_aclose_stops_holding_the_loop_pool(created_pools):
    import gc

    adapter = _adapter("kb_dropped_engine")
    kept, dropped = _Engine(), _Engine()

    async def main():
        prod.hold_loop_pools(kept)
        prod.hold_loop_pools(dropped)
        await adapter.asearch("question")

    async def run():
        nonlocal dropped
        await main()
        dropped = None  # an agent dropped without aclose()
        gc.collect()
        await prod.release_loop_pools(kept)

    asyncio.run(run())

    created_pools[0].close.assert_awaited_once()
