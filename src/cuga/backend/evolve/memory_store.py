"""Durable, service-scoped metadata for the experimental Evolve memory UI."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from cuga.backend.storage import get_storage
from cuga.config import get_service_instance_id, get_tenant_id


def _store():
    return get_storage().get_relational_store("evolve_memory")


def _scope() -> tuple[str, str]:
    return get_tenant_id(), get_service_instance_id()


async def _ensure_schema() -> None:
    await _store().execute(
        "CREATE TABLE IF NOT EXISTS evolve_memory_usage ("
        "tenant_id TEXT NOT NULL, instance_id TEXT NOT NULL, usage_id TEXT NOT NULL, "
        "turn_id TEXT NOT NULL, agent_id TEXT NOT NULL, user_id TEXT NOT NULL, "
        "entity_id TEXT NOT NULL, thread_id TEXT NOT NULL, conversation_label TEXT NOT NULL, "
        "purpose TEXT NOT NULL, used_at TEXT NOT NULL, "
        "PRIMARY KEY (tenant_id, instance_id, usage_id))"
    )
    await _store().commit()


async def record_memory_usage(
    *,
    turn_id: str,
    agent_id: str,
    user_id: str,
    entity_ids: list[str],
    thread_id: str,
    conversation_label: str,
    purpose: str = "prompt_context",
    used_at: str | None = None,
) -> dict[str, Any]:
    """Record one idempotent usage event per entity and graph turn."""
    await _ensure_schema()
    unique_ids = [entity_id for entity_id in dict.fromkeys(map(str, entity_ids)) if entity_id]
    moment = used_at or dt.datetime.now(dt.UTC).isoformat()
    tenant_id, instance_id = _scope()
    store = _store()
    for entity_id in unique_ids:
        usage_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{turn_id}:memory:{entity_id}"))
        await store.execute(
            "INSERT INTO evolve_memory_usage VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (tenant_id, instance_id, usage_id) DO NOTHING",
            (
                tenant_id,
                instance_id,
                usage_id,
                turn_id,
                agent_id,
                user_id,
                entity_id,
                thread_id,
                conversation_label[:120],
                purpose,
                moment,
            ),
        )
    await store.commit()
    return {
        "turn_id": turn_id,
        "count": len(unique_ids),
        "entity_ids": unique_ids,
        "used_at": moment,
    }


async def get_turn_memory_usage(
    *,
    turn_id: str,
    agent_id: str,
    user_id: str,
) -> dict[str, Any]:
    """Return the authenticated turn's prompt-context attribution."""
    await _ensure_schema()
    tenant_id, instance_id = _scope()
    rows = await _store().fetchall(
        "SELECT entity_id, used_at FROM evolve_memory_usage "
        "WHERE tenant_id = ? AND instance_id = ? AND turn_id = ? "
        "AND agent_id = ? AND user_id = ? ORDER BY entity_id",
        (tenant_id, instance_id, turn_id, agent_id, user_id),
    )
    entity_ids = [str(row["entity_id"]) for row in rows]
    return {
        "turn_id": turn_id,
        "count": len(entity_ids),
        "entity_ids": entity_ids,
        "used_at": rows[-1]["used_at"] if rows else None,
    }


async def get_available_conversation_thread_ids(*, agent_id: str, user_id: str) -> set[str]:
    """Return conversation IDs visible in the current service and user scope."""
    from cuga.backend.server.conversation_history import get_conversation_db

    conversations = await get_conversation_db().get_all_threads_for_agent(agent_id, user_id)
    return {str(item["thread_id"]) for item in conversations if item.get("thread_id")}


async def get_memory_usage_summaries(
    *,
    agent_id: str,
    entity_ids: list[str],
    user_id: str | None = None,
    recent_limit: int = 3,
    include_recent: bool = True,
    available_thread_ids: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Aggregate usage counts and recent linked conversations for inventory rows."""
    await _ensure_schema()
    wanted = set(map(str, entity_ids))
    if not wanted:
        return {}
    tenant_id, instance_id = _scope()
    store = _store()
    placeholders = ", ".join("?" for _ in wanted)
    params: tuple[Any, ...] = (tenant_id, instance_id, agent_id, *sorted(wanted))
    user_clause = ""
    if user_id is not None:
        user_clause = " AND user_id = ?"
        params += (user_id,)
    rows = await store.fetchall(
        "SELECT entity_id, thread_id, conversation_label, used_at "
        "FROM evolve_memory_usage WHERE tenant_id = ? AND instance_id = ? AND agent_id = ? "
        f"AND entity_id IN ({placeholders})"
        f"{user_clause} ORDER BY used_at DESC",
        params,
    )

    available_threads: set[str] = set()
    if include_recent and user_id is not None:
        available_threads = (
            available_thread_ids
            if available_thread_ids is not None
            else await get_available_conversation_thread_ids(agent_id=agent_id, user_id=user_id)
        )

    summaries: dict[str, dict[str, Any]] = {}
    for row in rows:
        entity_id = str(row["entity_id"])
        if entity_id not in wanted:
            continue
        summary = summaries.setdefault(
            entity_id,
            {"count": 0, "last_used_at": None, "recent": []},
        )
        summary["count"] += 1
        if summary["last_used_at"] is None:
            summary["last_used_at"] = row["used_at"]
        if (
            include_recent
            and str(row["thread_id"]) in available_threads
            and len(summary["recent"]) < recent_limit
        ):
            summary["recent"].append(
                {
                    "thread_id": row["thread_id"],
                    "conversation_label": row["conversation_label"],
                    "used_at": row["used_at"],
                }
            )
    return summaries
