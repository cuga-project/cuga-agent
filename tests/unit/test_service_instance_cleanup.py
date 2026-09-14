from __future__ import annotations

import asyncio
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from typer.testing import CliRunner

from cuga.backend.knowledge.storage.schema import knowledge_embedding_schema
from cuga.backend.server import config_store
from cuga.backend.server.conversation_history import ConversationHistoryDB
from cuga.backend.storage import facade, secrets_store
from cuga.backend.storage.embedding.local import LocalEmbeddingStore
from cuga.cli.main import app
from cuga.backend.storage import service_instance_cleanup

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def isolated_cleanup_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(
        service_instance_cleanup,
        "settings",
        SimpleNamespace(knowledge={"persist_dir": str(tmp_path / "knowledge")}, policy=None),
        raising=False,
    )


runner = CliRunner()


async def _seed_db(path, monkeypatch):
    monkeypatch.setattr(facade, "_storage_facade", None)
    monkeypatch.setattr(facade, "_storage_mode", lambda: "local")
    monkeypatch.setattr(facade, "_local_db_path", lambda: str(path))
    monkeypatch.setattr(facade, "_postgres_url", lambda: "")

    storage = facade.get_storage()
    store = storage.get_relational_store("test")
    await config_store._ensure_schema(store)
    await ConversationHistoryDB()._ensure_schema()
    await secrets_store.ensure_schema(store)

    await store.execute(
        """
        INSERT INTO agent_configs
            (tenant_id, instance_id, agent_id, version, config_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("account-a", "instance-1", "agent", "draft", "{}", "now", "now"),
    )
    await store.execute(
        """
        INSERT INTO agent_configs
            (tenant_id, instance_id, agent_id, version, config_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("account-b", "instance-1", "agent", "1", "{}", "now", "now"),
    )
    await store.execute(
        """
        INSERT INTO agent_configs
            (tenant_id, instance_id, agent_id, version, config_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("account-a", "instance-2", "agent", "draft", "{}", "now", "now"),
    )
    await store.execute(
        """
        INSERT INTO conversation_history
            (tenant_id, instance_id, agent_id, thread_id, version, user_id, messages, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("account-a", "instance-1", "agent", "thread-1", 1, "user", "[]", "now", "now"),
    )
    await store.execute(
        """
        INSERT INTO conversation_history
            (tenant_id, instance_id, agent_id, thread_id, version, user_id, messages, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("account-a", "instance-2", "agent", "thread-2", 1, "user", "[]", "now", "now"),
    )
    await store.execute(
        """
        INSERT INTO stream_events
            (tenant_id, instance_id, agent_id, thread_id, user_id, events, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("account-a", "instance-1", "agent", "thread-1", "user", "[]", "now", "now"),
    )
    await store.execute(
        """
        INSERT INTO stream_events
            (tenant_id, instance_id, agent_id, thread_id, user_id, events, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("account-a", "instance-2", "agent", "thread-2", "user", "[]", "now", "now"),
    )
    await store.execute(
        """
        INSERT INTO secrets
            (tenant_id, instance_id, agent_id, version, id, created_by, encrypted_value, description, tags)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("account-a", "instance-1", "*", "*", "secret-1", "user", b"value", None, "[]"),
    )
    await store.execute(
        """
        INSERT INTO secrets
            (tenant_id, instance_id, agent_id, version, id, created_by, encrypted_value, description, tags)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("account-a", "instance-2", "*", "*", "secret-2", "user", b"value", None, "[]"),
    )
    await store.commit()

    vectors = LocalEmbeddingStore(str(path), "kb_agent_test", knowledge_embedding_schema(4))
    await vectors.add_many(
        [
            (
                "chunk-1",
                [0.1, 0.2, 0.3, 0.4],
                {
                    "id": "chunk-1",
                    "tenant_id": "account-a",
                    "instance_id": "instance-1",
                    "source": "doc.md",
                    "filename": "doc.md",
                    "page": 1,
                    "chunk_text": "one",
                    "meta_json": "{}",
                },
            ),
            (
                "chunk-2",
                [0.4, 0.3, 0.2, 0.1],
                {
                    "id": "chunk-2",
                    "tenant_id": "account-a",
                    "instance_id": "instance-2",
                    "source": "doc.md",
                    "filename": "doc.md",
                    "page": 2,
                    "chunk_text": "two",
                    "meta_json": "{}",
                },
            ),
        ]
    )
    await storage.close_relational_stores()
    monkeypatch.setattr(facade, "_storage_facade", None)

    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE account_only_records (account_id TEXT NOT NULL, payload TEXT NOT NULL)")
        conn.execute("INSERT INTO account_only_records VALUES (?, ?)", ("account-a", "keep"))
        conn.execute("CREATE TABLE documents (instance_id TEXT NOT NULL, collection TEXT NOT NULL)")
        conn.execute("INSERT INTO documents VALUES (?, ?)", ("instance-1", "kb_agent_test"))
        conn.execute("CREATE TABLE kb_agent_test__fts (instance_id TEXT NOT NULL, chunk_text TEXT NOT NULL)")
        conn.execute("INSERT INTO kb_agent_test__fts VALUES (?, ?)", ("instance-1", "derived"))
        conn.commit()
    finally:
        conn.close()


def _count(path, table: str) -> int:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def _instance_ids(path, table: str) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(f"SELECT instance_id FROM {table} ORDER BY instance_id").fetchall()
        return [row[0] for row in rows]
    finally:
        conn.close()


async def _vector_instance_ids(path, table: str) -> list[str]:
    vectors = LocalEmbeddingStore(str(path), table, knowledge_embedding_schema(4))
    rows = await vectors.list({}, 10)
    return sorted(row["instance_id"] for row in rows)


def test_delete_service_instance_records_removes_all_matching_sqlite_rows(monkeypatch, tmp_path):
    db_path = tmp_path / "cuga.db"
    asyncio.run(_seed_db(db_path, monkeypatch))
    monkeypatch.setattr(
        service_instance_cleanup,
        "get_storage_connection_params",
        lambda: ("local", str(db_path), ""),
    )

    result = asyncio.run(service_instance_cleanup.delete_service_instance_records("instance-1"))

    assert result.service_instance_id == "instance-1"
    assert result.deleted_records == 6
    assert result.tables == {
        "agent_configs": 2,
        "conversation_history": 1,
        "kb_agent_test": 1,
        "secrets": 1,
        "stream_events": 1,
    }
    assert _count(db_path, "agent_configs") == 1
    assert _count(db_path, "conversation_history") == 1
    assert _count(db_path, "secrets") == 1
    assert _count(db_path, "stream_events") == 1
    assert _instance_ids(db_path, "agent_configs") == ["instance-2"]
    assert _instance_ids(db_path, "conversation_history") == ["instance-2"]
    assert _instance_ids(db_path, "secrets") == ["instance-2"]
    assert _instance_ids(db_path, "stream_events") == ["instance-2"]
    assert asyncio.run(_vector_instance_ids(db_path, "kb_agent_test")) == ["instance-2"]
    assert _count(db_path, "account_only_records") == 1
    assert _count(db_path, "documents") == 1
    assert _count(db_path, "kb_agent_test__fts") == 1


def test_delete_service_instance_records_dry_run_counts_without_deleting(monkeypatch, tmp_path):
    db_path = tmp_path / "cuga.db"
    asyncio.run(_seed_db(db_path, monkeypatch))
    monkeypatch.setattr(
        service_instance_cleanup,
        "get_storage_connection_params",
        lambda: ("local", str(db_path), ""),
    )

    result = asyncio.run(service_instance_cleanup.delete_service_instance_records("instance-1", dry_run=True))

    assert result.dry_run is True
    assert result.deleted_records == 6
    assert result.tables == {
        "agent_configs": 2,
        "conversation_history": 1,
        "kb_agent_test": 1,
        "secrets": 1,
        "stream_events": 1,
    }
    assert _count(db_path, "agent_configs") == 3
    assert _count(db_path, "conversation_history") == 2
    assert _count(db_path, "secrets") == 2
    assert _count(db_path, "stream_events") == 2
    assert _instance_ids(db_path, "agent_configs") == ["instance-1", "instance-1", "instance-2"]
    assert _instance_ids(db_path, "conversation_history") == ["instance-1", "instance-2"]
    assert _instance_ids(db_path, "secrets") == ["instance-1", "instance-2"]
    assert _instance_ids(db_path, "stream_events") == ["instance-1", "instance-2"]
    assert asyncio.run(_vector_instance_ids(db_path, "kb_agent_test")) == ["instance-1", "instance-2"]
    assert _count(db_path, "documents") == 1
    assert _count(db_path, "kb_agent_test__fts") == 1


def test_sqlite_discovery_excludes_knowledge_metadata_and_fts_tables(tmp_path):
    db_path = tmp_path / "cuga.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE agent_configs (instance_id TEXT NOT NULL)")
        conn.execute("CREATE TABLE cuga_knowledge_meta_documents (instance_id TEXT NOT NULL)")
        conn.execute("CREATE TABLE kb_agent_test__fts (instance_id TEXT NOT NULL)")

        assert service_instance_cleanup._sqlite_tables_with_instance_id(conn) == ["agent_configs"]
    finally:
        conn.close()


def test_postgres_discovery_excludes_knowledge_metadata_and_fts_tables():
    class FakeConnection:
        async def fetch(self, sql):
            assert "cuga_knowledge_meta_documents" in sql
            assert "\\_\\_fts" in sql
            return [
                {"table_schema": "public", "table_name": "agent_configs"},
            ]

    class FakeAcquire:
        async def __aenter__(self):
            return FakeConnection()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class FakePool:
        def acquire(self):
            return FakeAcquire()

    assert asyncio.run(service_instance_cleanup._postgres_tables_with_instance_id(FakePool())) == [
        ("public", "agent_configs")
    ]


def test_delete_service_instance_records_rejects_blank_service_instance_id():
    with pytest.raises(ValueError, match="service_instance_id is required"):
        asyncio.run(service_instance_cleanup.delete_service_instance_records(" "))


def test_purge_cli_requires_service_instance_id():
    result = runner.invoke(app, ["purge", "service-instance"])

    assert result.exit_code != 0


def test_purge_service_instance_records_cli(monkeypatch):
    async def fake_delete_service_instance_records(service_instance_id: str, *, dry_run: bool = False):
        assert service_instance_id == "instance-1"
        assert dry_run is False
        return service_instance_cleanup.ServiceInstanceCleanupResult(
            service_instance_id="instance-1",
            dry_run=False,
            deleted_records=3,
            tables={"agent_configs": 2, "conversation_history": 1},
        )

    monkeypatch.setattr(
        service_instance_cleanup,
        "delete_service_instance_records",
        fake_delete_service_instance_records,
    )

    result = runner.invoke(app, ["purge", "service-instance", "--service-instance-id", "instance-1"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "service_instance_id": "instance-1",
        "dry_run": False,
        "deleted_records": 3,
        "tables": {"agent_configs": 2, "conversation_history": 1},
    }


@pytest.mark.parametrize("dry_run", [True, False])
def test_preserves_exact_instance_id(monkeypatch, tmp_path, dry_run):
    path = tmp_path / "primary.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE records (instance_id TEXT)")
        conn.executemany("INSERT INTO records VALUES (?)", [("instance-1",), (" instance-1 ",)])
    monkeypatch.setattr(
        service_instance_cleanup, "get_storage_connection_params", lambda: ("local", str(path), "")
    )
    result = asyncio.run(
        service_instance_cleanup.delete_service_instance_records(" instance-1 ", dry_run=dry_run)
    )
    assert result.service_instance_id == " instance-1 "
    assert result.deleted_records == 1
    assert _instance_ids(path, "records") == ([" instance-1 ", "instance-1"] if dry_run else ["instance-1"])


@pytest.mark.parametrize("dry_run", [True, False])
def test_purges_separate_knowledge_and_policy_databases(monkeypatch, tmp_path, dry_run):
    from langchain_core.documents import Document
    from langchain_core.embeddings import DeterministicFakeEmbedding
    from cuga.backend.knowledge.vector_store import create_vector_store
    from cuga.backend.knowledge.storage import adapter

    primary = tmp_path / "primary.db"
    policy = tmp_path / "policy.db"
    for path in (primary, policy):
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE records (instance_id TEXT)")
            conn.executemany("INSERT INTO records VALUES (?)", [("instance-1",), ("instance-2",)])
    monkeypatch.setattr(
        service_instance_cleanup, "get_storage_connection_params", lambda: ("local", str(primary), "")
    )
    monkeypatch.setattr(service_instance_cleanup, "DBS_DIR", str(tmp_path), raising=False)
    service_instance_cleanup.settings.policy = SimpleNamespace(policy_db_path="policy.db")
    vectors = create_vector_store(
        "storage_local", "knowledge_chunks", DeterministicFakeEmbedding(size=4), tmp_path / "knowledge"
    )
    for instance_id in ("instance-1", "instance-2"):
        monkeypatch.setattr(adapter, "get_service_instance_id", lambda: instance_id)
        vectors.add_documents([Document(page_content=instance_id, metadata={"source": instance_id})])

    result = asyncio.run(
        service_instance_cleanup.delete_service_instance_records("instance-1", dry_run=dry_run)
    )
    expected = ["instance-1", "instance-2"] if dry_run else ["instance-2"]
    assert _instance_ids(primary, "records") == expected
    assert _instance_ids(policy, "records") == expected
    assert (
        asyncio.run(_vector_instance_ids(tmp_path / "knowledge" / "knowledge_vectors.db", "knowledge_chunks"))
        == expected
    )
    assert result.deleted_records == 3
    assert result.tables["records"] == 2
    assert result.tables["knowledge_chunks"] == 1


@pytest.mark.parametrize("separate", [True, False])
@pytest.mark.parametrize("dry_run", [True, False])
def test_postgres_targets_are_deduplicated(monkeypatch, separate, dry_run):
    primary = "postgresql://localhost/primary"
    knowledge = "postgresql://localhost/knowledge" if separate else primary
    service_instance_cleanup.settings.knowledge = {"pgvector_connection_string": knowledge}
    monkeypatch.setattr(
        service_instance_cleanup, "get_storage_connection_params", lambda: ("prod", "", primary)
    )
    delete = AsyncMock(return_value={"chunks": 2})
    monkeypatch.setattr(service_instance_cleanup, "_delete_postgres_service_instance_records", delete)
    result = asyncio.run(
        service_instance_cleanup.delete_service_instance_records(" instance-1 ", dry_run=dry_run)
    )
    assert [call.args for call in delete.call_args_list] == [
        (url, " instance-1 ") for url in ([primary, knowledge] if separate else [primary])
    ]
    assert all(call.kwargs == {"dry_run": dry_run} for call in delete.call_args_list)
    assert result.deleted_records == (4 if separate else 2)


@pytest.mark.parametrize("dry_run", [True, False])
def test_shared_sqlite_targets_are_counted_once(monkeypatch, tmp_path, dry_run):
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    path = knowledge_dir / "knowledge_vectors.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE records (instance_id TEXT)")
        conn.execute("INSERT INTO records VALUES ('instance-1')")
    alias = tmp_path / "policy.db"
    alias.symlink_to(path)
    service_instance_cleanup.settings.policy = SimpleNamespace(policy_db_path=str(alias))
    monkeypatch.setattr(
        service_instance_cleanup, "get_storage_connection_params", lambda: ("local", str(path), "")
    )
    result = asyncio.run(
        service_instance_cleanup.delete_service_instance_records("instance-1", dry_run=dry_run)
    )
    assert result.tables == {"records": 1}
    assert _count(path, "records") == (1 if dry_run else 0)


def test_default_knowledge_path_and_missing_optional_stores(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    service_instance_cleanup.settings.knowledge = {}
    service_instance_cleanup.settings.policy = SimpleNamespace(policy_db_path=str(tmp_path / "missing.db"))
    primary = tmp_path / "primary.db"
    monkeypatch.setattr(
        service_instance_cleanup, "get_storage_connection_params", lambda: ("local", str(primary), "")
    )
    assert service_instance_cleanup._configured_storage_targets() == [("local", str(primary.resolve()))]
    assert not (tmp_path / "missing.db").exists()
    knowledge = tmp_path / ".cuga" / "knowledge" / "knowledge_vectors.db"
    knowledge.parent.mkdir(parents=True)
    knowledge.touch()
    assert service_instance_cleanup._configured_storage_targets() == [
        ("local", str(primary.resolve())),
        ("local", str(knowledge.resolve())),
    ]


def test_prod_uses_primary_fallback_and_local_policy_override(monkeypatch, tmp_path):
    primary = "postgresql://localhost/primary"
    policy = tmp_path / "policy.db"
    policy.touch()
    service_instance_cleanup.settings.policy = SimpleNamespace(policy_db_path=str(policy))
    monkeypatch.setattr(
        service_instance_cleanup, "get_storage_connection_params", lambda: ("prod", "", primary)
    )
    assert service_instance_cleanup._configured_storage_targets() == [
        ("prod", primary),
        ("local", str(policy.resolve())),
    ]
