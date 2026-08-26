"""Restart recovery must tolerate every file-task shape that reaches the DB.

``recover_stale_tasks`` runs from ``_ensure_metadata_ready`` on the first
knowledge call after startup. It has no ``try/except`` around it and never
latches ``_metadata_ready``, so anything it raises re-raises on *every*
subsequent call — one poisoned row takes the whole knowledge surface down
until the database is hand-edited.

The shape that poisoned it (#683) is not corrupt data, it is normal steady
state: the ingest worker's progress emits deliberately write a file-task entry
with no ``status`` key, and ``update_task`` replaces the whole entry rather
than merging, so any task killed mid-ingest leaves one behind. SQLite
tolerated this; Postgres did ``ft["status"]`` and raised ``KeyError``. These
tests pin the tolerance in the shared helper and assert both backends route
through it, so the two cannot drift apart again.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from cuga.backend.knowledge.metadata.base import (
    INTERRUPTED_ERROR,
    mark_file_tasks_interrupted,
    normalize_file_tasks,
)
from cuga.backend.knowledge.metadata.postgres_store import PostgresKnowledgeMetadata
from cuga.backend.knowledge.metadata.sqlite_store import SqliteKnowledgeMetadata

# The exact payload from the #683 report: a progress emit that landed after
# Docling finished parsing, with no ``status`` key.
PROGRESS_ENTRY: dict[str, Any] = {
    "filename": "summary-plan-description.md",
    "stage": "parsed",
    "progress": {"done": 182, "total": 182},
}


class _RecoveryFakePostgres(PostgresKnowledgeMetadata):
    """Run the real ``recover_stale_tasks`` against in-memory I/O.

    Only the asyncpg boundary is replaced, so the store's own recovery logic
    is what executes — not a mock of it. No live Postgres required, which
    keeps this in the default unit shard.
    """

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        super().__init__("postgresql://fake/test")
        self._rows = rows
        self.written: dict[str, dict[str, Any]] = {}

    async def fetchall(self, sql: str, params: tuple = ()):  # type: ignore[override]
        return list(self._rows)

    async def execute(self, sql: str, params: tuple = ()) -> None:  # type: ignore[override]
        status, file_tasks_json, _updated_at = params[:3]
        task_id = params[-1]
        self.written[task_id] = {"status": status, "file_tasks_json": file_tasks_json}

    async def commit(self) -> None:  # type: ignore[override]
        return None


def _sqlite_store(tmp_path) -> SqliteKnowledgeMetadata:
    return SqliteKnowledgeMetadata(tmp_path / "metadata.db")


async def _stale_sqlite_row(store: SqliteKnowledgeMetadata, task_id: str, raw_json: str) -> None:
    """Insert a running task whose file_tasks_json is exactly ``raw_json``.

    Written through ``execute`` so we can plant payloads that ``create_task``
    would never produce (corrupt JSON, non-object values).
    """
    await store.create_task(task_id, "kb_agent_test", 1, {})
    await store.execute(
        "UPDATE tasks SET status='running', file_tasks_json=? WHERE task_id=?",
        (raw_json, task_id),
    )
    await store.commit()


# --- the shared helper -------------------------------------------------------


@pytest.mark.unit
def test_normalize_file_tasks_accepts_every_shape_on_disk():
    assert normalize_file_tasks(json.dumps({"a.md": PROGRESS_ENTRY})) == {"a.md": PROGRESS_ENTRY}
    # Already-deserialized input passes through (get_task hands us a dict).
    assert normalize_file_tasks({"a.md": PROGRESS_ENTRY}) == {"a.md": PROGRESS_ENTRY}
    # Everything that is not an object becomes one, so the caller's
    # re-serialization can never persist a non-mapping back into the column.
    for bad in ("not json", "", "null", "[1, 2]", '"a string"', "17", None, [1, 2], 17):
        assert normalize_file_tasks(bad) == {}, bad


@pytest.mark.unit
def test_mark_file_tasks_interrupted_defaults_missing_status_to_recoverable():
    """A status-less entry is mid-ingest, so it must be re-marked failed."""
    out = mark_file_tasks_interrupted({"a.md": dict(PROGRESS_ENTRY)})
    assert out["a.md"]["status"] == "failed"
    assert out["a.md"]["error"] == INTERRUPTED_ERROR
    # The progress fields survive — they are the audit trail of how far it got.
    assert out["a.md"]["stage"] == "parsed"
    assert out["a.md"]["progress"] == {"done": 182, "total": 182}


@pytest.mark.unit
def test_mark_file_tasks_interrupted_leaves_terminal_entries_alone():
    out = mark_file_tasks_interrupted(
        {
            "done.md": {"filename": "done.md", "status": "indexed"},
            "gone.md": {"filename": "gone.md", "status": "superseded"},
            "live.md": {"filename": "live.md", "status": "processing"},
            "junk.md": "not a dict",
        }
    )
    assert out["done.md"]["status"] == "indexed"
    assert out["gone.md"]["status"] == "superseded"
    assert out["live.md"]["status"] == "failed"
    # Non-dict entries are skipped, not dropped — we do not silently discard
    # rows we failed to understand.
    assert out["junk.md"] == "not a dict"


@pytest.mark.unit
def test_mark_file_tasks_interrupted_preserves_a_specific_error():
    """The generic restart message must not clobber a real diagnosis."""
    out = mark_file_tasks_interrupted(
        {"a.md": {"filename": "a.md", "status": "processing", "error": "embedder OOM"}}
    )
    assert out["a.md"]["error"] == "embedder OOM"


# --- Postgres (the backend that crashed) -------------------------------------


@pytest.mark.unit
async def test_postgres_recover_tolerates_missing_file_task_status():
    """The #683 reproduction: warmup must not raise KeyError."""
    store = _RecoveryFakePostgres(
        [
            {
                "task_id": "task_f0f171881f6a",
                "file_tasks_json": json.dumps({PROGRESS_ENTRY["filename"]: PROGRESS_ENTRY}),
            }
        ]
    )

    assert await store.recover_stale_tasks() == 1

    written = store.written["task_f0f171881f6a"]
    assert written["status"] == "failed"
    entry = json.loads(written["file_tasks_json"])[PROGRESS_ENTRY["filename"]]
    assert entry["status"] == "failed"
    assert entry["error"] == INTERRUPTED_ERROR


@pytest.mark.unit
async def test_postgres_recover_always_persists_an_object():
    """Corrupt or non-object payloads must not be written back as-is.

    The guard added in #684 skipped the *loop* for non-dicts but still
    re-serialized the original value, so a stale row could persist ``[1,2]``
    into a column every other reader treats as a mapping.
    """
    rows = [
        {"task_id": "t_corrupt", "file_tasks_json": "not json at all"},
        {"task_id": "t_null", "file_tasks_json": "null"},
        {"task_id": "t_list", "file_tasks_json": "[1, 2]"},
        {"task_id": "t_scalar", "file_tasks_json": "17"},
    ]
    store = _RecoveryFakePostgres(rows)

    assert await store.recover_stale_tasks() == len(rows)

    for task_id in ("t_corrupt", "t_null", "t_list", "t_scalar"):
        written = store.written[task_id]
        assert written["status"] == "failed"
        assert json.loads(written["file_tasks_json"]) == {}, task_id


# --- SQLite (end-to-end against a real database) ------------------------------


@pytest.mark.unit
async def test_sqlite_recover_tolerates_missing_file_task_status(tmp_path):
    store = _sqlite_store(tmp_path)
    await _stale_sqlite_row(store, "task_sqlite_1", json.dumps({PROGRESS_ENTRY["filename"]: PROGRESS_ENTRY}))

    assert await store.recover_stale_tasks() == 1

    task = await store.get_task("task_sqlite_1")
    assert task is not None
    assert task["status"] == "failed"
    entry = task["file_tasks"][PROGRESS_ENTRY["filename"]]
    assert entry["status"] == "failed"
    assert entry["error"] == INTERRUPTED_ERROR
    await store.close()


@pytest.mark.unit
async def test_sqlite_recover_always_persists_an_object(tmp_path):
    store = _sqlite_store(tmp_path)
    await _stale_sqlite_row(store, "task_sqlite_bad", "[1, 2]")

    assert await store.recover_stale_tasks() == 1

    # get_task json.loads the column unguarded, so this read is itself the
    # assertion that recovery left a mapping behind.
    task = await store.get_task("task_sqlite_bad")
    assert task is not None
    assert task["file_tasks"] == {}
    await store.close()


# --- the two backends must not drift again ------------------------------------


@pytest.mark.unit
async def test_recover_stale_tasks_parity_between_sqlite_and_postgres(tmp_path):
    """Same input, same output from both stores.

    #683 existed because the SQLite guard landed in 92618226 and was never
    back-ported to Postgres. This test fails if either backend stops routing
    through the shared helper.
    """
    payload = {
        "no-status.md": dict(PROGRESS_ENTRY),
        "processing.md": {"filename": "processing.md", "status": "processing"},
        "indexed.md": {"filename": "indexed.md", "status": "indexed"},
        "with-error.md": {"filename": "with-error.md", "status": "pending", "error": "disk full"},
    }
    raw = json.dumps(payload)

    pg = _RecoveryFakePostgres([{"task_id": "t_parity", "file_tasks_json": raw}])
    await pg.recover_stale_tasks()
    pg_out = json.loads(pg.written["t_parity"]["file_tasks_json"])

    lite = _sqlite_store(tmp_path)
    await _stale_sqlite_row(lite, "t_parity", raw)
    await lite.recover_stale_tasks()
    lite_task = await lite.get_task("t_parity")
    assert lite_task is not None
    lite_out = lite_task["file_tasks"]
    await lite.close()

    assert pg_out == lite_out
    # And both agree with the helper that owns the rules.
    assert pg_out == mark_file_tasks_interrupted(normalize_file_tasks(raw))
