"""Atomic runtime ownership against a disposable PostgreSQL database."""

import asyncio
import os
import uuid

import pytest

pytestmark = pytest.mark.e2e


async def test_postgres_runtime_thread_claims_across_workers(monkeypatch):
    import asyncpg
    from cuga.backend.server import conversation_history as history
    from cuga.backend.storage.relational.prod import ProdRelationalStore

    dsn = os.environ.get("CUGA_TEST_DELETION_POSTGRES_DSN")
    if not dsn:
        pytest.skip("Set CUGA_TEST_DELETION_POSTGRES_DSN to a disposable database")
    schema = "thread_owners_" + uuid.uuid4().hex
    admin = await asyncpg.connect(dsn)
    await admin.execute(f'CREATE SCHEMA "{schema}"')
    stores = [ProdRelationalStore(dsn, "conversation") for _ in range(2)]
    dbs = [history.ConversationHistoryDB() for _ in stores]
    monkeypatch.setattr(history, "_tenant_id", lambda: "tenant-a")
    monkeypatch.setattr(history, "_instance_id", lambda: "service-a")
    try:
        for db, store in zip(dbs, stores):
            store._pool = await asyncpg.create_pool(
                dsn, min_size=1, max_size=4, server_settings={"search_path": schema}
            )
            db._get_store = lambda store=store: store
            await db._ensure_schema()
        claims = await asyncio.gather(
            *(dbs[i % 2].claim_thread("race", f"user-{i}", "agent") for i in range(50)),
            return_exceptions=True,
        )
        assert claims.count("agent") == 1
        assert sum(isinstance(result, PermissionError) for result in claims) == 49
        winner = f"user-{claims.index('agent')}"
        # Reload ownership through a new connection pool, without conversation history.
        await stores[1].close()
        stores[1]._pool = await asyncpg.create_pool(
            dsn, min_size=1, max_size=4, server_settings={"search_path": schema}
        )
        assert await dbs[1].claim_thread("race", winner, "agent") == "agent"
        with pytest.raises(PermissionError):
            await dbs[1].claim_thread("race", "intruder", "agent")
        with pytest.raises(PermissionError):
            await dbs[1].claim_thread("race", winner, "other-agent")
        await dbs[0].claim_thread("agent-race", winner)
        agent_claims = await asyncio.gather(
            dbs[0].claim_thread("agent-race", winner, "agent-a"),
            dbs[1].claim_thread("agent-race", winner, "agent-b"),
            return_exceptions=True,
        )
        assert sum(isinstance(result, PermissionError) for result in agent_claims) == 1
        monkeypatch.setattr(history, "_tenant_id", lambda: "tenant-b")
        assert await dbs[1].claim_thread("race", "intruder", "agent") == "agent"
        monkeypatch.setattr(history, "_tenant_id", lambda: "tenant-a")
        monkeypatch.setattr(history, "_instance_id", lambda: "service-b")
        assert await dbs[1].claim_thread("race", "intruder", "agent") == "agent"
    finally:
        for store in stores:
            await store.close()
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()
