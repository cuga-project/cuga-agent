"""Deleting a document must make it not be there, even mid-upload.

Delete used to ignore in-flight ingests entirely (#691), so the button did
nothing in two different ways:

* during a FIRST upload the documents row does not exist yet — it is written
  near the end of ingest — so ``mark_deleting`` matched nothing, the request
  404'd, and the ingest carried on and indexed the file anyway;
* during a RE-UPLOAD the delete succeeded and the row vanished, then the
  in-flight ingest reached ``add_document`` and wrote it straight back.

Both are "I clicked delete and the file is still there".
"""

from __future__ import annotations

import asyncio
import tempfile
import threading
from pathlib import Path

import pytest
from langchain_core.documents import Document

from cuga.backend.knowledge.config import KnowledgeConfig
from cuga.backend.knowledge.engine import DocumentNotFoundError, KnowledgeEngine

COLL = "c"
FNAME = "f.txt"
MISSING = Path("/tmp/f.txt-does-not-exist")


def _cfg(**over):
    d = dict(
        enabled=True,
        persist_dir=Path(tempfile.mkdtemp(prefix="cuga-del-")),
        embedding_provider="fastembed",
    )
    d.update(over)
    return KnowledgeConfig(**d)


def _engine() -> KnowledgeEngine:
    eng = KnowledgeEngine(_cfg())

    async def _noop(*a, **k):
        return None

    async def _fake_insert(*a, **k):
        return {"num_added": 1, "num_skipped": 0}

    eng._ensure_collection_config = _noop
    eng._ensure_vector_store_cached = _noop
    eng._insert_documents_async = _fake_insert
    # The vector/file removal is real I/O against a store we never built.
    eng._delete_vector_and_file = lambda collection, filename: None
    return eng


async def _new_task(eng: KnowledgeEngine, task_id: str = "t1") -> None:
    await eng._ensure_metadata_ready()
    await eng._metadata.create_task(
        task_id, COLL, 1, {FNAME: {"filename": FNAME, "status": "pending"}}
    )


def _blocking_parse(started: threading.Event, release: threading.Event):
    def fake_load(path):
        started.set()
        assert release.wait(5), "test deadlock: parse never released"
        return [Document(page_content="hi", metadata={"source": FNAME})]

    return fake_load


@pytest.mark.unit
async def test_delete_during_first_upload_stops_the_ingest(eng=None):
    """Variant A: nothing indexed yet, so delete must cancel rather than 404."""
    eng = _engine()
    await _new_task(eng)
    started, release = threading.Event(), threading.Event()
    eng._load_document = _blocking_parse(started, release)

    cancel_event = asyncio.Event()
    eng._active_tasks["t1"] = cancel_event
    worker = asyncio.create_task(
        eng._ingest_inner(COLL, MISSING, FNAME, "t1", True, cancel_event, skip_file_copy=True)
    )
    await asyncio.to_thread(started.wait, 5)

    # Must NOT raise DocumentNotFoundError: there is no row yet, but there is
    # an ingest to stop, and stopping it is real work.
    await eng.delete_document(COLL, FNAME)

    release.set()
    await worker

    assert (await eng._metadata.get_task("t1"))["status"] == "cancelled"
    assert not await eng._metadata.document_exists(COLL, FNAME), (
        "the ingest indexed the document after it was deleted"
    )


@pytest.mark.unit
async def test_delete_during_reupload_is_not_resurrected():
    """Variant B: the ingest must not write the document back after the delete."""
    eng = _engine()
    await eng._ensure_metadata_ready()
    # Seed an already-indexed document.
    await eng._metadata.add_document(COLL, FNAME, 3, preview="hi")
    assert await eng._metadata.document_exists(COLL, FNAME)

    # A re-upload of the same name is now in flight.
    await _new_task(eng, "t2")
    started, release = threading.Event(), threading.Event()
    eng._load_document = _blocking_parse(started, release)
    cancel_event = asyncio.Event()
    eng._active_tasks["t2"] = cancel_event
    worker = asyncio.create_task(
        eng._ingest_inner(COLL, MISSING, FNAME, "t2", True, cancel_event, skip_file_copy=True)
    )
    await asyncio.to_thread(started.wait, 5)

    await eng.delete_document(COLL, FNAME)

    release.set()
    await worker

    assert not await eng._metadata.document_exists(COLL, FNAME), (
        "deleted document was resurrected by the in-flight ingest"
    )


@pytest.mark.unit
async def test_delete_with_no_document_and_no_ingest_still_404s():
    """The stop-the-ingest path must not turn a genuine miss into a success."""
    eng = _engine()
    await eng._ensure_metadata_ready()
    with pytest.raises(DocumentNotFoundError):
        await eng.delete_document(COLL, "never-existed.txt")


@pytest.mark.unit
async def test_delete_of_an_indexed_document_still_works():
    """The ordinary path is unchanged when nothing is in flight."""
    eng = _engine()
    await eng._ensure_metadata_ready()
    await eng._metadata.add_document(COLL, FNAME, 3, preview="hi")

    await eng.delete_document(COLL, FNAME)

    assert not await eng._metadata.document_exists(COLL, FNAME)
