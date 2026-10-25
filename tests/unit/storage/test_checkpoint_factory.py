"""``create_checkpointer`` (issue #774).

Memory and SQLite run for real; Postgres patches the pool and saver classes where the
factory imports them. The real-database test is ``test_checkpoint_factory_pg.py``.
"""

from __future__ import annotations

import os
import stat
import sys
from types import SimpleNamespace

import pytest

from cuga.backend.storage.checkpoint import create_checkpointer

pytestmark = pytest.mark.unit

_AGENT_STATE_PAIR = ("cuga.backend.cuga_graph.state.agent_state", "AgentState")


def _storage(**overrides):
    values = {
        "checkpointer": "durable",
        "mode": "local",
        "checkpoint_db_path": "",
        "postgres_url": "",
        "checkpoint_pool_min_size": 1,
        "checkpoint_pool_max_size": 10,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _uses_cuga_serde(saver) -> bool:
    return _AGENT_STATE_PAIR in (getattr(saver.serde, "_allowed_msgpack_modules", None) or ())


@pytest.mark.parametrize(
    "storage",
    [
        _storage(checkpointer="sqlite"),
        _storage(checkpointer=None),
        _storage(mode="cloud"),
        _storage(mode="prod", postgres_url=""),
        _storage(mode="prod", postgres_url="postgresql://h/db", checkpoint_pool_min_size=0),
        _storage(
            mode="prod",
            postgres_url="postgresql://h/db",
            checkpoint_pool_min_size=5,
            checkpoint_pool_max_size=2,
        ),
    ],
    ids=[
        "unknown-checkpointer",
        "missing-checkpointer",
        "unknown-mode",
        "prod-no-url",
        "pool-min-0",
        "pool-min-gt-max",
    ],
)
async def test_invalid_settings_raise_and_never_fall_back_to_memory(storage):
    with pytest.raises(ValueError):
        await create_checkpointer(storage)


async def test_memory_mode_uses_memory_saver_with_default_serde():
    from langgraph.checkpoint.memory import MemorySaver

    handle = await create_checkpointer(_storage(checkpointer="memory", mode="prod"))
    assert handle.kind == "memory"
    assert isinstance(handle.saver, MemorySaver)
    assert not _uses_cuga_serde(handle.saver)


async def test_local_mode_persists_to_a_private_sqlite_file(tmp_path):
    from langgraph.checkpoint.base import empty_checkpoint

    db = tmp_path / "nested" / "cp.db"
    config = {"configurable": {"thread_id": "t-1", "checkpoint_ns": ""}}
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {"messages": ["hello"]}
    checkpoint["channel_versions"] = {"messages": 1}

    first = await create_checkpointer(_storage(checkpoint_db_path=str(db)))
    assert first.kind == "sqlite"
    assert _uses_cuga_serde(first.saver)
    await first.saver.aput(config, checkpoint, {"source": "input", "step": 0}, {"messages": 1})
    await first.aclose()
    await first.aclose()  # idempotent

    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(db).st_mode) == 0o600

    second = await create_checkpointer(_storage(checkpoint_db_path=str(db)))
    try:
        loaded = await second.saver.aget_tuple(config)
        assert loaded.checkpoint["channel_values"] == {"messages": ["hello"]}
    finally:
        await second.aclose()


async def test_prod_mode_opens_pool_for_the_saver_and_closes_it_when_setup_fails(monkeypatch):
    import psycopg_pool
    from langgraph.checkpoint.postgres import aio as pg_aio
    from psycopg.rows import dict_row

    created: dict = {}

    class FakePool:
        def __init__(self, conninfo, **kwargs):
            created["kwargs"] = kwargs
            created["closed"] = False

        async def open(self, wait=False, timeout=30.0):
            pass

        async def close(self):
            created["closed"] = True

    class FakeSaver:
        fail = False

        def __init__(self, conn, serde=None):
            self.serde = serde

        async def setup(self):
            if FakeSaver.fail:
                raise RuntimeError("no DDL rights")

    monkeypatch.setattr(psycopg_pool, "AsyncConnectionPool", FakePool)
    monkeypatch.setattr(pg_aio, "AsyncPostgresSaver", FakeSaver)
    storage = _storage(mode="prod", postgres_url="postgresql://h/db", checkpoint_pool_max_size=7)

    handle = await create_checkpointer(storage)
    assert handle.kind == "postgres"
    assert _uses_cuga_serde(handle.saver)
    assert created["kwargs"] == {
        "min_size": 1,
        "max_size": 7,
        "kwargs": {"autocommit": True, "row_factory": dict_row, "prepare_threshold": None},
        "open": False,
    }
    await handle.aclose()
    assert created["closed"]

    FakeSaver.fail = True
    with pytest.raises(RuntimeError):
        await create_checkpointer(storage)
    assert created["closed"]
