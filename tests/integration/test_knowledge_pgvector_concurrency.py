"""Knowledge on pgvector under concurrent requests and with any collection name.

Adapter calls arrive on many threads at once and all run on the process's one Postgres event
loop (``_postgres_loop``), sharing one connection pool per database. These tests run the adapter
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

    async def add_many_after_the_searches(items, columns=None, **kwargs):
        await store._get_pool()
        upload_has_pool.set()
        await asyncio.to_thread(searches_done.wait, 120)
        return await add_many(items, columns=columns, **kwargs)

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


async def _knowledge_connections(dsn: str) -> int:
    """Client connections to the test database, other than these counting ones and the keyword
    leg's own connections (opened per call, outside the shared pool)."""
    conn = await asyncpg.connect(dsn, server_settings={"application_name": "test-counter"})
    try:
        return await conn.fetchval(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
            "AND backend_type = 'client backend' "
            "AND application_name NOT IN ('test-counter', 'cuga-knowledge-keyword')"
        )
    finally:
        await conn.close()


def test_many_collections_share_a_bounded_pool(clean_tenant, monkeypatch):
    """Every agent and conversation has its own collection; together they hold at most the shared
    pool's connections, not a pool each."""
    from cuga.backend.storage.embedding.prod import SHARED_POOL_MAX_SIZE

    dsn = _dsn()
    asyncio.run(_setup_extension(dsn))
    adapters = [
        _build_adapter(dsn, clean_tenant, monkeypatch, f"kb_many_{uuid.uuid4().hex[:12]}") for _ in range(12)
    ]
    peak = []
    try:

        def use(adapter):
            adapter.add_documents(_docs(2, "/tmp/many.pdf"))
            assert len(adapter.search("chunk text body", k=2)) == 2
            peak.append(asyncio.run(_knowledge_connections(dsn)))

        threads = [threading.Thread(target=use, args=(a,), daemon=True) for a in adapters]
        for t in threads:
            t.start()
        for t in threads:
            t.join(120)
        assert not any(t.is_alive() for t in threads)
        assert len(peak) == len(adapters)  # every collection was written and searched
        assert max(peak) <= SHARED_POOL_MAX_SIZE
    finally:
        for a in adapters:
            a.drop()


def test_dropping_a_collection_while_it_is_searched(clean_tenant, monkeypatch):
    """Searches keep running, many of them waiting for a pooled connection, while the collection is
    dropped: every search returns (results, or nothing once the table is gone), none hangs or fails."""
    dsn = _dsn()
    asyncio.run(_setup_extension(dsn))
    adapter = _build_adapter(dsn, clean_tenant, monkeypatch, f"kb_dropq_{uuid.uuid4().hex[:12]}")
    adapter.add_documents(_docs(6, "/tmp/dropq.pdf"))
    stop, errors, done, searches = threading.Event(), [], [], []

    def search_until_stopped():
        while not stop.is_set():
            searches.append(1)
            try:
                adapter.search("chunk text body", k=3)
            except Exception as exc:  # noqa: BLE001 - reported below
                errors.append(repr(exc))
        done.append(1)

    def wait_for_searches(count: int) -> None:
        deadline = time.monotonic() + 30
        while len(searches) < count:
            assert time.monotonic() < deadline, "searches did not start"
            time.sleep(0.005)

    threads = [threading.Thread(target=search_until_stopped, daemon=True) for _ in range(16)]
    for t in threads:
        t.start()
    wait_for_searches(64)  # every thread is searching, more than the 6 connections a collection gets
    adapter.drop()
    wait_for_searches(len(searches) + 64)  # and searches keep running after the drop
    stop.set()
    for t in threads:
        t.join(60)
    assert len(done) == len(threads), "searches hung after the collection was dropped"
    assert errors == []
    adapter.drop()


async def _drop_table(dsn: str, table: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(f"DROP TABLE IF EXISTS {table}")
    finally:
        await conn.close()


def test_a_table_dropped_by_another_process(clean_tenant, monkeypatch):
    """Another replica drops the table this process still holds open: searches find nothing and the
    next upload creates the table again, as when each call opened its own pool."""
    dsn = _dsn()
    asyncio.run(_setup_extension(dsn))
    adapter = _build_adapter(dsn, clean_tenant, monkeypatch, f"kb_gone_{uuid.uuid4().hex[:12]}")
    adapter.add_documents(_docs(3, "/tmp/gone.pdf"))
    assert len(adapter.search("chunk text body", k=5)) == 3
    try:
        asyncio.run(_drop_table(dsn, adapter._collection))
        assert adapter.search("chunk text body", k=5) == []
        adapter.add_documents(_docs(2, "/tmp/again.pdf"))
        assert len(adapter.search("chunk text body", k=5)) == 2
        # The re-created table has no keyword column yet; keyword search must set it up again.
        assert len(adapter.search_lexical("chunk text body", k=5)) == 2
    finally:
        adapter.drop()


def test_after_the_server_refuses_connections_later_calls_still_work(clean_tenant, monkeypatch):
    """At a role's connection limit a call that needs one more connection is refused. The calls
    after it must use the pool's open connections, not retry the refused connect until those
    connections expire."""
    from urllib.parse import urlsplit, urlunsplit

    dsn = _dsn()
    asyncio.run(_setup_extension(dsn))
    role = f"kb_lim_{uuid.uuid4().hex[:8]}"

    async def admin(*statements: str) -> None:
        conn = await asyncpg.connect(dsn)
        try:
            for statement in statements:
                await conn.execute(statement)
        finally:
            await conn.close()

    try:
        db = urlsplit(dsn).path.lstrip("/")
        asyncio.run(
            admin(
                f"CREATE ROLE {role} LOGIN PASSWORD 'limited' CONNECTION LIMIT 3",
                f'GRANT CONNECT ON DATABASE "{db}" TO {role}',
                f"GRANT USAGE, CREATE ON SCHEMA public TO {role}",
            )
        )
    except asyncpg.exceptions.InsufficientPrivilegeError:
        pytest.skip("the test database user cannot create roles")
    parts = urlsplit(dsn)
    role_dsn = urlunsplit(parts._replace(netloc=f"{role}:limited@{parts.hostname}:{parts.port or 5432}"))
    adapter = _build_adapter(role_dsn, clean_tenant, monkeypatch, f"kb_lim_{uuid.uuid4().hex[:12]}")
    try:
        adapter.add_documents(_docs(4, "/tmp/limit.pdf"))
        from cuga.backend.storage.embedding.prod import _acquire, shared_pool

        async def refuse_a_connect_after_the_pool_went_idle():
            # The role allows 3 connections and the pool holds all 3. A call that needs a 4th is
            # refused, and the refusal comes back after the pool's 3 connections were released, as
            # it does in a burst (refusing takes longer than a search). The pool's 3 open
            # connections then sit idle below the refused slot.
            pool = await shared_pool(role_dsn)
            held = [await pool.acquire() for _ in range(3)]

            async def needs_one_more():
                async with _acquire(pool):
                    pass

            refused = asyncio.ensure_future(needs_one_more())
            await asyncio.sleep(0)  # its connect is under way
            for conn in held:
                await pool.release(conn)
            with pytest.raises(asyncpg.exceptions.TooManyConnectionsError):
                await refused

        adapter._run_embedding_coro(refuse_a_connect_after_the_pool_went_idle())
        assert [len(adapter.search("chunk text body", k=3)) for _ in range(10)] == [3] * 10
    finally:
        adapter.drop()
        asyncio.run(
            admin(
                f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = '{role}'",
                f"DROP OWNED BY {role}",
                f"DROP ROLE {role}",
            )
        )


def test_a_collection_dropped_during_an_upload_is_not_created_again(clean_tenant, monkeypatch):
    """Another process drops the collection between two batches of an upload: the upload fails and
    rolls back instead of creating the table again with half the document in it."""
    dsn = _dsn()
    asyncio.run(_setup_extension(dsn))
    adapter = _build_adapter(dsn, clean_tenant, monkeypatch, f"kb_midup_{uuid.uuid4().hex[:12]}")
    adapter.add_documents(_docs(2, "/tmp/first.pdf"))
    add_many = adapter._store.add_many
    batches: list = []

    async def drop_after_the_first_batch(items, *args, **kwargs):
        await add_many(items, *args, **kwargs)
        batches.append(len(items))
        if len(batches) == 1:
            await _drop_table(dsn, adapter._collection)

    monkeypatch.setattr(adapter._store, "add_many", drop_after_the_first_batch)
    try:
        with pytest.raises(Exception):  # noqa: B017 - the second batch finds no table
            adapter.add_documents(_docs(6, "/tmp/second.pdf"))
        assert batches == [2]
        assert not asyncio.run(_table_exists(dsn, adapter._collection))
    finally:
        adapter.drop()
