import pytest

from cuga.backend.evolve import retention_store
from cuga.backend.storage.facade import get_storage

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_retention_history_is_service_instance_and_agent_scoped(tmp_path, monkeypatch):
    import cuga.backend.storage.facade as storage_facade

    db_path = str(tmp_path / "retention.db")
    monkeypatch.setattr(storage_facade, "_local_db_path", lambda: db_path)
    get_storage().invalidate_relational_stores()
    monkeypatch.setattr(retention_store, "_scope", lambda: ("tenant-a", "instance-a"))
    try:
        await retention_store._ensure_schema()
        store = retention_store._store()
        await store.execute(
            "INSERT INTO evolve_retention_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "tenant-a",
                "instance-a",
                "preview-a",
                "agent-a",
                "admin-a",
                1,
                "completed",
                '{"run_id":"preview-a"}',
                "2026-09-03T00:00:00+00:00",
            ),
        )
        await store.commit()
        await retention_store.save_retention_run(
            run_id="run-a",
            agent_id="agent-a",
            actor_id="admin-a",
            report={"run_id": "run-a", "deleted": [], "errors": []},
        )
        same_scope = await retention_store.list_retention_runs(agent_id="agent-a")
        other_agent = await retention_store.list_retention_runs(agent_id="agent-b")
        monkeypatch.setattr(retention_store, "_scope", lambda: ("tenant-a", "instance-b"))
        other_instance = await retention_store.list_retention_runs(agent_id="agent-a")
    finally:
        get_storage().invalidate_relational_stores()

    assert [item["run_id"] for item in same_scope] == ["run-a"]
    assert (
        await store.fetchone(
            "SELECT run_id FROM evolve_retention_runs WHERE run_id = ?",
            ("preview-a",),
        )
        is None
    )
    assert other_agent == []
    assert other_instance == []
