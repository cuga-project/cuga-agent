"""Durable audit history for manually triggered Evolve retention runs."""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from cuga.backend.storage import get_storage
from cuga.config import get_service_instance_id, get_tenant_id


def _store():
    return get_storage().get_relational_store("evolve_memory")


def _scope() -> tuple[str, str]:
    return get_tenant_id(), get_service_instance_id()


async def _ensure_schema() -> None:
    await _store().execute(
        "CREATE TABLE IF NOT EXISTS evolve_retention_runs ("
        "tenant_id TEXT NOT NULL, instance_id TEXT NOT NULL, run_id TEXT NOT NULL, "
        "agent_id TEXT NOT NULL, actor_id TEXT NOT NULL, dry_run INTEGER NOT NULL, "
        "status TEXT NOT NULL, report_json TEXT NOT NULL, created_at TEXT NOT NULL, "
        "PRIMARY KEY (tenant_id, instance_id, run_id))"
    )
    await _store().execute("DELETE FROM evolve_retention_runs WHERE dry_run <> 0")
    await _store().commit()


async def save_retention_run(
    *,
    run_id: str,
    agent_id: str,
    actor_id: str,
    report: dict[str, Any],
) -> None:
    await _ensure_schema()
    tenant_id, instance_id = _scope()
    status = "failed" if report.get("errors") else "completed"
    await _store().execute(
        "INSERT INTO evolve_retention_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            tenant_id,
            instance_id,
            run_id,
            agent_id,
            actor_id,
            0,
            status,
            json.dumps(report),
            dt.datetime.now(dt.UTC).isoformat(),
        ),
    )
    await _store().commit()


async def list_retention_runs(*, agent_id: str, limit: int = 50) -> list[dict[str, Any]]:
    await _ensure_schema()
    tenant_id, instance_id = _scope()
    rows = await _store().fetchall(
        "SELECT run_id, actor_id, status, report_json, created_at "
        "FROM evolve_retention_runs WHERE tenant_id = ? AND instance_id = ? AND agent_id = ? "
        "ORDER BY created_at DESC LIMIT ?",
        (tenant_id, instance_id, agent_id, limit),
    )
    return [
        {
            "run_id": row["run_id"],
            "actor_id": row["actor_id"],
            "status": row["status"],
            "created_at": row["created_at"],
            "report": json.loads(row["report_json"]),
        }
        for row in rows
    ]
