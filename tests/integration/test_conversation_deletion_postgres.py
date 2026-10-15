"""Atomic source-deletion outbox behavior on the production PostgreSQL store."""

import os
import uuid

import pytest

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_postgres_conversation_deletion_rolls_back_with_outbox():
    import asyncpg
    from cuga.backend.server.conversation_history import ConversationHistoryDB
    from cuga.backend.storage.relational.prod import ProdRelationalStore

    dsn = os.environ.get("CUGA_TEST_DELETION_POSTGRES_DSN")
    if not dsn:
        pytest.skip("Set CUGA_TEST_DELETION_POSTGRES_DSN to a disposable database")
    schema = "deletion_" + uuid.uuid4().hex
    admin = await asyncpg.connect(dsn)
    await admin.execute(f'CREATE SCHEMA "{schema}"')
    store = ProdRelationalStore(dsn, "conversation")
    store._pool = await asyncpg.create_pool(dsn, server_settings={"search_path": schema})
    db = ConversationHistoryDB()
    db._get_store = lambda: store
    try:
        assert await db.save_stream_events("agent", "thread", "user", [])
        await store.execute("""CREATE FUNCTION reject_deletion() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'injected failure'; END; $$ LANGUAGE plpgsql""")
        await store.execute(
            "CREATE TRIGGER reject_deletion BEFORE DELETE ON stream_events FOR EACH ROW EXECUTE FUNCTION reject_deletion()"
        )
        assert not await db.delete_thread("agent", "thread", "user")
        assert await db.pending_source_deletions() == []
        assert await db.get_stream_events("agent", "thread", "user") is not None
        await store.execute("DROP TRIGGER reject_deletion ON stream_events")
        assert await db.delete_thread("agent", "thread", "user")
        assert await db.get_stream_events("agent", "thread", "user") is None
        assert len(await db.pending_source_deletions()) == 1
    finally:
        await store.close()
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()
