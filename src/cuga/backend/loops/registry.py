"""SQLite-backed registry for loops + runs.

Uses CUGA's storage facade so the DB path follows existing conventions
(`CUGA_DBS_DIR` / `settings.storage.local_db_path`). Tables are namespaced
with a `cuga_loops_` prefix so they coexist with other CUGA tables in the
shared `cuga.db`.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import List, Optional

from cuga.backend.loops.models import Loop, LoopRun, LoopStatus, RunOutcome, TriggerKind
from cuga.backend.storage.facade import get_storage

logger = logging.getLogger(__name__)

_LOOPS_TABLE = "cuga_loops"
_RUNS_TABLE = "cuga_loop_runs"

_DDL_LOOPS = f"""
CREATE TABLE IF NOT EXISTS {_LOOPS_TABLE} (
    id TEXT PRIMARY KEY,
    app_name TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    prompt TEXT NOT NULL,
    trigger_kind TEXT NOT NULL,
    trigger_spec TEXT NOT NULL,
    cron TEXT,
    interval_seconds INTEGER,
    status TEXT NOT NULL DEFAULT 'active',
    created_at REAL NOT NULL,
    expires_at REAL,
    last_fire_at REAL,
    next_fire_at REAL,
    fire_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    metadata_json TEXT
)
"""

_DDL_RUNS = f"""
CREATE TABLE IF NOT EXISTS {_RUNS_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    loop_id TEXT NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL,
    outcome TEXT NOT NULL,
    summary TEXT,
    error TEXT,
    FOREIGN KEY (loop_id) REFERENCES {_LOOPS_TABLE}(id)
)
"""

_DDL_INDEXES = [
    f"CREATE INDEX IF NOT EXISTS idx_{_LOOPS_TABLE}_status ON {_LOOPS_TABLE}(status)",
    f"CREATE INDEX IF NOT EXISTS idx_{_LOOPS_TABLE}_app ON {_LOOPS_TABLE}(app_name)",
    f"CREATE INDEX IF NOT EXISTS idx_{_RUNS_TABLE}_loop ON {_RUNS_TABLE}(loop_id, started_at DESC)",
]


def _row_to_loop(row: dict) -> Loop:
    return Loop(
        id=row["id"],
        app_name=row["app_name"],
        agent_name=row["agent_name"],
        thread_id=row["thread_id"],
        prompt=row["prompt"],
        trigger_kind=TriggerKind(row["trigger_kind"]),
        trigger_spec=row["trigger_spec"],
        cron=row.get("cron"),
        interval_seconds=row.get("interval_seconds"),
        status=LoopStatus(row["status"]),
        created_at=row["created_at"],
        expires_at=row.get("expires_at"),
        last_fire_at=row.get("last_fire_at"),
        next_fire_at=row.get("next_fire_at"),
        fire_count=row.get("fire_count") or 0,
        error_count=row.get("error_count") or 0,
        last_error=row.get("last_error"),
        metadata_json=row.get("metadata_json"),
    )


def _row_to_run(row: dict) -> LoopRun:
    return LoopRun(
        id=row.get("id"),
        loop_id=row["loop_id"],
        started_at=row["started_at"],
        finished_at=row.get("finished_at"),
        outcome=RunOutcome(row["outcome"]),
        summary=row.get("summary"),
        error=row.get("error"),
    )


class LoopsRegistry:
    """SQLite CRUD for loops + runs. All methods are async."""

    def __init__(self):
        self._store = get_storage().get_relational_store("loops")
        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        await self._store.execute(_DDL_LOOPS)
        await self._store.execute(_DDL_RUNS)
        for ddl in _DDL_INDEXES:
            await self._store.execute(ddl)
        await self._store.commit()
        self._initialized = True
        logger.debug("loops registry initialized")

    async def create_loop(
        self,
        *,
        app_name: str,
        agent_name: str,
        thread_id: str,
        prompt: str,
        trigger_kind: TriggerKind,
        trigger_spec: str,
        cron: Optional[str] = None,
        interval_seconds: Optional[int] = None,
        expires_at: Optional[float] = None,
        next_fire_at: Optional[float] = None,
        metadata: Optional[dict] = None,
    ) -> Loop:
        await self.initialize()
        loop = Loop(
            id=f"loop_{uuid.uuid4().hex[:12]}",
            app_name=app_name,
            agent_name=agent_name,
            thread_id=thread_id,
            prompt=prompt,
            trigger_kind=trigger_kind,
            trigger_spec=trigger_spec,
            cron=cron,
            interval_seconds=interval_seconds,
            expires_at=expires_at,
            next_fire_at=next_fire_at,
            metadata_json=json.dumps(metadata) if metadata else None,
        )
        await self._store.execute(
            f"""INSERT INTO {_LOOPS_TABLE}
                (id, app_name, agent_name, thread_id, prompt, trigger_kind, trigger_spec,
                 cron, interval_seconds, status, created_at, expires_at, next_fire_at,
                 fire_count, error_count, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?)""",
            (
                loop.id, loop.app_name, loop.agent_name, loop.thread_id, loop.prompt,
                loop.trigger_kind.value, loop.trigger_spec, loop.cron, loop.interval_seconds,
                loop.status.value, loop.created_at, loop.expires_at, loop.next_fire_at,
                loop.metadata_json,
            ),
        )
        await self._store.commit()
        return loop

    async def get(self, loop_id: str) -> Optional[Loop]:
        await self.initialize()
        row = await self._store.fetchone(f"SELECT * FROM {_LOOPS_TABLE} WHERE id = ?", (loop_id,))
        return _row_to_loop(row) if row else None

    async def list_all(
        self,
        *,
        app_name: Optional[str] = None,
        thread_id: Optional[str] = None,
        status: Optional[LoopStatus] = None,
    ) -> List[Loop]:
        await self.initialize()
        clauses = []
        params: list = []
        if app_name:
            clauses.append("app_name = ?")
            params.append(app_name)
        if thread_id:
            clauses.append("thread_id = ?")
            params.append(thread_id)
        if status:
            clauses.append("status = ?")
            params.append(status.value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = await self._store.fetchall(
            f"SELECT * FROM {_LOOPS_TABLE}{where} ORDER BY created_at DESC", tuple(params)
        )
        return [_row_to_loop(r) for r in rows]

    async def update_status(self, loop_id: str, status: LoopStatus) -> None:
        await self.initialize()
        await self._store.execute(
            f"UPDATE {_LOOPS_TABLE} SET status = ? WHERE id = ?", (status.value, loop_id)
        )
        await self._store.commit()

    async def update_next_fire(self, loop_id: str, next_fire_at: Optional[float]) -> None:
        await self.initialize()
        await self._store.execute(
            f"UPDATE {_LOOPS_TABLE} SET next_fire_at = ? WHERE id = ?",
            (next_fire_at, loop_id),
        )
        await self._store.commit()

    async def record_fire(
        self, loop_id: str, *, ok: bool, summary: Optional[str], error: Optional[str]
    ) -> None:
        await self.initialize()
        now = time.time()
        if ok:
            await self._store.execute(
                f"""UPDATE {_LOOPS_TABLE}
                    SET last_fire_at = ?, fire_count = fire_count + 1, last_error = NULL
                    WHERE id = ?""",
                (now, loop_id),
            )
        else:
            await self._store.execute(
                f"""UPDATE {_LOOPS_TABLE}
                    SET last_fire_at = ?, fire_count = fire_count + 1,
                        error_count = error_count + 1, last_error = ?
                    WHERE id = ?""",
                (now, (error or "")[:500], loop_id),
            )
        await self._store.commit()

    async def insert_run(self, run: LoopRun) -> None:
        await self.initialize()
        await self._store.execute(
            f"""INSERT INTO {_RUNS_TABLE} (loop_id, started_at, finished_at, outcome, summary, error)
                VALUES (?, ?, ?, ?, ?, ?)""",
            (
                run.loop_id, run.started_at, run.finished_at, run.outcome.value,
                (run.summary or "")[:2000] if run.summary else None,
                (run.error or "")[:1000] if run.error else None,
            ),
        )
        await self._store.commit()

    async def list_runs(self, loop_id: str, limit: int = 25) -> List[LoopRun]:
        await self.initialize()
        rows = await self._store.fetchall(
            f"SELECT * FROM {_RUNS_TABLE} WHERE loop_id = ? ORDER BY started_at DESC LIMIT ?",
            (loop_id, limit),
        )
        return [_row_to_run(r) for r in rows]

    async def delete(self, loop_id: str) -> None:
        await self.initialize()
        await self._store.execute(f"DELETE FROM {_RUNS_TABLE} WHERE loop_id = ?", (loop_id,))
        await self._store.execute(f"DELETE FROM {_LOOPS_TABLE} WHERE id = ?", (loop_id,))
        await self._store.commit()

    async def list_active_for_revival(self) -> List[Loop]:
        """Loops that should be re-armed by a starting scheduler."""
        return await self.list_all(status=LoopStatus.ACTIVE)
