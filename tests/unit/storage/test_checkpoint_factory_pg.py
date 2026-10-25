"""``create_checkpointer`` against a real Postgres (issue #774).

Uses the same connection string as the other ``pgvector`` tests
(``CUGA_KNOWLEDGE_PGVECTOR_CONNECTION_STRING``, set by the
``pgvector-integration`` CI job) and skips when it is unset. Local run::

    docker run --rm -d --name cuga-pgvector-test \\
        -e POSTGRES_PASSWORD=cuga -e POSTGRES_USER=cuga \\
        -e POSTGRES_DB=cuga_test -p 5432:5432 \\
        pgvector/pgvector:0.8.0-pg16
    export CUGA_KNOWLEDGE_PGVECTOR_CONNECTION_STRING=\\
        postgresql://cuga:cuga@localhost:5432/cuga_test  # pragma: allowlist secret
    uv run pytest tests/unit/storage/test_checkpoint_factory_pg.py -m pgvector -v
"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest

from cuga.backend.storage.checkpoint import create_checkpointer

pytestmark = pytest.mark.pgvector


def _dsn() -> str:
    dsn = os.environ.get("CUGA_KNOWLEDGE_PGVECTOR_CONNECTION_STRING")
    if not dsn:
        pytest.skip("CUGA_KNOWLEDGE_PGVECTOR_CONNECTION_STRING not set")
    return dsn


def _storage(dsn: str) -> SimpleNamespace:
    return SimpleNamespace(
        checkpointer="durable",
        mode="prod",
        postgres_url=dsn,
        checkpoint_pool_min_size=1,
        checkpoint_pool_max_size=2,
    )


async def test_postgres_handle_setup_round_trip_and_close():
    from langgraph.checkpoint.base import empty_checkpoint
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    dsn = _dsn()
    thread_id = f"w02-1-{uuid.uuid4().hex}"
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}

    handle = await create_checkpointer(_storage(dsn))
    try:
        assert handle.kind == "postgres"
        assert isinstance(handle.saver, AsyncPostgresSaver)
        # The factory already ran setup(); it must be safe to run again.
        await handle.saver.setup()

        checkpoint = empty_checkpoint()
        checkpoint["channel_values"] = {"messages": ["hello"]}
        checkpoint["channel_versions"] = {"messages": 1}
        saved = await handle.saver.aput(config, checkpoint, {"source": "input", "step": 0}, {"messages": 1})

        loaded = await handle.saver.aget_tuple(config)
        assert loaded is not None
        assert loaded.config["configurable"]["checkpoint_id"] == saved["configurable"]["checkpoint_id"]
        assert loaded.checkpoint["channel_values"] == {"messages": ["hello"]}
    finally:
        try:
            await handle.saver.adelete_thread(thread_id)
        finally:
            await handle.aclose()

    # A second handle on the same database runs setup() again without error.
    second = await create_checkpointer(_storage(dsn))
    try:
        assert await second.saver.aget_tuple(config) is None
    finally:
        await second.aclose()
