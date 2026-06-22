"""Tests for ``LocalEmbeddingStore.add_many`` (issue #183 step 2)."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlite_vec")

from cuga.backend.knowledge.storage.schema import knowledge_embedding_schema
from cuga.backend.storage.embedding.local import LocalEmbeddingStore


def _make_store(tmp_path: Path, collection: str = "kb_add_many_test") -> LocalEmbeddingStore:
    schema = knowledge_embedding_schema(embedding_dim=4)
    return LocalEmbeddingStore(str(tmp_path / "kb.db"), collection, schema)


def _scope_meta(idx: int, *, source: str = "tests/add_many.pdf") -> dict[str, Any]:
    return {
        "tenant_id": "test_tenant",
        "instance_id": "test_instance",
        "source": source,
        "filename": "add_many.pdf",
        "page": idx + 1,
        "chunk_text": f"chunk {idx}",
        "meta_json": "{}",
    }


def test_add_many_inserts_all_items(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    items = [(f"id_{i}", [float(i) / 10.0, 0.1, 0.2, 0.3], _scope_meta(i)) for i in range(5)]
    asyncio.run(store.add_many(items))

    rows = asyncio.run(store.list({"tenant_id": "test_tenant", "instance_id": "test_instance"}, limit=10))
    assert len(rows) == 5
    assert {r["id"] for r in rows} == {f"id_{i}" for i in range(5)}


def test_add_many_round_trips_metadata(tmp_path: Path) -> None:
    """Metadata fields stored via add_many must come back unchanged via get()."""
    store = _make_store(tmp_path)
    items = [
        ("md_id", [0.1, 0.2, 0.3, 0.4], _scope_meta(7, source="round/trip.pdf")),
    ]
    asyncio.run(store.add_many(items))

    row = asyncio.run(store.get("md_id", tenant_id="test_tenant", instance_id="test_instance"))
    assert row is not None
    assert row["filename"] == "add_many.pdf"
    assert row["page"] == 8  # idx + 1


def test_add_many_empty_is_noop(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    asyncio.run(store.add_many([]))
    rows = asyncio.run(store.list({"tenant_id": "test_tenant", "instance_id": "test_instance"}, limit=10))
    assert rows == []


def test_add_many_fallback_loop_when_executemany_unavailable(tmp_path: Path) -> None:
    """Bypass the live connection's read-only API by calling the private path directly.

    ``sqlite3.Connection`` is a C type whose ``executemany`` cannot be patched on
    the instance or the class, so we cannot use monkeypatch to simulate an
    ``OperationalError``. Instead, drive ``_add_many_sync`` with a wrapper
    connection that raises on ``executemany``: the fallback per-row loop should
    insert all rows inside a single ``commit()``.
    """
    store = _make_store(tmp_path)
    # Touch the real connection so the table exists.
    asyncio.run(store.add("seed", [0.0] * 4, _scope_meta(0, source="seed")))
    real = store._get_conn()

    class _WrappedConn:
        def __init__(self, inner: sqlite3.Connection) -> None:
            self._inner = inner
            self.executemany_calls = 0
            self.execute_calls = 0
            self.commit_calls = 0

        def executemany(self, *args, **kwargs):
            self.executemany_calls += 1
            raise sqlite3.OperationalError("forced fallback")

        def execute(self, *args, **kwargs):
            self.execute_calls += 1
            return self._inner.execute(*args, **kwargs)

        def commit(self) -> None:
            self.commit_calls += 1
            self._inner.commit()

        def rollback(self) -> None:
            self._inner.rollback()

    wrapped = _WrappedConn(real)
    store._conn = wrapped  # type: ignore[assignment]
    try:
        items = [(f"fb_{i}", [float(i) / 10.0, 0.0, 0.0, 0.0], _scope_meta(i)) for i in range(4)]
        store._add_many_sync(items)
    finally:
        store._conn = real

    assert wrapped.executemany_calls == 1, "fast path should be tried exactly once"
    assert wrapped.execute_calls == 4, "fallback should INSERT each row individually"
    assert wrapped.commit_calls == 1, "fallback should still commit only once for the batch"

    rows = asyncio.run(store.list({"tenant_id": "test_tenant", "instance_id": "test_instance"}, limit=20))
    ids = {r["id"] for r in rows}
    assert {f"fb_{i}" for i in range(4)}.issubset(ids), (
        f"expected fallback per-row inserts to land all four ids, got {ids}"
    )
