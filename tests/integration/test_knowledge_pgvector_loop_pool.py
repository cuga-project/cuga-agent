"""Dense searches awaited on one event loop share that loop's pool per database: a bounded set of
open connections for every collection, closed with ``close_loop_pools``. Runs against the
pgvector service (see ``test_knowledge_pgvector_rollback.py`` for the local recipe)."""

from __future__ import annotations

import asyncio
import threading
import uuid

import pytest

from .test_knowledge_pgvector_rollback import _build_adapter, _docs, _dsn, _setup_extension

asyncpg = pytest.importorskip("asyncpg")
pytest.importorskip("pgvector.asyncpg")

from cuga.backend.storage.embedding import prod  # noqa: E402

pytestmark = pytest.mark.pgvector


async def _pool_connections(conn) -> int:
    return await conn.fetchval(
        "SELECT count(*) FROM pg_stat_activity WHERE application_name = $1 AND datname = current_database()",
        prod.LOOP_POOL_APPLICATION_NAME,
    )


async def _table_exists(dsn: str, table: str) -> bool:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", table)
    finally:
        await conn.close()


def test_searches_across_collections_on_one_loop_hold_a_bounded_pool(clean_tenant, monkeypatch):
    dsn = _dsn()
    asyncio.run(_setup_extension(dsn))
    adapters = [
        _build_adapter(dsn, clean_tenant, monkeypatch, f"kb_loop_{i}_{uuid.uuid4().hex[:8]}")
        for i in range(12)
    ]
    for i, adapter in enumerate(adapters):
        adapter.add_documents(_docs(3, f"/tmp/loop{i}.pdf"))
    expected = sorted(d.page_content for d, _ in adapters[0].search("chunk text body", k=3))

    async def main():
        watcher = await asyncpg.connect(dsn)
        peak, stop = 0, asyncio.Event()

        async def watch():
            nonlocal peak
            while not stop.is_set():
                peak = max(peak, await _pool_connections(watcher))
                await asyncio.sleep(0.005)

        watch_task = asyncio.create_task(watch())
        try:
            results = await asyncio.gather(
                *(a.asearch("chunk text body", k=3) for _ in range(8) for a in adapters)
            )
        finally:
            stop.set()
            await watch_task
        await prod.close_loop_pools()
        for _ in range(100):  # the server drops the closed sessions from pg_stat_activity shortly after
            if await _pool_connections(watcher) == 0:
                break
            await asyncio.sleep(0.02)
        left = await _pool_connections(watcher)
        await watcher.close()
        return results, peak, left

    try:
        results, peak, left = asyncio.run(main())
        assert len(results) == 96 and all(len(r) == 3 for r in results)
        assert sorted(d.page_content for d, _ in results[0]) == expected  # same rows as the sync path
        assert 1 <= peak <= prod.LOOP_POOL_MAX_SIZE
        assert left == 0
    finally:
        for adapter in adapters:
            adapter.drop()


def test_dropping_a_collection_while_it_is_searched_on_the_loop(clean_tenant, monkeypatch):
    dsn = _dsn()
    asyncio.run(_setup_extension(dsn))
    adapter = _build_adapter(dsn, clean_tenant, monkeypatch, f"kb_loopdrop_{uuid.uuid4().hex[:8]}")
    adapter.add_documents(_docs(6, "/tmp/loopdrop.pdf"))
    errors: list[str] = []

    async def main():
        stop, searches = asyncio.Event(), [0]

        async def search_until_stopped():
            while not stop.is_set():
                searches[0] += 1
                try:
                    await adapter.asearch("chunk text body", k=3)
                except Exception as exc:  # noqa: BLE001 - reported below
                    errors.append(repr(exc))

        tasks = [asyncio.create_task(search_until_stopped()) for _ in range(16)]
        while searches[0] < 64:
            await asyncio.sleep(0.005)
        await asyncio.to_thread(adapter.drop)
        after_drop = searches[0]
        while searches[0] < after_drop + 64:  # searches keep running after the drop
            await asyncio.sleep(0.005)
        stop.set()
        await asyncio.wait_for(asyncio.gather(*tasks), 60)
        await prod.close_loop_pools()

    try:
        asyncio.run(main())
        assert errors == []
    finally:
        adapter.drop()
    assert not asyncio.run(_table_exists(dsn, adapter._collection))


def test_an_upload_while_the_collection_is_searched_on_the_loop(clean_tenant, monkeypatch):
    """Writes still run on their own short-lived pool; searches on the loop see them once committed."""
    dsn = _dsn()
    asyncio.run(_setup_extension(dsn))
    adapter = _build_adapter(dsn, clean_tenant, monkeypatch, f"kb_loopup_{uuid.uuid4().hex[:8]}")
    adapter.add_documents(_docs(2, "/tmp/first.pdf"))
    errors: list[str] = []

    async def main():
        done = threading.Event()

        def upload():
            try:
                adapter.add_documents(_docs(40, "/tmp/second.pdf"))
            finally:
                done.set()

        upload_thread = threading.Thread(target=upload)
        upload_thread.start()
        while not done.is_set():
            try:
                await asyncio.gather(*(adapter.asearch("chunk text body", k=5) for _ in range(8)))
            except Exception as exc:  # noqa: BLE001 - reported below
                errors.append(repr(exc))
        upload_thread.join()
        rows = await adapter.asearch("chunk text body", k=100)
        await prod.close_loop_pools()
        return rows

    try:
        rows = asyncio.run(main())
        assert errors == []
        assert len(rows) == 42
    finally:
        adapter.drop()


async def _drop_table(dsn: str, table: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(f"DROP TABLE {table}")
    finally:
        await conn.close()


def test_a_search_never_creates_a_dropped_table_again(clean_tenant, monkeypatch):
    # Another process drops the table, for example to reindex it with a new embedder. A search must
    # not create it again with the old vector size: the reindex would then fail on insert. Neither
    # a store that has searched on this loop nor one that has not may run DDL.
    dsn = _dsn()
    asyncio.run(_setup_extension(dsn))
    name = f"kb_loopddl_{uuid.uuid4().hex[:8]}"
    searched = _build_adapter(dsn, clean_tenant, monkeypatch, name)
    searched.add_documents(_docs(3, "/tmp/ddl.pdf"))
    fresh = _build_adapter(dsn, clean_tenant, monkeypatch, name)
    fresh._ensure_store()  # a store for the table that has not searched on this loop yet

    async def main():
        assert len(await searched.asearch("chunk text body", k=3)) == 3
        await _drop_table(dsn, name)
        results = [await searched.asearch("chunk text body", k=3) for _ in range(2)]
        results.append(await fresh.asearch("chunk text body", k=3))
        await prod.close_loop_pools()
        return results

    try:
        assert asyncio.run(main()) == [[], [], []]
        assert not asyncio.run(_table_exists(dsn, name))
    finally:
        searched.drop()


class _Engine:
    """An owner of the loop's pools, as a KnowledgeEngine is."""


def test_one_engine_closing_leaves_the_pool_of_another_engine_open(clean_tenant, monkeypatch):
    # Two SDK agents on one loop have one engine each, and both search through the loop's pool.
    # When one agent closes, the other's searches keep working, including those waiting for a
    # connection; the pool closes when the last engine releases it.
    dsn = _dsn()
    asyncio.run(_setup_extension(dsn))
    adapter = _build_adapter(dsn, clean_tenant, monkeypatch, f"kb_loopclose_{uuid.uuid4().hex[:8]}")
    adapter.add_documents(_docs(3, "/tmp/close.pdf"))
    closing, searching = _Engine(), _Engine()
    errors: list[str] = []

    async def main():
        prod.hold_loop_pools(closing)
        prod.hold_loop_pools(searching)
        stop, searches = asyncio.Event(), [0]

        async def search_until_stopped():
            while not stop.is_set():
                try:
                    assert len(await adapter.asearch("chunk text body", k=3)) == 3
                    searches[0] += 1
                except Exception as exc:  # noqa: BLE001 - reported below
                    errors.append(repr(exc))

        # More searchers than the pool has connections, so some wait for one.
        tasks = [asyncio.create_task(search_until_stopped()) for _ in range(2 * prod.LOOP_POOL_MAX_SIZE)]
        while searches[0] < 20:
            await asyncio.sleep(0.005)
        await prod.release_loop_pools(closing)  # the first agent's aclose()
        after = searches[0]
        while searches[0] < after + 40:
            await asyncio.sleep(0.005)
        stop.set()
        await asyncio.wait_for(asyncio.gather(*tasks), 60)
        await prod.release_loop_pools(searching)  # the last agent's aclose() closes the pool
        watcher = await asyncpg.connect(dsn)
        for _ in range(100):
            if await _pool_connections(watcher) == 0:
                break
            await asyncio.sleep(0.02)
        left = await _pool_connections(watcher)
        await watcher.close()
        return left

    try:
        assert asyncio.run(main()) == 0
        assert errors == []
    finally:
        adapter.drop()


def test_an_engine_searched_on_a_new_loop_each_call_keeps_one_pool(tmp_path, monkeypatch):
    # SDK code that runs each call with asyncio.run: only the engine's first loop gets the shared
    # pool; searches on later loops use a pool of their own for the call, as before, instead of
    # leaving one open on every loop that ended.
    from cuga.backend.knowledge.config import KnowledgeConfig
    from cuga.backend.knowledge.engine import KnowledgeEngine

    dsn = _dsn()
    asyncio.run(_setup_extension(dsn))
    monkeypatch.setattr(
        "cuga.backend.knowledge.engine.knowledge_vector_backend_for_settings", lambda _s: "storage_prod"
    )
    monkeypatch.setattr(
        "cuga.backend.knowledge.engine.get_storage_connection_params",
        lambda: ("prod", str(tmp_path / "cuga.db"), dsn),
    )
    monkeypatch.setattr(prod, "_loop_pools", type(prod._loop_pools)())
    collection = f"kb_loopengine_{uuid.uuid4().hex[:8]}"
    doc = tmp_path / "notes.txt"
    doc.write_text("The warehouse in Haifa ships invoices every Tuesday.\n\n" * 20)
    engine = KnowledgeEngine(
        KnowledgeConfig(
            enabled=True,
            persist_dir=tmp_path / "knowledge",
            embedding_provider="fastembed",
            embedding_model="",
            pgvector_connection_string=dsn,
        )
    )

    async def setup():
        await engine.warmup()
        assert (await engine.ingest(collection, doc))["status"] == "completed"
        return await engine.search(collection, "when are invoices shipped", limit=3)

    async def cleanup():
        await engine.drop_collection(collection)
        await engine.aclose()

    home = asyncio.new_event_loop()  # the loop the app keeps, as a server or an SDK app does
    try:
        assert home.run_until_complete(setup())
        for _ in range(3):  # calls made on a new loop each
            assert asyncio.run(engine.search(collection, "when are invoices shipped", limit=3))
        assert len(prod._loop_pools) == 1  # only the home loop's
        home.run_until_complete(cleanup())
        assert len(prod._loop_pools) == 0  # aclose() on the home loop closed it
    finally:
        home.close()
        engine.shutdown()
