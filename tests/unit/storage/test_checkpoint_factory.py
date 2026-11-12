"""Checkpointer settings, DB path resolution and ``create_checkpointer`` (issue #774).

Memory and SQLite run for real. The ``prod`` unit test patches the pool and saver
classes where the factory imports them; ``test_postgres_round_trip`` runs against a
real database in the ``pgvector-integration`` CI job and skips when
``CUGA_KNOWLEDGE_PGVECTOR_CONNECTION_STRING`` is unset. Local run::

    docker run --rm -d --name cuga-pgvector-test \\
        -e POSTGRES_PASSWORD=cuga -e POSTGRES_USER=cuga \\
        -e POSTGRES_DB=cuga_test -p 5432:5432 \\
        pgvector/pgvector:0.8.0-pg16
    export CUGA_KNOWLEDGE_PGVECTOR_CONNECTION_STRING=\\
        postgresql://cuga:cuga@localhost:5432/cuga_test  # pragma: allowlist secret
    uv run pytest tests/unit/storage/test_checkpoint_factory.py -m pgvector -v
"""

from __future__ import annotations

import os
import stat
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from dynaconf import Dynaconf, ValidationError

from cuga.backend.storage.checkpoint import create_checkpointer
from cuga.backend.storage.checkpoint.paths import resolve_checkpoint_db_path

# The Postgres round trip also carries ``pgvector``, which the unit jobs exclude.
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


def _dynaconf_storage(tmp_path, storage_toml: str, validators=()):
    """A private ``Dynaconf`` ``storage`` section, so the global settings are never touched."""
    settings_file = tmp_path / "settings.toml"
    settings_file.write_text(f'[storage]\nmode = "local"\n{storage_toml}\n')
    return Dynaconf(
        settings_files=[str(settings_file)],
        envvar_prefix="CUGA_TEST_CHECKPOINT",
        validators=list(validators),
    )


def _uses_cuga_serde(saver) -> bool:
    return _AGENT_STATE_PAIR in (getattr(saver.serde, "_allowed_msgpack_modules", None) or ())


def test_settings_validator_rejects_unknown_checkpointer(tmp_path):
    from cuga.config import validators

    checkpointer_validators = [v for v in validators if v.names and v.names[0] == "storage.checkpointer"]
    isolated = _dynaconf_storage(tmp_path, 'checkpointer = "foo"', checkpointer_validators)
    with pytest.raises(ValidationError, match="storage.checkpointer"):
        isolated.validators.validate_all()


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("", "{dbs}/checkpoints.db"),
        ("   ", "{dbs}/checkpoints.db"),
        ("state/cp.db", "{dbs}/state/cp.db"),
        ("{tmp}/elsewhere/cp.db", "{tmp}/elsewhere/cp.db"),
        ("~/cp.db", "{home}/cp.db"),
    ],
    ids=["unset", "blank", "relative-to-dbs-dir", "absolute", "home"],
)
def test_db_path_resolves_and_creates_its_parent(tmp_path, monkeypatch, configured, expected):
    import cuga.config as cuga_config

    dbs_dir = tmp_path / "dbs"
    home = tmp_path / "home"
    monkeypatch.setattr(cuga_config, "DBS_DIR", str(dbs_dir))
    monkeypatch.setenv("HOME", str(home))
    fmt = {"dbs": dbs_dir, "tmp": tmp_path, "home": home}

    storage = _dynaconf_storage(tmp_path, f'checkpoint_db_path = "{configured.format(**fmt)}"').storage
    path = resolve_checkpoint_db_path(storage)

    assert path == Path(expected.format(**fmt))
    assert path.parent.is_dir()


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


@pytest.mark.pgvector
async def test_postgres_round_trip():
    from langgraph.checkpoint.base import empty_checkpoint
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    dsn = os.environ.get("CUGA_KNOWLEDGE_PGVECTOR_CONNECTION_STRING")
    if not dsn:
        pytest.skip("CUGA_KNOWLEDGE_PGVECTOR_CONNECTION_STRING not set")
    thread_id = f"w02-1-{uuid.uuid4().hex}"
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}

    handle = await create_checkpointer(_storage(mode="prod", postgres_url=dsn, checkpoint_pool_max_size=2))
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
    second = await create_checkpointer(_storage(mode="prod", postgres_url=dsn, checkpoint_pool_max_size=2))
    try:
        assert await second.saver.aget_tuple(config) is None
    finally:
        await second.aclose()
