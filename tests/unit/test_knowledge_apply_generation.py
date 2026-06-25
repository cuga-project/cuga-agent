"""Slice B: engine apply_generation counter + ReindexSupersededError.

The race this guards against: user picks profile A (e.g. ``standard``)
then picks B (``max_quality``) before A's reindex finishes embedding.
Without Slice B, A's workers keep writing vectors with the old
embedder until they complete, then B's reindex runs — orphan vectors,
mixed state, "double reindex" feel. With Slice B, A's workers detect
the apply_generation bump at their next batch boundary, raise
``ReindexSupersededError``, and exit cleanly; the task ends as
``cancelled`` (SQL-level) with ``file_tasks[filename].status="superseded"``
and a ``reason`` field for audit.

This test exercises the real ``_ingest_inner`` path: it monkey-patches
``_load_document`` to bump the engine counter mid-ingest (simulating a
concurrent ``commit_knowledge_update``) and asserts the worker
detects the bump at the post-parse check and records the supersede.
Implicitly covers: counter is read at worker start, ``_check_supersede``
fires after parse, ``ReindexSupersededError`` is caught, audit fields
land in the metadata store.
"""

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

    # Engine boots at generation 0.
    assert eng._apply_generation == 0

    # Patch _load_document: returns a doc AND bumps the engine's
    # apply_generation. That simulates a concurrent commit_knowledge_update
    # landing while this worker was parsing. The post-parse _check_supersede
    # in _ingest_inner should observe the mismatch (worker captured 0,
    # engine now at 1) and raise ReindexSupersededError.
    def fake_load(path):
        eng._apply_generation += 1
        return [Document(page_content="hi", metadata={"source": "f.txt"})]

    eng._load_document = fake_load

    async def run():
        await eng._ensure_metadata_ready()
        # _ingest_inner expects an existing task row; create it.
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

    # SQL-level status is "cancelled" because the existing CHECK constraint
    # on the tasks table only admits {pending, running, completed, failed,
    # cancelled}. The supersede-vs-user-cancel distinction lives in
    # file_tasks where we record the new status + reason.
    assert task["status"] == "cancelled", f"expected cancelled, got {task['status']!r}"
    ft = task["file_tasks"]["f.txt"]
    assert ft["status"] == "superseded", f"expected file status superseded, got {ft['status']!r}"
    assert "config changed mid-ingest" in ft["reason"], f"reason missing audit detail: {ft.get('reason')!r}"
    assert "gen 0" in ft["reason"] and "1" in ft["reason"], (
        f"reason missing worker_gen/current_gen: {ft.get('reason')!r}"
    )

    # Counter ends at 1 (the simulated bump) — sanity that we exercised the path.
    assert eng._apply_generation == 1
