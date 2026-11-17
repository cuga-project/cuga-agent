"""Conversation and lease schemas are required before server startup succeeds."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from cuga.backend.server import main
from cuga.backend.server.conversation_history import ConversationHistoryDB
from cuga.backend.server.thread_lease import ThreadLease
from cuga.backend.storage.checkpoint import CheckpointerHandle
from cuga.backend.storage.relational.local import LocalRelationalStore

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("failure", [None, "conversation", "lease"])
@pytest.mark.parametrize("mode", ["memory", "durable"])
async def test_schemas_gate_startup_and_failure_closes_connections(monkeypatch, failure, mode):
    events = []

    async def conversation_schema():
        events.append("conversation")
        if failure == "conversation":
            raise RuntimeError("conversation DDL denied")

    async def lease_schema():
        events.append("lease")
        if failure == "lease":
            raise RuntimeError("lease DDL denied")

    handle = CheckpointerHandle(saver=MagicMock(), kind="memory", _close=AsyncMock())
    monkeypatch.setattr(
        main,
        "settings",
        SimpleNamespace(
            storage=SimpleNamespace(
                mode="local",
                checkpointer=mode,
                postgres_url="",
            )
        ),
    )
    monkeypatch.setattr(main.app_state, "checkpointer_handle", None)
    monkeypatch.setattr("cuga.backend.storage.checkpoint.create_checkpointer", AsyncMock(return_value=handle))
    monkeypatch.setattr(
        main, "get_conversation_db", lambda: SimpleNamespace(initialize_schema=conversation_schema)
    )
    monkeypatch.setattr(
        "cuga.backend.server.thread_lease.get_thread_lease",
        lambda: SimpleNamespace(initialize_schema=lease_schema),
    )
    close_stores = AsyncMock()
    monkeypatch.setattr(
        "cuga.backend.storage.facade.get_storage",
        lambda: SimpleNamespace(close_relational_stores=close_stores),
    )

    @asynccontextmanager
    async def app_lifespan(app):
        events.append("app-startup")
        yield
        events.append("app-shutdown")

    monkeypatch.setattr(main, "_app_lifespan", app_lifespan)
    if failure:
        with pytest.raises(RuntimeError, match="DDL denied"):
            async with main.lifespan(MagicMock()):
                pytest.fail("schema failure must prevent serving requests")
        assert "app-startup" not in events
        close_stores.assert_awaited_once()
        assert events == (["conversation"] if failure == "conversation" else ["conversation", "lease"])
    else:
        async with main.lifespan(MagicMock()):
            assert events == ["conversation", "lease", "app-startup"]
        assert events[-1] == "app-shutdown"
        close_stores.assert_not_awaited()
    handle._close.assert_awaited_once()
    assert main.app_state.checkpointer_handle is None


async def test_startup_upgrades_existing_conversation_schema_and_preserves_rows(tmp_path, monkeypatch):
    store = LocalRelationalStore(str(tmp_path / "conversation.db"))
    db, lease = ConversationHistoryDB(), ThreadLease()
    monkeypatch.setattr(db, "_get_store", lambda: store)
    monkeypatch.setattr(lease, "_get_store", lambda: store)
    monkeypatch.setattr(main, "get_conversation_db", lambda: db)
    monkeypatch.setattr("cuga.backend.server.thread_lease.get_thread_lease", lambda: lease)
    try:
        await store.execute("""CREATE TABLE runtime_thread_owners (
            tenant_id TEXT NOT NULL, instance_id TEXT NOT NULL, thread_id TEXT NOT NULL,
            user_id TEXT NOT NULL, agent_id TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, instance_id, thread_id))""")
        await store.execute(
            "INSERT INTO runtime_thread_owners VALUES ('tenant', 'instance', 'old', 'user', 'agent', '2026-01-01')"
        )
        await store.commit()
        await main._initialize_conversation_storage()
        # Safe to repeat; later requests need no new migration step.
        await main._initialize_conversation_storage()
        row = await store.fetchone("SELECT * FROM runtime_thread_owners WHERE thread_id = 'old'")
        assert row["user_id"] == "user"
        assert row["status"] == "legacy"
        assert row["last_turn_at"] == ""
        columns = await store.fetchall("PRAGMA table_info(thread_leases)")
        assert {column["name"] for column in columns} >= {"holder_id", "expires_at", "stop_requested"}
    finally:
        await store.close()
