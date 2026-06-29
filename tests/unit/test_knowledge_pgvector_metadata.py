"""Verify the upload-progress code paths against the Postgres metadata store.

Confirms that the new ``wait=false`` + background-task code in ``routes.py``
produces well-formed writes against ``PostgresKnowledgeMetadata`` — without
requiring a live Postgres instance.

What we explicitly check:
1. ``_TASK_UPDATE_COLS`` allowlist covers every kwarg our new code passes
   (so no field is silently dropped by the Postgres store).
2. ``update_task`` with the failure-path kwargs ((status, processed_files,
   failed_files, file_tasks) emitted from ``_cleanup_after_ingest`` in
   routes.py) produces a single UPDATE with all expected columns.
3. ``get_task`` deserializes ``file_tasks_json`` back into a dict whose
   shape matches what ``_weighted_pct_for_file_task`` expects.
4. SQL is emitted with ``?`` placeholders (which ``ProdRelationalStore``
   translates to ``$N``); we don't smuggle a Postgres-incompatible literal.
"""

from __future__ import annotations

from typing import Any

import pytest

from cuga.backend.knowledge.metadata.postgres_store import (
    PostgresKnowledgeMetadata,
    _TASK_UPDATE_COLS,
)
from cuga.backend.knowledge.routes import _enrich_task


class _FakePostgresMetadata(PostgresKnowledgeMetadata):
    """Override the asyncpg I/O with an in-memory recorder.

    Keeps the parent's SQL-generation logic intact so the test exercises
    the real ``update_task`` / ``get_task`` code paths, not a mock of them.
    """

    def __init__(self) -> None:
        super().__init__("postgresql://fake/test")
        self.executed: list[tuple[str, tuple]] = []
        self.fetched_one: list[tuple[str, tuple]] = []
        self._rows_by_task_id: dict[str, dict[str, Any]] = {}

    async def execute(self, sql: str, params: tuple = ()) -> None:  # type: ignore[override]
        self.executed.append((sql, tuple(params)))
        # Minimal interpreter so create_task / update_task affect get_task.
        # We don't translate ``?`` here — we just match the engine's own
        # call patterns and update our in-memory row store.
        if "INSERT INTO" in sql and "_tasks" in sql:
            (
                task_id,
                collection,
                total_files,
                file_tasks_json,
                created_at,
                updated_at,
            ) = params
            self._rows_by_task_id[task_id] = {
                "task_id": task_id,
                "collection": collection,
                "status": "pending",
                "total_files": total_files,
                "processed_files": 0,
                "successful_files": 0,
                "failed_files": 0,
                "file_tasks_json": file_tasks_json,
                "created_at": created_at,
                "updated_at": updated_at,
            }
        elif "UPDATE" in sql and "_tasks" in sql:
            # SET <col> = ?, ... WHERE task_id = ?
            set_section = sql.split("SET", 1)[1].split("WHERE", 1)[0]
            cols = [seg.split("=")[0].strip() for seg in set_section.strip().rstrip(",").split(",")]
            task_id = params[-1]
            if task_id in self._rows_by_task_id:
                for col, val in zip(cols, params[:-1]):
                    self._rows_by_task_id[task_id][col] = val

    async def fetchone(self, sql: str, params: tuple = ()):  # type: ignore[override]
        self.fetched_one.append((sql, tuple(params)))
        if "_tasks" in sql and "WHERE task_id" in sql:
            return self._rows_by_task_id.get(params[0])
        return None

    async def fetchall(self, sql: str, params: tuple = ()):  # type: ignore[override]
        return []

    async def commit(self) -> None:  # type: ignore[override]
        return None

    async def ensure_ready(self) -> None:  # type: ignore[override]
        # Skip schema bootstrap — we're not running real SQL.
        self._schema_initialized = True


def test_failure_path_kwargs_are_fully_covered_by_allowlist():
    """``_cleanup_after_ingest`` in routes.py writes these on background
    exceptions. Every one must be in ``_TASK_UPDATE_COLS`` or the Postgres
    store silently drops it, leaving the task stuck in pending."""
    failure_kwargs_pre_transform = {
        "status",
        "processed_files",
        "failed_files",
        "file_tasks",  # transformed to file_tasks_json before allowlist check
    }
    # ``update_task`` itself adds ``updated_at`` and renames file_tasks.
    # The allowlist must contain the post-transform names.
    expected_post_transform = (failure_kwargs_pre_transform - {"file_tasks"}) | {
        "file_tasks_json",
        "updated_at",
    }
    missing = expected_post_transform - _TASK_UPDATE_COLS
    assert not missing, (
        f"Postgres _TASK_UPDATE_COLS missing fields written by failure path: {missing}. "
        f"Postgres store would silently drop these and leave the task stuck."
    )


def test_engine_completion_kwargs_covered_by_allowlist():
    """Engine writes status=completed + counts + file_tasks at the end of
    a successful ingest. Same allowlist check, different kwargs."""
    completion_post_transform = {
        "status",
        "processed_files",
        "successful_files",
        "file_tasks_json",
        "updated_at",
    }
    missing = completion_post_transform - _TASK_UPDATE_COLS
    assert not missing, f"Postgres _TASK_UPDATE_COLS missing completion fields: {missing}"


@pytest.mark.asyncio
async def test_failure_path_writes_well_formed_sql_against_postgres_store():
    """End-to-end: simulate the exact ``update_task`` call our background
    task emits on failure, then read back via ``get_task`` and confirm the
    shape matches what ``_enrich_task`` consumes."""
    store = _FakePostgresMetadata()
    await store.ensure_ready()

    # Step 1: create the task entry (mirrors engine._create_task_entry).
    await store.create_task(
        task_id="task_pg_1",
        collection="kb_agent_test",
        total_files=1,
        file_tasks={"report.pdf": {"filename": "report.pdf", "status": "pending"}},
    )

    # Step 2: simulate _cleanup_after_ingest writing failed-on-exception.
    await store.update_task(
        "task_pg_1",
        status="failed",
        processed_files=1,
        failed_files=1,
        file_tasks={
            "report.pdf": {
                "filename": "report.pdf",
                "status": "failed",
                "error": "vector store init refused",
            }
        },
    )

    # Step 3: read back the way the route does.
    task = await store.get_task("task_pg_1")
    assert task is not None
    assert task["status"] == "failed"
    assert task["processed_files"] == 1
    assert task["failed_files"] == 1
    # file_tasks_json deserialized into a dict
    assert task["file_tasks"]["report.pdf"]["status"] == "failed"
    assert task["file_tasks"]["report.pdf"]["error"] == "vector store init refused"

    # Step 4: route enrichment must classify this as a failed ui_phase
    # with weighted_pct=0 — what the frontend's poll loop branches on.
    enriched = _enrich_task(dict(task))
    assert enriched["ui_phase"] == "failed"
    assert enriched["weighted_pct"] == 0.0

    # Step 5: SQL placeholder shape. ProdRelationalStore translates ?→$N;
    # our store must emit ``?`` (not ``%s``, not ``$1``) so the translator
    # has something to match. One non-INSERT execute should be the UPDATE.
    update_sqls = [s for s, _ in store.executed if "UPDATE" in s]
    assert update_sqls, "expected at least one UPDATE statement"
    for sql in update_sqls:
        assert "?" in sql, f"UPDATE missing ``?`` placeholders: {sql}"
        assert "$1" not in sql, "Postgres-specific placeholders leaked: " + sql
        assert "%s" not in sql, "Mysql/psycopg-style placeholders leaked: " + sql


@pytest.mark.asyncio
async def test_progress_emit_replaces_file_tasks_json_atomically():
    """Each ``_emit_progress`` call from the engine overwrites the whole
    file_tasks_json. Verify a stage='embed' progress write doesn't corrupt
    or duplicate the row, and that get_task returns the latest state."""
    store = _FakePostgresMetadata()
    await store.ensure_ready()
    await store.create_task(
        task_id="task_pg_2",
        collection="kb_agent_test",
        total_files=1,
        file_tasks={"x.pdf": {"filename": "x.pdf", "status": "processing"}},
    )

    # Three progress emits in sequence (parsed → embed → insert) mirroring
    # what engine._ingest_inner does.
    for stage, done, total in [
        ("parsed", 100, 100),
        ("embed", 250, 500),
        ("insert", 400, 500),
    ]:
        await store.update_task(
            "task_pg_2",
            file_tasks={
                "x.pdf": {
                    "filename": "x.pdf",
                    "stage": stage,
                    "progress": {"done": done, "total": total},
                }
            },
        )

    task = await store.get_task("task_pg_2")
    assert task is not None
    # The last emit wins (last-write replaces, no merge).
    assert task["file_tasks"]["x.pdf"]["stage"] == "insert"
    assert task["file_tasks"]["x.pdf"]["progress"] == {"done": 400, "total": 500}

    # Route enrichment must compute weighted_pct against this real shape.
    enriched = _enrich_task(dict(task))
    # parse(0.45) + embed(0.40) + insert(0.15)*(400/500) = 0.97
    assert abs(enriched["weighted_pct"] - 0.97) < 0.001
    assert enriched["ui_phase"] == "queued"  # status is still 'pending' since we only updated file_tasks


@pytest.mark.asyncio
async def test_no_kwarg_silently_dropped_during_progress_emit():
    """If the engine ever passes an extra kwarg the Postgres allowlist
    doesn't know about, the SQLite store would happily write it but the
    Postgres store drops it. This test guards against that drift."""
    store = _FakePostgresMetadata()
    await store.ensure_ready()
    await store.create_task("t3", "c", 1, {"f.pdf": {"filename": "f.pdf"}})

    # All kwargs the engine + routes actually pass:
    kwargs_emitted_by_codebase = {
        "status": "completed",
        "processed_files": 1,
        "successful_files": 1,
        "failed_files": 0,
        "total_files": 1,
        "file_tasks": {"f.pdf": {"filename": "f.pdf", "status": "indexed"}},
    }
    await store.update_task("t3", **kwargs_emitted_by_codebase)

    task = await store.get_task("t3")
    assert task is not None
    # Every field we passed must have landed.
    assert task["status"] == "completed"
    assert task["processed_files"] == 1
    assert task["successful_files"] == 1
    assert task["failed_files"] == 0
    assert task["total_files"] == 1
    assert task["file_tasks"]["f.pdf"]["status"] == "indexed"
