"""Knowledge calls on Postgres share one connection pool per database instead of connecting for
every call, and the shared pool survives a collection being dropped or closed."""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock

import pytest

from cuga.backend.knowledge.storage import adapter as adapter_mod
from cuga.backend.knowledge.storage.adapter import StorageBackedKnowledgeVectorStore
from cuga.backend.storage.embedding import prod

pytestmark = pytest.mark.unit


class _Embeddings:
    def embed_query(self, text: str) -> list[float]:
        return [0.1] * 384

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 384 for _ in texts]


def _fake_pool() -> MagicMock:
    connection = MagicMock()
    connection.fetch = AsyncMock(return_value=[])
    connection.fetchval = AsyncMock(return_value=True)  # the table and its index exist
    connection.execute = AsyncMock()
    pool = MagicMock()
    pool.acquire = AsyncMock(return_value=connection)
    pool.release = AsyncMock()
    pool.close = AsyncMock()
    pool.connection = connection
    return pool


@pytest.fixture
def created_pools(monkeypatch):
    created: list[MagicMock] = []

    async def new_pool(postgres_url, **kwargs):
        created.append(_fake_pool())
        created[-1].create_kwargs = kwargs
        return created[-1]

    monkeypatch.setattr(prod, "_new_pool", new_pool)
    # Each test gets fresh shared pools; the Postgres loop itself is shared by the process.
    monkeypatch.setattr(prod, "_shared_pools", type(prod._shared_pools)())
    return created


def _adapter(collection: str = "kb_reuse") -> StorageBackedKnowledgeVectorStore:
    return StorageBackedKnowledgeVectorStore(
        collection, _Embeddings(), local_db_path="", postgres_url="postgresql://u@h/db"
    )


def test_searches_from_many_threads_share_one_pool(created_pools):
    adapter = _adapter()
    threads = [threading.Thread(target=adapter.search, args=("q",)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    adapter.search("again")

    assert len(created_pools) == 1
    created_pools[0].close.assert_not_awaited()


def test_collections_share_one_pool_per_database(created_pools):
    adapters = [_adapter(f"kb_reuse_{i}") for i in range(12)]
    for a in adapters:
        a.search("q")

    assert len(created_pools) == 1  # one bounded pool, not one per collection
    kwargs = created_pools[0].create_kwargs
    assert kwargs["max_size"] == prod.SHARED_POOL_MAX_SIZE
    assert kwargs["min_size"] == 0
    assert kwargs["max_inactive_connection_lifetime"] == prod.SHARED_POOL_IDLE_S
    assert kwargs["server_settings"] == {"application_name": "cuga-knowledge"}


@pytest.mark.asyncio
async def test_a_call_from_inside_an_event_loop_still_reuses_the_pool(created_pools):
    adapter = _adapter()
    adapter.search("from a thread")
    adapter.search("from a running loop")  # the server's request loop calls in like this

    assert len(created_pools) == 1


def test_a_call_from_the_postgres_loop_itself_fails_instead_of_hanging(created_pools):
    adapter = _adapter()

    async def nested():
        return adapter.search("q")

    with pytest.raises(RuntimeError, match="Postgres loop"):
        asyncio.run_coroutine_threadsafe(nested(), adapter_mod._postgres_loop()).result(10)


def _store(name: str = "kb_store", shared: bool = True) -> prod.ProdEmbeddingStore:
    schema = prod.EmbeddingSchemaConfig(
        embedding_dim=3, id_column="id", metadata_columns={}, auxiliary_columns={"chunk_text": "text"}
    )
    return prod.ProdEmbeddingStore("postgresql://u@h/db", name, schema, shared_pool=shared)


@pytest.mark.asyncio
async def test_closing_one_store_leaves_the_shared_pool_open(created_pools):
    a, b = _store("kb_a"), _store("kb_b")
    await a.search([0.0, 0.0, 1.0], 3, {})
    await b.search([0.0, 0.0, 1.0], 3, {})

    await a.close_pool()  # what dropping a collection does first

    created_pools[0].close.assert_not_awaited()
    assert await b.search([0.0, 0.0, 1.0], 3, {}) == []
    assert len(created_pools) == 1


@pytest.mark.asyncio
async def test_a_dropped_table_reads_as_empty_and_the_next_write_recreates_it(created_pools, monkeypatch):
    import asyncpg

    store = _store()
    await store.search([0.0, 0.0, 1.0], 3, {})
    conn = created_pools[0].connection
    missing = asyncpg.exceptions.UndefinedTableError('relation "kb_store" does not exist')

    conn.fetch = AsyncMock(side_effect=missing)
    assert await store.search([0.0, 0.0, 1.0], 3, {}) == []
    assert await store.list({}, 10) == []
    conn.fetchrow = AsyncMock(side_effect=missing)
    assert await store.get("a") is None
    conn.execute = AsyncMock(side_effect=missing)
    await store.delete("a")  # nothing to delete

    ensure = AsyncMock()
    monkeypatch.setattr(store, "_ensure_table", ensure)
    conn.executemany = AsyncMock(side_effect=[missing, None])
    conn.transaction = MagicMock(
        return_value=MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock(return_value=False))
    )
    await store.add_many([("a", [0.0, 0.0, 1.0], {"chunk_text": "x"})])

    ensure.assert_awaited_once()
    assert conn.executemany.await_count == 2

    # A later batch of the same upload fails instead, so the upload rolls back.
    conn.executemany = AsyncMock(side_effect=missing)
    with pytest.raises(asyncpg.exceptions.UndefinedTableError):
        await store.add_many([("b", [0.0, 0.0, 1.0], {"chunk_text": "y"})], recreate_if_missing=False)
    ensure.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_missing_table_still_fails_for_a_store_without_the_shared_pool(monkeypatch):
    """The policy store keeps failing closed when its table is gone."""
    import asyncpg

    pool = _fake_pool()
    monkeypatch.setattr(prod, "_new_pool", AsyncMock(return_value=pool))
    store = _store(shared=False)
    await store.search([0.0, 0.0, 1.0], 3, {})
    missing = asyncpg.exceptions.UndefinedTableError('relation "kb_store" does not exist')
    pool.connection.fetch = AsyncMock(side_effect=missing)

    with pytest.raises(asyncpg.exceptions.UndefinedTableError):
        await store.search([0.0, 0.0, 1.0], 3, {})
    with pytest.raises(asyncpg.exceptions.UndefinedTableError):
        await store.list({}, 10)


class _Slot:
    def __init__(self, connected: bool):
        self.connected = connected

    def is_connected(self) -> bool:
        return self.connected


@pytest.mark.asyncio
async def test_after_a_refused_connection_the_open_connections_are_used_first():
    """asyncpg puts a slot whose connect was refused back on top of its LIFO queue; the next call
    must get an open connection, not retry the refused connect."""
    import asyncpg

    queue = asyncio.LifoQueue()
    for slot in (_Slot(True), _Slot(True), _Slot(False)):  # the refused slot was returned last, on top
        queue.put_nowait(slot)
    pool = MagicMock(
        _queue=queue, acquire=AsyncMock(side_effect=asyncpg.exceptions.TooManyConnectionsError("x"))
    )

    with pytest.raises(asyncpg.exceptions.TooManyConnectionsError):
        async with prod._acquire(pool):
            pass

    assert queue.get_nowait().is_connected()


@pytest.mark.asyncio
async def test_asyncpg_pool_still_keeps_its_slots_in_a_lifo_list():
    """Pins the asyncpg internals _acquire reorders: no database needed with min_size=0."""
    import asyncpg

    pool = await asyncpg.create_pool("postgresql://u@127.0.0.1:1/none", min_size=0, max_size=2)
    try:
        slots = pool._queue._queue
        assert isinstance(pool._queue, asyncio.LifoQueue) and isinstance(slots, list)
        assert len(slots) == 2 and not any(slot.is_connected() for slot in slots)
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_one_busy_collection_leaves_connections_for_the_others(created_pools):
    """A store holds at most SHARED_POOL_MAX_SIZE - 2 of the shared pool's connections, so a slow
    or locked table cannot take every connection from the other collections."""
    busy = _store("kb_busy")
    await busy.search([0.0, 0.0, 1.0], 3, {})  # creates the pool and checks the table
    pool = created_pools[0]
    release = asyncio.Event()

    async def slow_fetch(*args):
        await release.wait()
        return []

    pool.connection.fetch = AsyncMock(side_effect=slow_fetch)
    before = pool.acquire.await_count
    searches = [
        asyncio.ensure_future(busy.search([0.0, 0.0, 1.0], 3, {})) for _ in range(prod.SHARED_POOL_MAX_SIZE)
    ]
    await asyncio.sleep(0.05)  # no I/O: every search has run as far as it can
    held = pool.acquire.await_count - before
    release.set()
    await asyncio.gather(*searches)

    assert held == prod.SHARED_POOL_MAX_SIZE - 2


@pytest.mark.asyncio
async def test_concurrent_first_calls_create_one_shared_pool(created_pools, monkeypatch):
    async def slow_new_pool(postgres_url, **kwargs):
        await asyncio.sleep(0.01)  # connecting yields, so every caller reaches the pool check meanwhile
        created_pools.append(_fake_pool())
        return created_pools[-1]

    monkeypatch.setattr(prod, "_new_pool", slow_new_pool)

    pools = await asyncio.gather(*(prod.shared_pool("postgresql://u@h/db") for _ in range(10)))

    assert len(created_pools) == 1
    assert all(pool is created_pools[0] for pool in pools)


def test_a_search_keeps_its_store_when_the_collection_is_dropped_meanwhile(created_pools):
    adapter = _adapter()
    adapter.search("q")  # creates the store
    embed_query = adapter._embeddings.embed_query

    def dropped_meanwhile(text):
        adapter._store = None  # what drop() leaves, from another thread, while this search embeds
        return embed_query(text)

    adapter._embeddings.embed_query = dropped_meanwhile

    assert adapter.search("q") == []
