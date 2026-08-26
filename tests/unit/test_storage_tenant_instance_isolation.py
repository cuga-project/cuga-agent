"""Tenant + instance isolation for every store that lands in Postgres in prod."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from cuga.backend.cuga_graph.policy.configurable import resolve_local_policy_db_path
from cuga.backend.knowledge.metadata import create_knowledge_metadata
from cuga.backend.knowledge.metadata.postgres_store import PostgresKnowledgeMetadata
from cuga.backend.knowledge.metadata.sqlite_store import SqliteKnowledgeMetadata
from cuga.backend.knowledge.session_provider import (
    AgentKnowledgeState,
    SessionKnowledgeState,
    StorageBackedSessionProvider,
    create_session_provider,
)

pytestmark = pytest.mark.unit


def _patch_scope(monkeypatch, tenant: str, instance: str) -> None:
    monkeypatch.setattr("cuga.backend.knowledge.metadata.base.get_tenant_id", lambda: tenant)
    monkeypatch.setattr("cuga.backend.knowledge.metadata.base.get_service_instance_id", lambda: instance)
    monkeypatch.setattr("cuga.backend.server.conversation_history.get_tenant_id", lambda: tenant)
    monkeypatch.setattr("cuga.backend.server.conversation_history.get_service_instance_id", lambda: instance)
    monkeypatch.setattr("cuga.config.get_tenant_id", lambda: tenant)
    monkeypatch.setattr("cuga.config.get_service_instance_id", lambda: instance)


@pytest.fixture
def meta_db(tmp_path):
    db = SqliteKnowledgeMetadata(tmp_path / "metadata.db")
    yield db


@pytest.mark.asyncio
async def test_knowledge_metadata_isolates_documents_by_tenant_and_instance(meta_db, monkeypatch):
    _patch_scope(monkeypatch, "tenant-a", "inst-1")
    await meta_db.add_document("kb_agent_default", "a.pdf", 3)

    _patch_scope(monkeypatch, "tenant-b", "inst-1")
    await meta_db.add_document("kb_agent_default", "b.pdf", 5)

    docs_b = await meta_db.list_documents("kb_agent_default")
    assert [d["filename"] for d in docs_b] == ["b.pdf"]
    assert not await meta_db.document_exists("kb_agent_default", "a.pdf")

    _patch_scope(monkeypatch, "tenant-a", "inst-1")
    docs_a = await meta_db.list_documents("kb_agent_default")
    assert [d["filename"] for d in docs_a] == ["a.pdf"]


@pytest.mark.asyncio
async def test_knowledge_metadata_isolates_tasks_and_settings(meta_db, monkeypatch):
    _patch_scope(monkeypatch, "t1", "i1")
    await meta_db.create_task("task-shared-id", "col", 1, {"f.pdf": {"filename": "f.pdf"}})
    await meta_db.set_setting("chunk_size", "100")
    await meta_db.set_collection_config("col", "hf", "m", 384)

    _patch_scope(monkeypatch, "t2", "i1")
    assert await meta_db.get_task("task-shared-id") is None
    assert await meta_db.list_tasks("col") == []
    assert await meta_db.get_setting("chunk_size", "missing") == "missing"
    assert await meta_db.get_collection_config("col") is None
    await meta_db.create_task("task-shared-id", "col", 1, {"g.pdf": {"filename": "g.pdf"}})
    task = await meta_db.get_task("task-shared-id")
    assert task is not None
    assert "g.pdf" in task["file_tasks"]


@pytest.mark.asyncio
async def test_knowledge_metadata_maintenance_does_not_cross_scope(meta_db, monkeypatch):
    _patch_scope(monkeypatch, "t1", "i1")
    await meta_db.add_document("col", "stale.pdf", 1)
    await meta_db.mark_deleting("col", "stale.pdf")
    await meta_db.create_task("t-run", "col", 1, {"f.pdf": {"filename": "f.pdf", "status": "processing"}})
    await meta_db.update_task("t-run", status="running")

    _patch_scope(monkeypatch, "t2", "i2")
    assert await meta_db.get_deleting_documents() == []
    assert await meta_db.recover_stale_tasks() == 0

    _patch_scope(monkeypatch, "t1", "i1")
    deleting = await meta_db.get_deleting_documents()
    assert len(deleting) == 1
    assert deleting[0]["filename"] == "stale.pdf"
    assert await meta_db.recover_stale_tasks() == 1


@pytest.mark.asyncio
async def test_sqlite_migrates_legacy_schema_without_scope_columns(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE documents (
            collection TEXT NOT NULL,
            filename TEXT NOT NULL,
            chunk_count INTEGER DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'indexed',
            ingested_at TEXT NOT NULL,
            preview TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (collection, filename)
        );
        INSERT INTO documents VALUES ('col', 'old.pdf', 2, 'indexed', '2020-01-01T00:00:00+00:00', 'hi');
        """
    )
    conn.commit()
    conn.close()

    _patch_scope(monkeypatch, "", "")
    store = SqliteKnowledgeMetadata(db_path)
    docs = await store.list_documents("col")
    assert len(docs) == 1
    assert docs[0]["filename"] == "old.pdf"
    await store.close()


def test_create_knowledge_metadata_wires_prod_to_postgres():
    store = create_knowledge_metadata(
        Path("/tmp/unused"),
        mode="prod",
        postgres_url="postgresql://example/cuga",
    )
    assert isinstance(store, PostgresKnowledgeMetadata)


def test_create_knowledge_metadata_prod_requires_url(tmp_path):
    with pytest.raises(ValueError, match="postgres_url"):
        create_knowledge_metadata(tmp_path, mode="prod", postgres_url="")


def test_resolve_local_policy_db_path_ignores_settings_in_prod():
    assert resolve_local_policy_db_path(None, "/tmp/policies.db", "prod") is None
    assert resolve_local_policy_db_path(None, "/tmp/policies.db", "local") == "/tmp/policies.db"
    assert resolve_local_policy_db_path("/explicit.db", "/tmp/policies.db", "prod") == "/explicit.db"


def test_gc_stream_events_only_touches_current_tenant_instance(monkeypatch):
    import asyncio

    import cuga.backend.storage.facade as facade
    from cuga.backend.server.conversation_history import ConversationHistoryDB

    tmpfile = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmpfile.close()
    original = facade._local_db_path
    facade._local_db_path = lambda: tmpfile.name
    facade.get_storage().invalidate_relational_stores()
    db = ConversationHistoryDB()
    db._schema_ensured = False
    try:

        async def _run():
            events = [{"event_name": "Answer", "event_data": "x", "timestamp": "t", "sequence": 0}]
            _patch_scope(monkeypatch, "tenant-a", "inst-a")
            await db.save_stream_events("agent", "orphan-a", "user", events)
            _patch_scope(monkeypatch, "tenant-b", "inst-b")
            await db.save_stream_events("agent", "orphan-b", "user", events)
            removed = await db.gc_ephemeral_stream_events(older_than_days=0)
            assert removed == 1
            assert await db.get_stream_events("agent", "orphan-b", "user") is None
            _patch_scope(monkeypatch, "tenant-a", "inst-a")
            kept = await db.get_stream_events("agent", "orphan-a", "user")
            assert kept is not None
            assert len(kept.events) == 1

        asyncio.run(_run())
    finally:
        facade._local_db_path = original
        facade.get_storage().invalidate_relational_stores()
        Path(tmpfile.name).unlink(missing_ok=True)


def test_storage_backed_session_provider_isolates_by_scope(monkeypatch, tmp_path):
    import cuga.backend.storage.facade as facade

    db_path = str(tmp_path / "cuga.db")
    original = facade._local_db_path
    facade._local_db_path = lambda: db_path
    facade.get_storage().invalidate_relational_stores()
    try:
        _patch_scope(monkeypatch, "tenant-a", "inst-1")
        a = StorageBackedSessionProvider()
        a.save_session(
            "thread-1",
            SessionKnowledgeState(thread_id="thread-1", user_id="alice", tenant_id="tenant-a"),
        )
        a.save_agent(AgentKnowledgeState(agent_id="cuga-default", config_version="1"))

        _patch_scope(monkeypatch, "tenant-b", "inst-1")
        b = StorageBackedSessionProvider()
        assert b.get_session("thread-1") is None
        assert b.list_agents() == {}
        b.save_session(
            "thread-1",
            SessionKnowledgeState(thread_id="thread-1", user_id="bob", tenant_id="tenant-b"),
        )

        _patch_scope(monkeypatch, "tenant-a", "inst-1")
        a2 = StorageBackedSessionProvider()
        assert a2.get_session("thread-1").user_id == "alice"
        assert "cuga-default:1" in a2.list_agents()
    finally:
        facade._local_db_path = original
        facade.get_storage().invalidate_relational_stores()


def test_create_session_provider_imports_legacy_json(monkeypatch, tmp_path):
    import cuga.backend.storage.facade as facade
    from cuga.backend.knowledge.session_provider import PersistentSessionProvider

    db_path = str(tmp_path / "cuga.db")
    original = facade._local_db_path
    facade._local_db_path = lambda: db_path
    facade.get_storage().invalidate_relational_stores()
    try:
        _patch_scope(monkeypatch, "t", "i")
        json_path = tmp_path / "session_knowledge.json"
        legacy = PersistentSessionProvider(json_path)
        legacy.save_session("th", SessionKnowledgeState(thread_id="th", user_id="u", tenant_id="t"))
        provider = create_session_provider(json_legacy_path=json_path)
        assert provider.get_session("th") is not None
        assert provider.get_session("th").user_id == "u"
    finally:
        facade._local_db_path = original
        facade.get_storage().invalidate_relational_stores()


@pytest.mark.asyncio
async def test_postgres_metadata_sql_always_includes_scope_predicates():
    store = PostgresKnowledgeMetadata("postgresql://fake/test")
    executed: list[tuple[str, tuple]] = []

    async def _execute(sql: str, params: tuple = ()):
        executed.append((sql, tuple(params)))

    async def _fetchone(sql: str, params: tuple = ()):
        executed.append((sql, tuple(params)))
        return None

    async def _fetchall(sql: str, params: tuple = ()):
        executed.append((sql, tuple(params)))
        return []

    async def _commit() -> None:
        return None

    store.execute = _execute  # type: ignore[method-assign]
    store.fetchone = _fetchone  # type: ignore[method-assign]
    store.fetchall = _fetchall  # type: ignore[method-assign]
    store.commit = _commit  # type: ignore[method-assign]
    store._schema_initialized = True
    store._last_rowcount = 0

    with (
        patch("cuga.backend.knowledge.metadata.base.get_tenant_id", return_value="ten"),
        patch("cuga.backend.knowledge.metadata.base.get_service_instance_id", return_value="inst"),
    ):
        await store.add_document("col", "f.pdf", 1)
        await store.list_documents("col")
        await store.get_deleting_documents()
        await store.get_task("t1")
        await store.list_tasks()
        await store.recover_stale_tasks()
        await store.purge_old_tasks()
        await store.get_all_settings()
        await store.list_all_collection_configs()

    assert executed
    for sql, params in executed:
        assert "tenant_id" in sql, sql
        assert "instance_id" in sql, sql
        assert params[0] == "ten"
        assert params[1] == "inst"
