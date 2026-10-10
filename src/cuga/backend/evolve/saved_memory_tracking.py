"""Confirm background fact saves and attach them when conversation history is read."""

import json

from loguru import logger

from cuga.backend.evolve.memory_store import _scope, _store


async def _schema() -> None:
    await _store().execute(
        "CREATE TABLE IF NOT EXISTS evolve_memory_saved ("
        "tenant_id TEXT NOT NULL, instance_id TEXT NOT NULL, turn_id TEXT NOT NULL, "
        "agent_id TEXT NOT NULL, user_id TEXT NOT NULL, entity_id TEXT NOT NULL, "
        "PRIMARY KEY (tenant_id, instance_id, turn_id, agent_id, user_id, entity_id))"
    )
    await _store().commit()


async def record_saved_memories(result, *, turn_id: str, agent_id: str, user_id: str) -> None:
    """Only confirmed ADD/UPDATE results count; runs inside the existing background save."""
    if not (turn_id and agent_id and user_id) or not isinstance(result, dict) or result.get("error"):
        return
    ids = {
        str(item["id"])
        for item in (result.get("updates") or [])
        if isinstance(item, dict) and item.get("event") in {"ADD", "UPDATE"} and item.get("id") is not None
    }
    if not ids:
        return
    try:
        await _schema()
        for entity_id in sorted(ids):
            await _store().execute(
                "INSERT INTO evolve_memory_saved VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                (*_scope(), turn_id, agent_id, user_id, entity_id),
            )
        await _store().commit()
    except Exception:
        logger.warning("Memory was saved but its saved-memory tracking could not be recorded")


async def enrich_saved_memories(events: list[dict], *, agent_id: str, user_id: str) -> list[dict]:
    """Enrich a copy of authorized history; never rewrite the stream or its stored event list."""
    answers = []
    for index, event in enumerate(events):
        if event.get("event_name") not in {"Answer", "FinalAnswer"}:
            continue
        try:
            payload = json.loads(event.get("event_data", ""))
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        turn_id = payload.get("memory_turn_id") or (payload.get("memory_usage") or {}).get("turn_id")
        if isinstance(turn_id, str) and turn_id:
            answers.append((index, turn_id, payload))
    if not answers:
        return events
    await _schema()
    saved: dict[str, list[str]] = {}
    turns = list(dict.fromkeys(turn for _, turn, _ in answers))
    # Bound placeholders for SQLite as well as Postgres.
    for offset in range(0, len(turns), 500):
        batch = turns[offset : offset + 500]
        rows = await _store().fetchall(
            "SELECT turn_id, entity_id FROM evolve_memory_saved "
            "WHERE tenant_id = ? AND instance_id = ? AND agent_id = ? AND user_id = ? "
            f"AND turn_id IN ({','.join('?' for _ in batch)}) ORDER BY entity_id",
            (*_scope(), agent_id, user_id, *batch),
        )
        for row in rows:
            saved.setdefault(row["turn_id"], []).append(str(row["entity_id"]))
    enriched = list(events)
    for index, turn_id, payload in answers:
        ids = saved.get(turn_id)
        if ids:
            payload["memory_saved"] = {"count": len(ids), "entity_ids": ids}
            enriched[index] = {**events[index], "event_data": json.dumps(payload)}
    return enriched
