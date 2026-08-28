from __future__ import annotations

import asyncio
import json
import sqlite3

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
        conn.execute(
            """
        CREATE TABLE account_only_records (
            account_id TEXT NOT NULL,
            payload TEXT NOT NULL
        )
            """
        )
        conn.execute("INSERT INTO account_only_records VALUES (?, ?)", ("account-a", "keep"))
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
