"""Knowledge on pgvector under concurrent requests and with any collection name.

Adapter calls arrive on many threads at once and all run on the process's one Postgres event
loop (``_postgres_loop``), sharing each store's connection pool. These tests run the adapter
from many threads against a real pgvector, as the server does, and use collection names that
are not valid PostgreSQL identifiers as they stand.

Local run: start pgvector as described in ``test_knowledge_pgvector_rollback.py`` and set
``CUGA_KNOWLEDGE_PGVECTOR_CONNECTION_STRING``.
"""

from __future__ import annotations

import asyncio
import hashlib
import threading
import time
import uuid

import pytest

from cuga.backend.knowledge.storage.adapter import _table_name

from .test_knowledge_pgvector_rollback import _build_adapter, _docs, _dsn, _setup_extension

asyncpg = pytest.importorskip("asyncpg")
pytest.importorskip("pgvector.asyncpg")

pytestmark = pytest.mark.pgvector

_THREADS = 8
_SEARCHES_PER_THREAD = 5


def _run_together(fn, n: int, timeout: float = 120) -> list:
    """Start ``fn`` in ``n`` threads at the same moment; return results or raise the first error.

    The threads are daemons with a deadline, so a call that hangs fails the test instead of
    hanging it.
    """
    start = threading.Barrier(n)
    results: list = [None] * n
    errors: list = []

    def worker(i):
        try:
            start.wait(timeout=10)
            results[i] = fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(n)]
    for t in threads:
        t.start()
    deadline = time.monotonic() + timeout
    for t in threads:
        t.join(max(0.0, deadline - time.monotonic()))
    if any(t.is_alive() for t in threads):
        pytest.fail(f"calls still running after {timeout} s")
    if errors:
        raise errors[0]
    return results


async def _table_exists(dsn: str, table: str) -> bool:
    conn = await asyncpg.connect(dsn)
    try:
        return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", table))
    finally:
        await conn.close()


def test_concurrent_searches_all_succeed_on_the_upload_s_pool(clean_tenant, monkeypatch):
    dsn = _dsn()
    asyncio.run(_setup_extension(dsn))
    adapter = _build_adapter(dsn, clean_tenant, monkeypatch, f"kb_conc_{uuid.uuid4().hex[:12]}")
    adapter.add_documents(_docs(6, "/tmp/conc.pdf"))
    pools_created = []
    create_pool = asyncpg.create_pool
    monkeypatch.setattr(
        asyncpg, "create_pool", lambda *a, **kw: pools_created.append(1) or create_pool(*a, **kw)
    )
    try:
        results = _run_together(
            lambda: [len(adapter.search("chunk text body", k=3)) for _ in range(_SEARCHES_PER_THREAD)],
            _THREADS,
        )
        assert results == [[3] * _SEARCHES_PER_THREAD] * _THREADS
        assert pools_created == []  # every search reused the pool the upload opened
    finally:
        adapter.drop()


def test_searches_while_a_file_is_added(clean_tenant, monkeypatch):
    """Searches start and end while an upload has its connection pool open, then the upload ends."""
    dsn = _dsn()
    asyncio.run(_setup_extension(dsn))
    adapter = _build_adapter(dsn, clean_tenant, monkeypatch, f"kb_conc_{uuid.uuid4().hex[:12]}")
    adapter.add_documents(_docs(4, "/tmp/first.pdf"))
    store = adapter._store
    add_many = store.add_many
    upload_has_pool = threading.Event()
    searches_done = threading.Event()

    async def add_many_after_the_searches(items):
        await store._get_pool()
        upload_has_pool.set()
        await asyncio.to_thread(searches_done.wait, 120)
        return await add_many(items)

    monkeypatch.setattr(store, "add_many", add_many_after_the_searches)
    upload_errors: list = []

    def upload():
        try:
            adapter.add_documents(_docs(40, "/tmp/second.pdf"))
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            upload_errors.append(exc)

    adding = threading.Thread(target=upload, daemon=True)
    try:
        adding.start()
        assert upload_has_pool.wait(60), "the upload did not reach the store"
        results = _run_together(lambda: [len(adapter.search("chunk", k=2)) for _ in range(3)], 4)
        searches_done.set()
        adding.join(timeout=120)
        assert not adding.is_alive(), "adding the file did not finish in 120 s"
        if upload_errors:
            raise upload_errors[0]
        assert results == [[2] * 3] * 4
        assert len(adapter.search("chunk", k=100)) == 44
    finally:
        searches_done.set()
        adapter.drop()


@pytest.mark.parametrize(
    "collection",
    [
        f"kb_agent_RunG_{uuid.uuid4().hex[:12]}",  # upper case: agent id "RunG"
        f"kb_agent_{'long_agent_name_' * 4}{uuid.uuid4().hex[:12]}",  # longer than 63 characters
    ],
    ids=["upper-case", "too-long"],
)
def test_collection_that_is_not_a_postgres_identifier(collection, clean_tenant, monkeypatch):
    dsn = _dsn()
    asyncio.run(_setup_extension(dsn))
    adapter = _build_adapter(dsn, clean_tenant, monkeypatch, collection)
    other = _build_adapter(
        dsn, clean_tenant, monkeypatch, collection.swapcase().replace("KB_AGENT_", "kb_agent_")
    )
    try:
        adapter.add_documents(_docs(3, "/tmp/named.pdf"))
        other.add_documents(_docs(1, "/tmp/other.pdf"))

        assert len(adapter.search("chunk", k=10)) == 3
        assert len(other.search("chunk", k=10)) == 1, "two collections must not share a table"

        table = adapter._collection
        assert asyncio.run(_table_exists(dsn, table))
        adapter.drop()
        assert not asyncio.run(_table_exists(dsn, table))
    finally:
        adapter.drop()
        other.drop()


def test_first_calls_on_a_new_collection_at_the_same_time(clean_tenant, monkeypatch):
    """Every call creates the table if it is missing, so the first calls race to create it."""
    dsn = _dsn()
    asyncio.run(_setup_extension(dsn))
    adapter = _build_adapter(dsn, clean_tenant, monkeypatch, f"kb_conc_{uuid.uuid4().hex[:12]}")
    try:
        assert _run_together(lambda: adapter.search("chunk", k=2), _THREADS) == [[]] * _THREADS
    finally:
        adapter.drop()


def test_lookalike_collections_search_and_drop_separately(clean_tenant, monkeypatch):
    """A mixed-case session, the table the first version of the mapping gave it, and a name like
    its new table: three collections, three tables."""
    dsn = _dsn()
    asyncio.run(_setup_extension(dsn))
    first = f"kb_sess_SupportChat_{uuid.uuid4().hex[:8]}"
    earlier = f"{first.lower()[:50]}_{hashlib.sha256(first.encode()).hexdigest()[:12]}"
    adapters = [
        _build_adapter(dsn, clean_tenant, monkeypatch, name)
        for name in (first, earlier, _table_name(first, postgres=True))
    ]
    try:
        assert len({a._collection for a in adapters}) == 3
        for i, adapter in enumerate(adapters):
            adapter.add_documents(_docs(i + 1, f"/tmp/lookalike{i}.pdf"))

        assert [len(a.search("chunk", k=10)) for a in adapters] == [1, 2, 3]
        adapters[0].drop()
        assert [len(a.search("chunk", k=10)) for a in adapters[1:]] == [2, 3]
        adapters[1].drop()
        assert len(adapters[2].search("chunk", k=10)) == 3
    finally:
        for adapter in adapters:
            adapter.drop()


def test_the_table_lock_is_released_when_creating_the_table_fails(clean_tenant, monkeypatch):
    dsn = _dsn()
    asyncio.run(_setup_extension(dsn))
    collection = f"kb_conc_{uuid.uuid4().hex[:12]}"
    adapter = _build_adapter(dsn, clean_tenant, monkeypatch, collection)

    async def fetch(sql: str, *args):
        conn = await asyncpg.connect(dsn)
        try:
            return await conn.fetchval(sql, *args)
        finally:
            await conn.close()

    held_sql = (
        "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND objsubid = 1 "
        "AND ((classid::bigint << 32) | objid::bigint) = hashtextextended($1, 0)"
    )
    # A composite type with the table's name makes the DDL fail after the lock is taken: CREATE
    # TABLE IF NOT EXISTS skips the existing relation and CREATE INDEX cannot index a type.
    asyncio.run(fetch(f"CREATE TYPE {collection} AS (x int)"))
    type_dropped = False
    try:
        with pytest.raises(asyncpg.exceptions.WrongObjectTypeError):
            adapter.search("chunk", k=2)
        assert asyncio.run(fetch(held_sql, collection)) == 0
        asyncio.run(fetch(f"DROP TYPE {collection}"))
        type_dropped = True
        assert adapter.search("chunk", k=2) == []
    finally:
        if not type_dropped:
            asyncio.run(fetch(f"DROP TYPE {collection}"))
        adapter.drop()
