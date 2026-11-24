"""A reindex whose vector drop fails must stop with the old collection intact.

The engine logged a failed ``adapter.drop()`` at DEBUG, deleted the collection's metadata and
re-ingested every file on top of the old vectors: mixed rows from two runs, or "expected 384
dimensions, not 768" on the next insert. Seen for real with one refused Postgres connection.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from cuga.backend.knowledge.config import KnowledgeConfig
from cuga.backend.knowledge.engine import KnowledgeEngine, _sanitize_collection

pytestmark = pytest.mark.unit


class _UnreachableStore:
    def drop(self):
        raise ConnectionError("server rejected SSL upgrade")


def test_reindex_stops_when_the_old_vectors_cannot_be_dropped(monkeypatch):
    eng = KnowledgeEngine(
        KnowledgeConfig(enabled=True, persist_dir=Path(tempfile.mkdtemp(prefix="cuga-drop-")))
    )
    collection = _sanitize_collection("kb_agent_x")

    async def _no_embeddings(*_a, **_k):
        return None

    monkeypatch.setattr(eng, "_resolve_embeddings_for_collection", _no_embeddings)

    async def scenario():
        await eng._ensure_metadata_ready()
        await eng._metadata.set_collection_config(collection, "fastembed", "BAAI/bge-small-en-v1.5", 384)
        files = eng._files_dir / collection
        files.mkdir(parents=True)
        (files / "policy.txt").write_text("The policy covers dental.")
        eng._vector_stores[collection] = _UnreachableStore()
        with pytest.raises(ConnectionError):
            await eng.reindex(collection)
        return await eng._metadata.get_collection_config(collection), await eng._metadata.list_tasks(
            collection
        )

    config, tasks = asyncio.run(scenario())

    assert config is not None, "the collection's metadata was deleted although its vectors remain"
    assert tasks == [], "files were queued for re-ingest on top of the old vectors"
    assert collection not in eng._reindex_in_progress
