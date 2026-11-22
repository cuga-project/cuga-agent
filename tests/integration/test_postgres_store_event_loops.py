"""The cached PostgreSQL config store used from more than one event loop.

The store is cached for the whole process, while sync code reaches it through wrappers that
run each call on a loop of their own: ``config_store.run_sync`` (``cuga start`` seeds the agent
config with several of them) and ``secrets_store.get_secret_sync`` (the secret resolver reads
API keys saved in the UI with it, from inside the server's loop). An asyncpg pool works only on
the loop that created it.

Local run: start pgvector as described in ``test_knowledge_pgvector_rollback.py`` and set
``CUGA_KNOWLEDGE_PGVECTOR_CONNECTION_STRING``.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from cryptography.fernet import Fernet

from cuga.backend.server import config_store
from cuga.backend.storage import get_storage, secrets_store
from cuga.backend.storage.relational.prod import ProdRelationalStore

from .test_knowledge_pgvector_rollback import _dsn

asyncpg = pytest.importorskip("asyncpg")

pytestmark = pytest.mark.pgvector


async def _fetchval(dsn: str, sql: str, *args):
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval(sql, *args)
    finally:
        await conn.close()


def _connections(dsn: str) -> int:
    return asyncio.run(
        _fetchval(
            dsn,
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE datname = current_database() AND pid <> pg_backend_pid()",
        )
    )


def _assert_connections_back_to(dsn: str, before: int) -> None:
    """A closed connection leaves pg_stat_activity a moment later; one left open never does."""
    deadline = time.monotonic() + 10
    while (now := _connections(dsn)) > before and time.monotonic() < deadline:
        time.sleep(0.2)
    assert now <= before, f"{now - before} connection(s) left open"


@pytest.fixture
def config_db(clean_tenant, monkeypatch):
    dsn = _dsn()
    store = ProdRelationalStore(dsn, "config")
    monkeypatch.setitem(get_storage()._relational_stores, "config", store)
    for module in (config_store, secrets_store):
        monkeypatch.setattr(module, "_tenant_id", lambda: clean_tenant["tenant_id"])
        monkeypatch.setattr(module, "_instance_id", lambda: clean_tenant["instance_id"])
    monkeypatch.setenv("CUGA_SECRET_KEY", Fernet.generate_key().decode())
    yield dsn
    asyncio.run(_delete_tenant_rows(dsn, clean_tenant["tenant_id"]))


async def _delete_tenant_rows(dsn: str, tenant_id: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        for table in ("agent_configs", "secrets"):
            if await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", table):
                await conn.execute(f"DELETE FROM {table} WHERE tenant_id = $1", tenant_id)
    finally:
        await conn.close()


def test_cuga_start_sync_calls_each_on_its_own_loop(config_db):
    """`cuga start` checks for a saved config, reads it, then seeds one: one loop per call."""
    before = _connections(config_db)

    assert config_store.run_sync(config_store.has_any_config("agent")) is False
    assert config_store.run_sync(config_store.load_config(None, "agent")) == (None, None)
    config_store.run_sync(config_store.save_draft({"agent": {"name": "Seeded"}}, "agent"))
    assert config_store.run_sync(config_store.has_any_config("agent")) is True

    _assert_connections_back_to(config_db, before)


def test_ui_saved_secret_is_read_from_sync_code_inside_the_server_loop(config_db):
    """The server loop already uses the store when the resolver reads the key from sync code."""
    before = _connections(config_db)

    async def server():
        await secrets_store.set_secret("openai-api-key", "key-saved-in-the-ui")
        assert secrets_store.get_secret_sync("openai-api-key") == "key-saved-in-the-ui"
        # The server loop's own pool still works after the read on the other loop.
        assert await secrets_store.get_secret("openai-api-key") == "key-saved-in-the-ui"
        await get_storage().close_loop_pools()

    asyncio.run(server())
    _assert_connections_back_to(config_db, before)


def test_server_loop_uses_the_store_after_a_sync_secret_read_came_first(config_db):
    """A secret read from sync code is the process's first use of the store."""

    async def server():
        assert secrets_store.get_secret_sync("not-saved") is None
        await config_store.save_draft({"agent": {"name": "After"}}, "agent")
        await secrets_store.set_secret("openai-api-key", "saved-after")
        assert await secrets_store.get_secret("openai-api-key") == "saved-after"
        await get_storage().close_loop_pools()

    asyncio.run(server())
