"""Stale ingest worker bails when ``_apply_generation`` bumps mid-ingest:
patches ``_load_document`` to bump the counter, asserts task lands as
``cancelled`` + ``file_tasks[].status="superseded"`` with audit ``reason``."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from langchain_core.documents import Document

from cuga.backend.knowledge.config import KnowledgeConfig
from cuga.backend.knowledge.engine import KnowledgeEngine


def _cfg(**over):
    d = dict(
        enabled=True,
        persist_dir=Path(tempfile.mkdtemp(prefix="cuga-slice-b-")),
        embedding_provider="fastembed",
    )
    d.update(over)
    return KnowledgeConfig(**d)


def test_stale_ingest_worker_records_superseded_when_apply_generation_bumps():
    eng = KnowledgeEngine(_cfg())
    assert eng._apply_generation == 0

    def fake_load(path):
        eng._apply_generation += 1  # simulate concurrent commit_knowledge_update
        return [Document(page_content="hi", metadata={"source": "f.txt"})]

    eng._load_document = fake_load

    async def run():
        await eng._ensure_metadata_ready()
        await eng._metadata.create_task("t1", "c", 1, {"f.txt": {"filename": "f.txt", "status": "pending"}})
        await eng._ingest_inner(
            "c",
            Path("/tmp/f.txt-does-not-exist"),
            "f.txt",
            "t1",
            True,
            asyncio.Event(),
            skip_file_copy=True,
        )
        return await eng._metadata.get_task("t1")

    task = asyncio.run(run())

    assert task["status"] == "cancelled"
    ft = task["file_tasks"]["f.txt"]
    assert ft["status"] == "superseded"
    assert "config changed mid-ingest" in ft["reason"]
    assert "gen 0 -> 1" in ft["reason"]
    assert eng._apply_generation == 1
