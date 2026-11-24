"""Every connection in a Postgres vector store's pool can write and search vectors (issue #1208).

Concurrent calls on one event loop make the pool open more connections; each of them must accept
a Python list as a vector, as the first one does.

Local run: start pgvector as described in ``test_knowledge_pgvector_rollback.py`` and set
``CUGA_KNOWLEDGE_PGVECTOR_CONNECTION_STRING``.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from cuga.backend.storage.embedding.base import EmbeddingSchemaConfig
from cuga.backend.storage.embedding.prod import ProdEmbeddingStore

asyncpg = pytest.importorskip("asyncpg")
pytest.importorskip("pgvector.asyncpg")

pytestmark = pytest.mark.pgvector


def test_every_pooled_connection_accepts_vectors():
    dsn = os.environ.get("CUGA_KNOWLEDGE_PGVECTOR_CONNECTION_STRING")
    if not dsn:
        pytest.skip("CUGA_KNOWLEDGE_PGVECTOR_CONNECTION_STRING not set")
    table = f"vec_pool_{uuid.uuid4().hex[:12]}"
    schema = EmbeddingSchemaConfig(
        embedding_dim=3, id_column="id", metadata_columns={}, auxiliary_columns={"chunk_text": "text"}
    )

    async def scenario():
        store = ProdEmbeddingStore(dsn, table, schema)
        try:
            await asyncio.gather(
                *(store.add(f"a{i}", [float(i), 0.0, 1.0], {"chunk_text": "a"}) for i in range(8))
            )
            await asyncio.gather(*(store.search([1.0, 0.0, 1.0], 3, {}) for _ in range(8)))
            for i in range(4):  # one at a time, on whichever pooled connection is free
                await store.add(f"b{i}", [0.0, float(i), 1.0], {"chunk_text": "b"})
            return len(await store.search([0.0, 1.0, 1.0], 20, {}))
        finally:
            pool = await store._get_pool()
            async with pool.acquire() as conn:
                await conn.execute(f"DROP TABLE IF EXISTS {table}")
            await store.close_pool()

    assert asyncio.run(scenario()) == 12
