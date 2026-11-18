"""Knowledge on pgvector under concurrent requests and with any collection name.

Every adapter call runs on its own short-lived event loop (``_run_embedding_coro``), so
requests that overlap use the store from several loops at once. These tests run the
adapter from many threads against a real pgvector, as the server does, and use
collection names that are not valid PostgreSQL identifiers as they stand.

Local run: start pgvector as described in ``test_knowledge_pgvector_rollback.py`` and set
``CUGA_KNOWLEDGE_PGVECTOR_CONNECTION_STRING``.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid

import pytest

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


def test_concurrent_searches_all_succeed(clean_tenant, monkeypatch):
    dsn = _dsn()
    asyncio.run(_setup_extension(dsn))
    adapter = _build_adapter(dsn, clean_tenant, monkeypatch, f"kb_conc_{uuid.uuid4().hex[:12]}")
    adapter.add_documents(_docs(6, "/tmp/conc.pdf"))
    try:
        results = _run_together(
            lambda: [len(adapter.search("chunk text body", k=3)) for _ in range(_SEARCHES_PER_THREAD)],
            _THREADS,
        )
        assert results == [[3] * _SEARCHES_PER_THREAD] * _THREADS
    finally:
        adapter.drop()


def test_searches_while_a_file_is_added(clean_tenant, monkeypatch):
    dsn = _dsn()
    asyncio.run(_setup_extension(dsn))
    adapter = _build_adapter(dsn, clean_tenant, monkeypatch, f"kb_conc_{uuid.uuid4().hex[:12]}")
    adapter.add_documents(_docs(4, "/tmp/first.pdf"))
    try:
        adding = threading.Thread(
            target=adapter.add_documents, args=(_docs(40, "/tmp/second.pdf"),), daemon=True
        )
        adding.start()
        results = _run_together(lambda: [len(adapter.search("chunk", k=2)) for _ in range(3)], 4)
        adding.join(timeout=120)
        assert not adding.is_alive(), "adding the file did not finish in 120 s"
        assert results == [[2] * 3] * 4
        assert len(adapter.search("chunk", k=100)) == 44
    finally:
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
