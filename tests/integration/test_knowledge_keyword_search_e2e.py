"""Keyword search through the knowledge client, end to end, on SQLite and pgvector.

Files are ingested through ``KnowledgeEngine`` (Docling, the local embedder, the storage
backend) and searched through ``KnowledgeClient.search_envelope``, the same path as the agent's
knowledge tool and the SDK. The dense and keyword legs run concurrently, as in production.

Invoices differ only in their number, so the embedder cannot tell them apart: the right invoice
ranks first only when the keyword leg finds it. pgvector cases need
``CUGA_KNOWLEDGE_PGVECTOR_CONNECTION_STRING``.
"""

from __future__ import annotations

import asyncio
import os

import pytest
import pytest_asyncio

from .helpers import unique_collection

INVOICES = 16
OTHER_LANGUAGES = {
    "hindi.txt": ("हिन्दी भाषा में यह दस्तावेज़ बैकअप नीति का वर्णन करता है।", "हिन्दी"),
    "chinese.txt": (
        "我们的数据备份策略每天执行一次，所有备份文件都会加密并保存在两个不同的数据中心。",
        "备份策略",
    ),
}


def _invoice(i: int) -> str:
    return f"INV-2026-{i:04d}"


@pytest_asyncio.fixture(
    params=[
        pytest.param("sqlite", marks=pytest.mark.slow),
        pytest.param("pgvector", marks=pytest.mark.pgvector),
    ]
)
async def knowledge(request, tmp_path, monkeypatch):
    from cuga.backend.knowledge.client import KnowledgeClient
    from cuga.backend.knowledge.config import KnowledgeConfig
    from cuga.backend.knowledge.engine import KnowledgeEngine

    dsn = ""
    if request.param == "pgvector":
        dsn = os.environ.get("CUGA_KNOWLEDGE_PGVECTOR_CONNECTION_STRING", "")
        if not dsn:
            pytest.skip("CUGA_KNOWLEDGE_PGVECTOR_CONNECTION_STRING not set")
    mode, backend = ("prod", "storage_prod") if dsn else ("local", "storage_local")
    # The engine picks its vector backend from cuga.config settings (storage.mode), so pin both
    # the backend and the connection parameters; otherwise the pgvector case would run on SQLite.
    monkeypatch.setattr(
        "cuga.backend.knowledge.engine.knowledge_vector_backend_for_settings", lambda _s: backend
    )
    monkeypatch.setattr(
        "cuga.backend.knowledge.engine.get_storage_connection_params",
        lambda: (mode, str(tmp_path / "cuga.db"), dsn),
    )
    engine = KnowledgeEngine(
        KnowledgeConfig(
            enabled=True,
            persist_dir=tmp_path / "knowledge",
            embedding_provider="fastembed",
            embedding_model="",
            max_ingest_workers=1,
            max_pending_tasks=INVOICES + len(OTHER_LANGUAGES),
            pgvector_connection_string=dsn,
        )
    )
    await engine.warmup()
    client = KnowledgeClient(engine, default_agent_id=unique_collection("kwe"))
    try:
        yield engine, client, request.param
    finally:
        await engine.drop_collection(client._resolve_collection("agent", None))
        await engine.aclose()
        engine.shutdown()


async def _ingest(engine, client, path) -> None:
    task_id = (await client.ingest(str(path)))["task_id"]
    for _ in range(600):
        task = await engine.get_task(task_id)
        if task["status"] in ("completed", "failed", "cancelled"):
            assert task["status"] == "completed", task
            return
        await asyncio.sleep(0.05)
    raise TimeoutError(f"ingest of {path.name} did not finish")


async def _top_filename(client, query: str) -> str:
    envelope = await client.search_envelope(query, "agent", 5, 0.0)
    return envelope["results"][0]["filename"] if envelope["results"] else ""


@pytest.mark.asyncio
async def test_keyword_match_ranks_first_through_the_client(knowledge, tmp_path):
    engine, client, backend = knowledge
    docs = {
        f"invoice_{i:02d}.txt": f"Invoice {_invoice(i)} was issued to Contoso for the backup service."
        for i in range(INVOICES)
    }
    docs.update({name: text for name, (text, _query) in OTHER_LANGUAGES.items()})
    for name, text in docs.items():
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        await _ingest(engine, client, path)

    (adapter,) = engine._vector_stores.values()
    assert adapter._use_prod == (backend == "pgvector"), f"{backend} case ran on the wrong storage backend"

    misses = [
        i for i in range(INVOICES) if await _top_filename(client, _invoice(i)) != f"invoice_{i:02d}.txt"
    ]
    assert not misses, f"invoice lookups not ranked first: {misses}"
    for name, (_text, query) in OTHER_LANGUAGES.items():
        assert await _top_filename(client, query) == name, f"{query!r} did not rank {name} first"
