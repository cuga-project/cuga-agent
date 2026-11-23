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
