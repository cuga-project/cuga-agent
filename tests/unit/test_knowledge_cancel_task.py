"""Cancelling an ingest must record an OUTCOME, not just an intent.

Before #685 ``cancel_task`` set an in-memory ``asyncio.Event`` and, for a
``running`` task, persisted nothing — so the row stayed ``running`` forever
and the pending/running dedup guard kept rejecting re-uploads of that
filename with a 409. The worker made it worse: it checked the event exactly
once, before parsing, so a cancel arriving any later had no effect at all.

The invariant these tests pin:

    a task row is ``cancelled`` IFF the worker is guaranteed never to touch
    the vector store, the documents table, or the files dir.

That "iff" is load-bearing in both directions. Persisting ``cancelled`` is
what releases the dedup guard, so it must not happen while a worker may still
write — otherwise a re-upload is admitted and races the live insert, and
because ``_insert_documents_async`` does delete-by-source + add under
``replace_duplicates``, the loser of that race wins the content.
"""

from __future__ import annotations

import asyncio
import tempfile
import threading
from pathlib import Path
from typing import Any

import pytest
from langchain_core.documents import Document

from cuga.backend.knowledge.config import KnowledgeConfig
from cuga.backend.knowledge.engine import DocumentExistsError, KnowledgeEngine

COLL = "c"
FNAME = "f.txt"
MISSING = Path("/tmp/f.txt-does-not-exist")


def _cfg(**over):
    d = dict(
        enabled=True,
        persist_dir=Path(tempfile.mkdtemp(prefix="cuga-cancel-")),
        embedding_provider="fastembed",
    )
    d.update(over)
    return KnowledgeConfig(**d)


def _engine() -> KnowledgeEngine:
    """An engine whose insert path is stubbed out.

    Every test here is about control flow around the insert, never the insert
    itself, so the vector-store setup is neutralized. Tests that care assert
    the stub was *not* reached.
    """
    eng = KnowledgeEngine(_cfg())

    async def _noop(*a, **k):
        return None

    async def _fake_insert(*a, **k):
        return {"num_added": 1, "num_skipped": 0}

    eng._ensure_collection_config = _noop
    eng._ensure_vector_store_cached = _noop
    eng._insert_documents_async = _fake_insert
    return eng


async def _new_task(eng: KnowledgeEngine, task_id: str = "t1") -> None:
    await eng._ensure_metadata_ready()
    await eng._metadata.create_task(task_id, COLL, 1, {FNAME: {"filename": FNAME, "status": "pending"}})


def _blocking_parse(started: threading.Event, release: threading.Event):
    """A parse that parks until the test lets it go.

    ``_load_document`` is sync and runs via ``asyncio.to_thread``, so a pair
    of threading Events gives an exact rendezvous with no sleeps.
    """

    def fake_load(path):
        started.set()
        assert release.wait(5), "test deadlock: parse never released"
        return [Document(page_content="hi", metadata={"source": FNAME})]

    return fake_load


# --- durable cancel ----------------------------------------------------------


@pytest.mark.unit
async def test_cancel_while_running_persists_cancelled_status():
    """The direct regression: ``running`` used to persist nothing."""
    eng = _engine()
    await _new_task(eng)
    started, release = threading.Event(), threading.Event()
    eng._load_document = _blocking_parse(started, release)

    cancel_event = asyncio.Event()
    eng._active_tasks["t1"] = cancel_event
    worker = asyncio.create_task(
        eng._ingest_inner(COLL, MISSING, FNAME, "t1", True, cancel_event, skip_file_copy=True)
    )
    await asyncio.to_thread(started.wait, 5)  # parse provably in flight

    returned = await eng.cancel_task("t1")
    assert returned is not None and returned["status"] == "cancelled"

    # Re-read from the store, not the return value — the persisted row is
    # what the dedup guard consults.
    persisted = await eng._metadata.get_task("t1")
    assert persisted["status"] == "cancelled"
    assert persisted["file_tasks"][FNAME]["status"] == "cancelled"

    release.set()
    await worker


@pytest.mark.unit
async def test_cancelled_running_task_does_not_block_reupload():
    """The #685 reproduction, at both guard layers.

    Asserted while the doomed worker is still parsing — that is the whole
    point: the user should not have to wait for a cancelled ingest to finish.
    """
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

    await eng.cancel_task("t1")

    # Layer 1 (advisory) and layer 2 (atomic, under the collection lock).
    tmp = Path(tempfile.mkdtemp(prefix="cuga-reupload-")) / FNAME
    tmp.write_text("replacement content")
    assert await eng._sanitize_and_validate(COLL, tmp, True) == FNAME
    entry = await eng._create_task_entry(COLL, FNAME)
    assert entry["task_id"] != "t1"

    release.set()
    await worker

    # The doomed worker must not have resurrected its own row.
    assert (await eng._metadata.get_task("t1"))["status"] == "cancelled"


@pytest.mark.unit
async def test_cancelled_worker_never_touches_the_vector_store():
    """The invariant that makes durable cancel safe."""
    eng = _engine()
    await _new_task(eng)

    async def _must_not_insert(*a, **k):
        raise AssertionError("cancelled worker reached the vector store")

    eng._insert_documents_async = _must_not_insert

    started, release = threading.Event(), threading.Event()
    eng._load_document = _blocking_parse(started, release)

    cancel_event = asyncio.Event()
    eng._active_tasks["t1"] = cancel_event
    worker = asyncio.create_task(
        eng._ingest_inner(COLL, MISSING, FNAME, "t1", True, cancel_event, skip_file_copy=True)
    )
    await asyncio.to_thread(started.wait, 5)

    await eng.cancel_task("t1")
    release.set()
    await worker  # must not raise the AssertionError above

    assert not await eng._metadata.document_exists(COLL, FNAME)
    task = await eng._metadata.get_task("t1")
    assert task["status"] == "cancelled"
    assert task["file_tasks"][FNAME]["status"] == "cancelled"


# --- the point of no return ---------------------------------------------------


def _park_at_insert(eng: KnowledgeEngine):
    """Stub the insert so it parks, and report when it has been entered."""
    entered, gate = asyncio.Event(), asyncio.Event()

    async def fake_insert(*a, **k):
        entered.set()
        await gate.wait()
        return {"num_added": 1, "num_skipped": 0}

    eng._insert_documents_async = fake_insert
    eng._load_document = lambda p: [Document(page_content="hi", metadata={"source": FNAME})]
    return entered, gate


@pytest.mark.unit
async def test_cancel_during_insert_is_refused_and_task_completes():
    """Once the chunks are landing, "cancelled" would be a lie."""
    eng = _engine()
    await _new_task(eng)
    entered, gate = _park_at_insert(eng)

    cancel_event = asyncio.Event()
    eng._active_tasks["t1"] = cancel_event
    worker = asyncio.create_task(
        eng._ingest_inner(COLL, MISSING, FNAME, "t1", True, cancel_event, skip_file_copy=True)
    )
    await asyncio.wait_for(entered.wait(), 5)

    assert "t1" in eng._uncancellable_tasks
    returned = await eng.cancel_task("t1")
    assert returned["status"] != "cancelled"
    assert (await eng._metadata.get_task("t1"))["status"] == "running"

    gate.set()
    await worker

    assert (await eng._metadata.get_task("t1"))["status"] == "completed"
    # And the flag is not leaked once the worker is done.
    assert "t1" not in eng._uncancellable_tasks


@pytest.mark.unit
async def test_cancel_during_insert_does_not_open_the_duplicate_window():
    """Guards the data-integrity race a naive fix would introduce.

    If cancel terminalized the row here, this re-upload would be admitted and
    would race the in-flight insert of the same source_id — and since
    ``_insert_documents_async`` deletes by source then adds, the later writer
    wins the content.

    Probed via the advisory layer on purpose: it takes no collection lock, so
    it can answer while the insert is in flight. The atomic layer is doubly
    safe here — it would simply block on the collection lock the worker holds
    until the insert finishes — but that makes it untestable without a
    deadlock, and it is the advisory answer the upload route surfaces first.
    """
    eng = _engine()
    await _new_task(eng)
    entered, gate = _park_at_insert(eng)

    cancel_event = asyncio.Event()
    eng._active_tasks["t1"] = cancel_event
    worker = asyncio.create_task(
        eng._ingest_inner(COLL, MISSING, FNAME, "t1", True, cancel_event, skip_file_copy=True)
    )
    await asyncio.wait_for(entered.wait(), 5)
    await eng.cancel_task("t1")

    tmp = Path(tempfile.mkdtemp(prefix="cuga-race-")) / FNAME
    tmp.write_text("replacement content")
    with pytest.raises(DocumentExistsError):
        await eng._sanitize_and_validate(COLL, tmp, True)

    gate.set()
    await worker


# --- ordering, orphans, idempotence -------------------------------------------


@pytest.mark.unit
async def test_cancel_before_running_flip_does_not_resurrect_running():
    """A cancel landing while queued on the semaphore must stick.

    The worker's first write is ``status="running"``. If the cancel check sits
    after it, the row flips back to running and re-blocks the guard.
    """
    eng = _engine()
    await _new_task(eng)
    eng._load_document = lambda p: [Document(page_content="hi", metadata={"source": FNAME})]

    written: list[Any] = []
    real_update = eng._metadata.update_task

    async def recording_update(task_id, **kwargs):
        if "status" in kwargs:
            written.append(kwargs["status"])
        return await real_update(task_id, **kwargs)

    eng._metadata.update_task = recording_update

    cancel_event = asyncio.Event()
    cancel_event.set()  # cancelled before the worker got a slot
    await eng._ingest_inner(COLL, MISSING, FNAME, "t1", True, cancel_event, skip_file_copy=True)

    assert "running" not in written, f"row was flipped to running after cancel: {written}"
    assert (await eng._metadata.get_task("t1"))["status"] == "cancelled"


@pytest.mark.unit
async def test_cancel_terminalizes_orphaned_running_row():
    """A ``running`` row with no live worker is now user-fixable.

    Without this, an orphan (worker died, or the _active_tasks entry leaked)
    wedges the filename until a restart runs recover_stale_tasks.
    """
    eng = _engine()
    await _new_task(eng)
    await eng._metadata.update_task("t1", status="running")
    assert "t1" not in eng._active_tasks

    task = await eng.cancel_task("t1")
    assert task["status"] == "cancelled"
    assert not eng._blocks_reupload(task, FNAME)


@pytest.mark.unit
async def test_cancel_is_idempotent_on_terminal_task():
    eng = _engine()
    await _new_task(eng)
    await eng._metadata.update_task("t1", status="completed")

    before = await eng._metadata.get_task("t1")
    task = await eng.cancel_task("t1")
    assert task["status"] == "completed"
    assert task["updated_at"] == before["updated_at"], "terminal task must not be rewritten"


@pytest.mark.unit
async def test_cancel_preserves_sibling_file_tasks():
    """Pins the blob-replace semantics and the missing-status default.

    ``update_task`` replaces the whole file_tasks map, so cancel must
    read-modify-write it. And a status-less entry is mid-ingest (a progress
    emit replaced it), so it must be treated as live, not terminal.
    """
    eng = _engine()
    await eng._ensure_metadata_ready()
    await eng._metadata.create_task(
        "t1",
        COLL,
        2,
        {
            "done.md": {"filename": "done.md", "status": "indexed"},
            # No status key — exactly what a progress emit leaves behind.
            "live.md": {"filename": "live.md", "stage": "embed", "progress": {"done": 3, "total": 9}},
        },
    )
    await eng._metadata.update_task("t1", status="running")

    task = await eng.cancel_task("t1")

    fts = task["file_tasks"]
    assert fts["done.md"]["status"] == "indexed", "terminal sibling must survive"
    assert fts["live.md"]["status"] == "cancelled"
    assert fts["live.md"]["stage"] == "embed", "progress audit trail must survive"


@pytest.mark.unit
def test_blocks_reupload_treats_unknown_status_as_live():
    """Unknown must never read as terminal — that would race a live insert."""
    eng = _engine()
    live = {"status": "running", "file_tasks": {FNAME: {"filename": FNAME, "stage": "embed"}}}
    assert eng._blocks_reupload(live, FNAME)

    for terminal in ("cancelled", "skipped", "failed", "indexed", "superseded"):
        task = {"status": "running", "file_tasks": {FNAME: {"filename": FNAME, "status": terminal}}}
        assert not eng._blocks_reupload(task, FNAME), terminal

    # Parent terminal, or the file simply absent.
    assert not eng._blocks_reupload({"status": "completed", "file_tasks": {FNAME: {}}}, FNAME)
    assert not eng._blocks_reupload({"status": "running", "file_tasks": {}}, FNAME)


@pytest.mark.unit
async def test_cancel_before_worker_registers_its_event_is_still_honoured():
    """Cancel can land before ``_run_ingest`` registers its cancel event.

    routes.py creates the task row, then schedules ``_run_ingest`` as a
    background task. Between those two points ``_active_tasks`` has no entry,
    so ``cancel_task`` persists ``cancelled`` without any event to set. The
    worker then builds a *fresh, unset* event — so an in-memory-only check
    sails straight past it, writes ``running``, and goes on to insert. That
    resurrects a row the dedup guard has already released, which is exactly
    the duplicate-content race the point-of-no-return design exists to stop.

    The worker must therefore also consult the persisted status before its
    first write.
    """
    eng = _engine()
    await _new_task(eng)

    async def _must_not_insert(*a, **k):
        raise AssertionError("cancelled task reached the vector store")

    eng._insert_documents_async = _must_not_insert
    eng._load_document = lambda p: [Document(page_content="hi", metadata={"source": FNAME})]

    # Cancel arrives while the row is pending and BEFORE any event registration.
    assert "t1" not in eng._active_tasks
    await eng.cancel_task("t1")
    assert (await eng._metadata.get_task("t1"))["status"] == "cancelled"

    # Now the worker finally starts, with its own freshly created (unset) event,
    # exactly as _run_ingest builds it.
    await eng._ingest_inner(COLL, MISSING, FNAME, "t1", True, asyncio.Event(), skip_file_copy=True)

    task = await eng._metadata.get_task("t1")
    assert task["status"] == "cancelled", f"cancelled row was resurrected to {task['status']}"
    assert not await eng._metadata.document_exists(COLL, FNAME)


@pytest.mark.unit
async def test_row_and_document_never_disagree_when_cancel_races_completion():
    """Close the last interleaving: a "cancelled" row over an indexed document.

    ``update_task`` is last-write-wins with no CAS. Without a lock around
    read-decide-write, the worker's ``status="completed"`` could land between
    ``cancel_task``'s read (which saw ``running``) and its UPDATE, leaving the
    row ``cancelled`` while the document really is indexed.

    Asserted as an invariant rather than a fixed outcome, because either side
    may legitimately win the race. What must never happen is the two
    disagreeing.
    """
    eng = _engine()
    await _new_task(eng)
    entered, gate = _park_at_insert(eng)

    cancel_event = asyncio.Event()
    eng._active_tasks["t1"] = cancel_event
    worker = asyncio.create_task(
        eng._ingest_inner(COLL, MISSING, FNAME, "t1", True, cancel_event, skip_file_copy=True)
    )
    await asyncio.wait_for(entered.wait(), 5)

    # Release the insert and cancel in the same tick so the terminal write and
    # the cancel write contend for the task write lock.
    gate.set()
    cancelled, _ = await asyncio.gather(eng.cancel_task("t1"), worker)

    task = await eng._metadata.get_task("t1")
    indexed = await eng._metadata.document_exists(COLL, FNAME)
    assert task["status"] in ("completed", "cancelled")
    if task["status"] == "cancelled":
        assert not indexed, "row says cancelled but the document is indexed"
    else:
        assert indexed, "row says completed but no document was indexed"
    # The returned row may legitimately be the pre-read snapshot: when the
    # cancel is refused past the point of no return we hand back the live row
    # so the caller keeps polling to the real terminal status. What it must
    # never do is claim "cancelled" for a task that indexed its document.
    assert cancelled["status"] != "cancelled" or not indexed
