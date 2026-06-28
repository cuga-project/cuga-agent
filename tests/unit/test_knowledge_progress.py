"""Adapter-level tests for the C5 progress callback (issue #183 step 6).

The engine-side bridge (run_coroutine_threadsafe + drain) is harder to unit
test in isolation because it requires the full ingest path with a metadata
DB. The drain-before-completion guarantee is the kind of thing best
verified end-to-end on a fixture — that is left to the manual bench / an
integration test in a follow-up. What we cover here is the deterministic
adapter-level contract: the callback is invoked the expected number of
times per stage, with monotonic counts, and the ``status`` field is never
present in the payload an emit could carry (so a stray update cannot
un-flip ``status="completed"``).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

pytest.importorskip("sqlite_vec")

from cuga.backend.knowledge.storage.local import create_storage_local_knowledge_store


class _FixedEmbeddings(Embeddings):
    def __init__(self, dim: int = 4):
        self._dim = dim

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[float((i + j) % 7) / 7.0 for j in range(self._dim)] for i in range(len(texts))]

    def embed_query(self, text: str) -> list[float]:
        return [0.0] * self._dim


def _make_docs(n: int) -> list[Document]:
    return [
        Document(
            page_content=f"chunk {i} of progress test",
            metadata={"source": "kb_progress/test.pdf", "filename": "test.pdf", "page": i + 1},
        )
        for i in range(n)
    ]


def test_progress_cb_emits_one_per_embed_subbatch(tmp_path: Path) -> None:
    """``embed`` stage emits once per sub-batch with monotonic done count."""
    store = create_storage_local_knowledge_store(
        "kb_prog_embed", _FixedEmbeddings(4), str(tmp_path / "kb.db"), embedding_batch_size=10
    )
    events: list[tuple[str, int, int]] = []
    store.add_documents(_make_docs(25), progress_cb=lambda s, d, t: events.append((s, d, t)))

    embed_events = [e for e in events if e[0] == "embed"]
    # 25 docs at batch 10 => 10, 10, 5 -> three embed emits with monotonic done.
    assert [d for _, d, _ in embed_events] == [10, 20, 25], f"unexpected: {embed_events}"
    # Total is constant n.
    assert all(t == 25 for _, _, t in embed_events)


def test_progress_cb_emits_insert_start_and_insert(tmp_path: Path) -> None:
    """Both an ``insert_start`` and a final ``insert`` event must fire."""
    store = create_storage_local_knowledge_store(
        "kb_prog_insert", _FixedEmbeddings(4), str(tmp_path / "kb.db"), embedding_batch_size=10
    )
    events: list[tuple[str, int, int]] = []
    store.add_documents(_make_docs(7), progress_cb=lambda s, d, t: events.append((s, d, t)))

    stages = [s for s, _, _ in events]
    assert "insert_start" in stages
    assert stages[-1] == "insert", f"final event should be 'insert', got {stages!r}"
    insert_final = next(e for e in reversed(events) if e[0] == "insert")
    assert insert_final == ("insert", 7, 7), f"unexpected final insert event: {insert_final}"


def test_progress_cb_failure_does_not_break_ingest(tmp_path: Path) -> None:
    """A throwing progress_cb must not propagate; the ingest still completes."""
    store = create_storage_local_knowledge_store(
        "kb_prog_throw", _FixedEmbeddings(4), str(tmp_path / "kb.db"), embedding_batch_size=5
    )

    def boom(stage: str, done: int, total: int) -> None:
        raise RuntimeError("simulated metadata write failure")

    r = store.add_documents(_make_docs(10), progress_cb=boom)
    assert r["num_added"] == 10  # ingest still completed despite the throwing callback


def test_progress_cb_none_is_a_noop(tmp_path: Path) -> None:
    """Passing ``progress_cb=None`` (default) must not change behaviour."""
    store = create_storage_local_knowledge_store(
        "kb_prog_none", _FixedEmbeddings(4), str(tmp_path / "kb.db"), embedding_batch_size=5
    )
    r = store.add_documents(_make_docs(10))  # no progress_cb -> default None
    assert r["num_added"] == 10
