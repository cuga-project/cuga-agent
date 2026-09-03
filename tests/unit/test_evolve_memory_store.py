import asyncio

import pytest

from cuga.backend.evolve import memory_store
from cuga.backend.storage.facade import get_storage

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_usage_is_idempotent_and_service_scoped(tmp_path, monkeypatch):
    import cuga.backend.storage.facade as storage_facade

    db_path = str(tmp_path / "memory.db")
    monkeypatch.setattr(storage_facade, "_local_db_path", lambda: db_path)
    get_storage().invalidate_relational_stores()
    monkeypatch.setattr(memory_store, "_scope", lambda: ("tenant-a", "instance-a"))
    try:
        payload = {
            "turn_id": "turn-a",
            "agent_id": "agent-a",
            "user_id": "user-a",
            "entity_ids": ["entity-a", "entity-a"],
            "thread_id": "thread-a",
            "conversation_label": "A conversation",
            "used_at": "2026-08-01T00:00:00+00:00",
        }
        await asyncio.gather(
            memory_store.record_memory_usage(**payload),
            memory_store.record_memory_usage(**payload),
        )

        same_scope = await memory_store.get_turn_memory_usage(
            turn_id="turn-a", agent_id="agent-a", user_id="user-a"
        )
        monkeypatch.setattr(memory_store, "_scope", lambda: ("tenant-a", "instance-b"))
        other_instance = await memory_store.get_turn_memory_usage(
            turn_id="turn-a", agent_id="agent-a", user_id="user-a"
        )
    finally:
        get_storage().invalidate_relational_stores()

    assert same_scope["entity_ids"] == ["entity-a"]
    assert same_scope["count"] == 1
    assert other_instance["entity_ids"] == []
