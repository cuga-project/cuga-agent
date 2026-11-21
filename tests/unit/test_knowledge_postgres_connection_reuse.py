"""Knowledge calls on Postgres reuse one connection pool instead of connecting for every call."""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock

import pytest

from cuga.backend.knowledge.storage import adapter as adapter_mod
from cuga.backend.knowledge.storage.adapter import StorageBackedKnowledgeVectorStore
from cuga.backend.storage.embedding.prod import ProdEmbeddingStore

pytestmark = pytest.mark.unit


class _Embeddings:
    def embed_query(self, text: str) -> list[float]:
        return [0.1] * 384

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 384 for _ in texts]


def _fake_pool() -> MagicMock:
    connection = MagicMock()
    connection.fetch = AsyncMock(return_value=[])
    acquire = MagicMock()
    acquire.__aenter__ = AsyncMock(return_value=connection)
    acquire.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire)
    pool.close = AsyncMock()
    return pool


@pytest.fixture
def created_pools(monkeypatch):
    created: list[MagicMock] = []

    async def create_pool(self):
        created.append(_fake_pool())
        return created[-1]

    monkeypatch.setattr(ProdEmbeddingStore, "_create_pool", create_pool)
    return created


def _adapter() -> StorageBackedKnowledgeVectorStore:
    return StorageBackedKnowledgeVectorStore(
        "kb_reuse", _Embeddings(), local_db_path="", postgres_url="postgresql://u@h/db"
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


@pytest.mark.asyncio
async def test_a_call_from_inside_an_event_loop_still_reuses_the_pool(created_pools):
    adapter = _adapter()
    adapter.search("from a thread")
    adapter.search("from a running loop")  # the server's request loop calls in like this

    assert len(created_pools) == 1


@pytest.mark.asyncio
async def test_closing_the_adapter_closes_its_connections(created_pools):
    adapter = _adapter()
    adapter.search("q")

    await adapter.close_pool()  # what KnowledgeEngine.aclose calls for each collection

    created_pools[0].close.assert_awaited_once()
    adapter.search("after close")
    assert len(created_pools) == 2  # a later call opens a new pool


def test_a_call_from_the_postgres_loop_itself_fails_instead_of_hanging(created_pools):
    adapter = _adapter()

    async def nested():
        return adapter.search("q")

    with pytest.raises(RuntimeError, match="Postgres loop"):
        asyncio.run_coroutine_threadsafe(nested(), adapter_mod._postgres_loop()).result(10)
