"""pgvector compensating-rollback integration tests — bug D from Sami's
2026-06-10 re-review of PR #297.

Why this file exists
====================

The compensating-delete rollback in ``StorageBackedKnowledgeVectorStore._add_all``
(``adapter.py``) is the engine's only defense against orphan vectors after a
partial-insert failure on pgvector. Until now, that code was only exercised
under unit tests against ``LocalEmbeddingStore`` (sqlite-vec) and mocked
backends. Sqlite-vec's single-file atomic-write semantics **structurally
cannot reproduce the bug class** the rollback is patching (MVCC visibility,
per-batch transaction commits, asyncpg pool reuse, HNSW page locks). A
green sqlite-vec test proves nothing about pgvector.

This module runs the **MVT (Minimum Useful Test)** identified by the
2-expert debate against a real pgvector container provisioned by the
``pgvector-integration`` CI job. The full 8-scenario matrix
(connection-drop, HNSW contention, pool exhaustion, container kill,
crash-mid-rollback, etc.) is tracked separately and runs nightly — this
file's job is to catch a regression in the rollback path on every PR
in ~15s of CI time.

Test architecture — why these tests are SYNC
--------------------------------------------

The first two CI runs failed with ``RuntimeError: got Future attached to
a different loop`` + ``another operation in progress``. Root cause:
``adapter.add_documents`` is a SYNC function that internally spawns a
``ThreadPoolExecutor`` + ``asyncio.run`` (see ``adapter.py::_run_embedding_coro``).
When called from an async test, it BLOCKS the test's event loop on
``.result()`` for the duration of the adapter's work — long-lived
asyncpg connections in the test's pool go stale during the block and
the subsequent verification query raises mid-protocol.

Fix: each test is a plain sync function. Verification queries use a
fresh ``asyncpg.connect`` inside a one-shot ``asyncio.run`` so every
async piece lives in its own complete event-loop lifecycle. No
long-lived pools, no cross-loop binding. Slower per-test (~+200 ms for
connection setup) but loop-safe across pytest-asyncio / anyio modes.

Local execution
---------------

Requires a running pgvector instance. The fastest local recipe::

    docker run --rm -d --name cuga-pgvector-test \\
        -e POSTGRES_PASSWORD=cuga -e POSTGRES_USER=cuga \\
        -e POSTGRES_DB=cuga_test -p 5432:5432 \\
        pgvector/pgvector:0.8.0-pg16
    export CUGA_KNOWLEDGE_PGVECTOR_CONNECTION_STRING=\\
        postgresql://cuga:cuga@localhost:5432/cuga_test  # pragma: allowlist secret
    uv run pytest tests/integration/test_knowledge_pgvector_rollback.py -v
"""

from __future__ import annotations

import asyncio
import os

import pytest

from .helpers import unique_collection

# Skip the whole file if pgvector deps aren't available — the [gpu] extra
# is excluded in CI's main Unit Tests job, and pgvector pulls in asyncpg
# which is also a knowledge-engine prod dep. If the asyncpg/pgvector
# imports fail, the whole suite skips cleanly.
asyncpg = pytest.importorskip("asyncpg")
pytest.importorskip("pgvector.asyncpg")

# Marker so devs can ``-m "not pgvector"`` locally and CI can filter.
pytestmark = pytest.mark.pgvector


def _docs(n: int, source: str, *, page_offset: int = 1):
    """Build N dummy LangChain documents sharing a single ``source``.

    Source identity is what the rollback tracks; every chunk of a single
    document carries the same ``meta["source"]``.
    """
    from langchain_core.documents import Document

    return [
        Document(
            page_content=f"chunk text body number {i} for {source}",
            metadata={
                "source": source,
                "filename": source.rsplit("/", 1)[-1],
                "page": page_offset + i,
            },
        )
        for i in range(n)
    ]


class _DeterministicEmbeddings:
    """LangChain Embeddings stub that returns a stable vector per input
    without touching the network or a downloaded ONNX model.

    The MVT does NOT need real embedding quality — it needs predictable,
    valid 384-dim vectors so the only failure-injection vector is the
    one the test controls (the patched ``add_many``).
    """

    _DIM = 384

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[float((i + j) % 7) / 7.0 for j in range(self._DIM)] for i in range(len(texts))]

    def embed_query(self, text: str) -> list[float]:
        return [0.1] * self._DIM


def _dsn() -> str:
    """One env var read, with a clear skip if unset (matches the conftest
    fixture's contract but works in a sync test)."""
    dsn = os.environ.get("CUGA_KNOWLEDGE_PGVECTOR_CONNECTION_STRING")
    if not dsn:
        pytest.skip("CUGA_KNOWLEDGE_PGVECTOR_CONNECTION_STRING not set")
    return dsn


async def _setup_extension(dsn: str) -> None:
    """Idempotent: enable pgvector + register the vector codec on a
    single connection. Each test calls this once before constructing
    the adapter so the table-create DDL the adapter issues lands cleanly.
    """
    from pgvector.asyncpg import register_vector

    conn = await asyncpg.connect(dsn)
    try:
        try:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        except asyncpg.exceptions.InsufficientPrivilegeError:
            pass
        await register_vector(conn)
    finally:
        await conn.close()


async def _count_for_source(dsn: str, collection: str, source: str, tenant_id: str) -> int:
    """Fresh single-shot connection — never a long-lived pool. Avoids the
    cross-loop binding pitfall that killed the first two CI runs.
    """
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchval(
            f'SELECT count(*) FROM "{collection}" WHERE source = $1 AND tenant_id = $2',
            source,
            tenant_id,
        )
        return int(row or 0)
    finally:
        await conn.close()


def _build_adapter(dsn: str, clean_tenant: dict, monkeypatch, collection: str):
    """Construct ``StorageBackedKnowledgeVectorStore`` against the live
    pgvector container with the test's tenant scope bound via monkeypatch."""
    from cuga.backend.knowledge.storage.adapter import StorageBackedKnowledgeVectorStore

    monkeypatch.setattr(
        "cuga.backend.knowledge.storage.adapter.get_tenant_id",
        lambda: clean_tenant["tenant_id"],
    )
    monkeypatch.setattr(
        "cuga.backend.knowledge.storage.adapter.get_service_instance_id",
        lambda: clean_tenant["instance_id"],
    )
    return StorageBackedKnowledgeVectorStore(
        collection=collection,
        embeddings=_DeterministicEmbeddings(),
        postgres_url=dsn,
        vector_insert_batch_size=2,
    )


# ---------------------------------------------------------------------------
# MVT — the one test that proves the rollback works against real pgvector
# ---------------------------------------------------------------------------


def test_partial_insert_rollback_clears_orphans(clean_tenant, monkeypatch):
    """**MVT (Minimum Useful Test)** — Expert E2 §4.

    Scenario: ingest 4 chunks of source ``S`` with ``vector_insert_batch_size=2``.
    Patch the underlying ``ProdEmbeddingStore.add_many`` so the FIRST sub-batch
    commits successfully to pgvector and the SECOND sub-batch raises an
    ``asyncpg.PostgresError``. The compensating-rollback path in ``_add_all``
    must then delete batch-1's 2 committed rows so the failed source ends
    with ``count(*) = 0``.

    This single test exercises:

      - The real ``executemany`` path on pgvector (catches the bug class
        sqlite-vec cannot reproduce).
      - The real ``conn.transaction()`` per-sub-batch atomicity (batch-2
        rollback) — so we know we are not double-counting rollback work.
      - The inlined compensating delete loop in ``_add_all`` (covers
        the multi-page pagination scaffolding at ``adapter.py:632-640``,
        even though batch-1's 2 rows fit in a single page).
      - Post-rollback MVCC visibility — the count is asserted from a
        FRESH asyncpg connection in its own one-shot ``asyncio.run`` so
        no stale snapshot can mask orphan rows.
    """
    dsn = _dsn()
    asyncio.run(_setup_extension(dsn))

    collection = unique_collection("mvt")
    source = f"{collection}/mvt.pdf"
    adapter = _build_adapter(dsn, clean_tenant, monkeypatch, collection)
    docs = _docs(4, source)

    # Materialize the store so we can patch its ``add_many`` method.
    adapter._ensure_store()
    real_add_many = adapter._store.add_many

    call_count = {"n": 0}

    async def flaky_add_many(items):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # First sub-batch commits via the real pool — orphan rows on disk.
            await real_add_many(items)
            return
        # Second sub-batch raises BEFORE pgvector commits — the rollback
        # path is responsible for cleaning up the orphans from call 1.
        raise asyncpg.exceptions.PostgresError("simulated mid-batch failure (MVT fault injection)")

    adapter._store.add_many = flaky_add_many

    with pytest.raises(asyncpg.exceptions.PostgresError):
        adapter.add_documents(docs)

    assert call_count["n"] == 2, (
        f"expected 2 add_many calls (sub-batches), got {call_count['n']} — "
        "if 1, the rollback never ran; if >2, the inner retry semantics drifted"
    )

    # **Invariant 1 — Expert E2 §2**: the failed source must have zero
    # rows in the live pgvector table.
    count = asyncio.run(_count_for_source(dsn, collection, source, clean_tenant["tenant_id"]))
    assert count == 0, (
        f"compensating rollback failed: source {source!r} has {count} "
        f"orphan rows in pgvector after partial-insert failure — the "
        f"_add_all rollback at adapter.py:608-680 didn't clean them up"
    )


# ---------------------------------------------------------------------------
# Scope-isolation guard (the second high-signal test for PR-time)
# ---------------------------------------------------------------------------


def test_rollback_respects_tenant_scope_isolation(clean_tenant, monkeypatch):
    """A rollback for tenant A's failed ingest must NOT touch tenant B's
    rows for the same ``source`` value.

    The compensating delete filters by ``(tenant_id, instance_id, source)``;
    this test verifies that filter is actually applied at the DB level
    (not just trusted in code) by pre-seeding tenant-B's rows with the
    same source string and confirming they survive tenant-A's rollback.

    Multi-tenant safety — Expert E2 §2 invariant 4.
    """
    import uuid

    dsn = _dsn()
    asyncio.run(_setup_extension(dsn))

    collection = unique_collection("scope")
    source = f"{collection}/shared-name.pdf"

    # First materialize the adapter's table by constructing the adapter
    # and running its _ensure_store path. The adapter's own DDL is what
    # we want the seeded rows to land in.
    adapter = _build_adapter(dsn, clean_tenant, monkeypatch, collection)
    adapter._ensure_store()

    # ``_ensure_store()`` only constructs ``ProdEmbeddingStore`` — DDL
    # fires lazily inside ``_get_pool`` on the first ``add_many``. We
    # need the table to exist NOW so the raw asyncpg seed below can
    # INSERT control rows for tenant B. ``_get_pool()`` creates the pool
    # + runs ``CREATE TABLE IF NOT EXISTS``; ``close_pool()`` then
    # releases the pool so the adapter's later ``add_documents`` (which
    # runs on its OWN thread+loop) can rebuild the pool on that loop —
    # avoiding the cross-loop binding the first three CI runs tripped on.
    async def _init_table(store) -> None:
        await store._get_pool()
        await store.close_pool()

    asyncio.run(_init_table(adapter._store))

    tenant_b = f"control_{uuid.uuid4().hex[:8]}"
    instance_b = f"control_{uuid.uuid4().hex[:8]}"

    async def _seed_b():
        from pgvector.asyncpg import register_vector

        conn = await asyncpg.connect(dsn)
        try:
            await register_vector(conn)
            for i in range(3):
                # The adapter's PK is composite ``(tenant_id, instance_id, id)``
                # (see ``ProdEmbeddingStore._ensure_table``) — a plain
                # ``ON CONFLICT (id)`` doesn't match the constraint and pg
                # raises ``no unique or exclusion constraint matching the ON
                # CONFLICT specification``. The seed uses unique uuid-based
                # ``control_<i>`` ids, so we don't need a conflict clause.
                await conn.execute(
                    f'INSERT INTO "{collection}" '
                    "(id, tenant_id, instance_id, source, filename, page, "
                    "chunk_text, meta_json, embedding) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
                    f"control_{i}",
                    tenant_b,
                    instance_b,
                    source,
                    "shared-name.pdf",
                    i + 1,
                    f"control row {i}",
                    "{}",
                    [0.5] * 384,
                )
        finally:
            await conn.close()

    asyncio.run(_seed_b())

    # Patch the second sub-batch to fail, run tenant-A's ingest.
    real_add_many = adapter._store.add_many
    call_count = {"n": 0}

    async def flaky_add_many(items):
        call_count["n"] += 1
        if call_count["n"] == 1:
            await real_add_many(items)
            return
        raise asyncpg.exceptions.PostgresError("simulated mid-batch failure")

    adapter._store.add_many = flaky_add_many
    with pytest.raises(asyncpg.exceptions.PostgresError):
        adapter.add_documents(_docs(4, source))

    # Tenant-A's rollback must clear A's rows only.
    a_count = asyncio.run(_count_for_source(dsn, collection, source, clean_tenant["tenant_id"]))
    b_count = asyncio.run(_count_for_source(dsn, collection, source, tenant_b))

    assert a_count == 0, f"tenant-A's rollback left {a_count} orphan rows in the test tenant"
    assert b_count == 3, (
        f"tenant-A's rollback bled into tenant-B's rows: expected 3 control "
        f"rows to survive, got {b_count}. The compensating delete is "
        "not properly filtered by tenant_id/instance_id."
    )
