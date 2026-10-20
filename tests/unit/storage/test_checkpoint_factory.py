"""``create_checkpointer`` / ``CheckpointerHandle`` (issue #774, W02.1).

The memory and SQLite branches run for real (SQLite against ``tmp_path``). The
Postgres branch patches ``psycopg_pool.AsyncConnectionPool`` and
``langgraph.checkpoint.postgres.aio.AsyncPostgresSaver``, the modules the
factory imports them from; the real-database test is
``test_checkpoint_factory_pg.py`` (``pgvector``).
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import stat
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from dynaconf import Dynaconf
from loguru import logger

from cuga.backend.storage.checkpoint import CheckpointerHandle, create_checkpointer
from cuga.backend.storage.checkpoint import factory as factory_mod

pytestmark = pytest.mark.unit

_AGENT_STATE_PAIR = ("cuga.backend.cuga_graph.state.agent_state", "AgentState")
_MISSING_URL = "storage.postgres_url is required when storage.mode=prod"
_SECRET = "s3cr3t-Pa55word"
_PG_URL = f"postgresql://cuga_user:{_SECRET}@db.internal.example:6543/cuga"  # pragma: allowlist secret

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")


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


def _is_cuga_allowlist(serde) -> bool:
    allowed = getattr(serde, "_allowed_msgpack_modules", None)
    return isinstance(allowed, (set, frozenset)) and _AGENT_STATE_PAIR in allowed


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


async def _round_trip(saver, thread_id: str = "t-1") -> None:
    from langgraph.checkpoint.base import empty_checkpoint

    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {"messages": ["hello"], "variables": {"n": 1}}
    checkpoint["channel_versions"] = {"messages": 1, "variables": 1}
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}

    saved = await saver.aput(
        config, checkpoint, {"source": "input", "step": 0}, {"messages": 1, "variables": 1}
    )
    loaded = await saver.aget_tuple({"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}})

    assert loaded is not None
    assert loaded.config["configurable"]["checkpoint_id"] == saved["configurable"]["checkpoint_id"]
    assert loaded.checkpoint["id"] == checkpoint["id"]
    assert loaded.checkpoint["channel_values"] == {"messages": ["hello"], "variables": {"n": 1}}


@pytest.fixture
def log_text():
    """Collect loguru output (the factory logs with loguru, not stdlib logging)."""
    lines: list[str] = []
    sink_id = logger.add(lambda msg: lines.append(str(msg)), level="DEBUG", format="{message}")
    yield lines
    logger.remove(sink_id)


# ---------------------------------------------------------------------------
# Settings validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["Durable", "sqlite", "postgres", "", None, "memory "])
async def test_unknown_checkpointer_value_raises_and_never_falls_back(value):
    with pytest.raises(ValueError, match="storage.checkpointer must be one of"):
        await create_checkpointer(_storage(checkpointer=value))


async def test_missing_checkpointer_attribute_raises():
    with pytest.raises(ValueError, match="storage.checkpointer"):
        await create_checkpointer(SimpleNamespace(mode="local"))


async def test_unknown_mode_raises():
    with pytest.raises(ValueError, match="storage.mode must be one of"):
        await create_checkpointer(_storage(mode="cloud"))


@pytest.mark.parametrize("url", ["", None])
async def test_prod_without_url_raises_exact_error(url):
    with pytest.raises(ValueError) as excinfo:
        await create_checkpointer(_storage(mode="prod", postgres_url=url))

    assert str(excinfo.value) == _MISSING_URL


async def test_explicit_postgres_without_url_raises_exact_error():
    with pytest.raises(ValueError) as excinfo:
        await factory_mod._create_postgres("")

    assert str(excinfo.value) == _MISSING_URL


@pytest.mark.parametrize(
    ("min_size", "max_size", "match"),
    [
        (0, 10, "checkpoint_pool_min_size must be >= 1"),
        (1, 0, "checkpoint_pool_max_size must be >= 1"),
        (0, 0, "checkpoint_pool_min_size must be >= 1"),
        (5, 2, r"checkpoint_pool_min_size \(5\) must be <= storage.checkpoint_pool_max_size \(2\)"),
        ("2", 10, "checkpoint_pool_min_size must be an integer"),
        (True, 10, "checkpoint_pool_min_size must be an integer"),
    ],
)
async def test_invalid_pool_sizes_raise_before_any_pool_is_created(monkeypatch, min_size, max_size, match):
    import psycopg_pool

    def _must_not_be_called(*args, **kwargs):
        raise AssertionError("pool created despite invalid sizes")

    monkeypatch.setattr(psycopg_pool, "AsyncConnectionPool", _must_not_be_called)

    with pytest.raises(ValueError, match=match):
        await create_checkpointer(
            _storage(
                mode="prod",
                postgres_url=_PG_URL,
                checkpoint_pool_min_size=min_size,
                checkpoint_pool_max_size=max_size,
            )
        )


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------


async def test_memory_branch_uses_memory_saver_with_default_serde():
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    handle = await create_checkpointer(_storage(checkpointer="memory", mode="prod", postgres_url=""))

    assert isinstance(handle, CheckpointerHandle)
    assert handle.kind == "memory"
    assert isinstance(handle.saver, MemorySaver)
    assert not _is_cuga_allowlist(handle.saver.serde)
    assert type(handle.saver.serde) is JsonPlusSerializer
    assert handle.saver.serde._allowed_msgpack_modules == JsonPlusSerializer()._allowed_msgpack_modules
    await _round_trip(handle.saver)
    assert await handle.ping() is True

    await handle.aclose()
    await handle.aclose()
    assert handle.closed
    assert await handle.ping() is False


async def test_memory_branch_honours_explicit_serde():
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    serde = JsonPlusSerializer()
    handle = await create_checkpointer(_storage(checkpointer="memory"), serde=serde)

    assert handle.saver.serde is serde


# ---------------------------------------------------------------------------
# SQLite (mode = local)
# ---------------------------------------------------------------------------


@posix_only
async def test_local_branch_creates_private_wal_file_and_round_trips(tmp_path):
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    db = tmp_path / "nested" / "cp.db"
    handle = await create_checkpointer(_storage(checkpoint_db_path=str(db)))
    try:
        assert handle.kind == "sqlite"
        assert isinstance(handle.saver, AsyncSqliteSaver)
        assert _is_cuga_allowlist(handle.saver.serde)
        assert db.is_file()
        assert _mode(db) == 0o600

        async with handle.saver.conn.execute("PRAGMA journal_mode") as cur:
            assert (await cur.fetchone())[0].lower() == "wal"

        # setup() is idempotent on the same saver.
        await handle.saver.setup()
        handle.saver.is_setup = False
        await handle.saver.setup()

        await _round_trip(handle.saver)
        wal = db.with_name(db.name + "-wal")
        if wal.exists():
            assert _mode(wal) == 0o600
        assert await handle.ping() is True
    finally:
        await handle.aclose()

    # WAL is persistent in the file header.
    with sqlite3.connect(db) as raw:
        assert raw.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


async def test_local_branch_second_handle_on_same_file_reads_first(tmp_path):
    db = tmp_path / "cp.db"

    first = await create_checkpointer(_storage(checkpoint_db_path=str(db)))
    await _round_trip(first.saver, thread_id="shared")
    await first.aclose()

    second = await create_checkpointer(_storage(checkpoint_db_path=str(db)))
    try:
        loaded = await second.saver.aget_tuple({"configurable": {"thread_id": "shared", "checkpoint_ns": ""}})
        assert loaded is not None
        assert loaded.checkpoint["channel_values"]["messages"] == ["hello"]
    finally:
        await second.aclose()


@posix_only
async def test_local_branch_tightens_existing_file_permissions(tmp_path):
    db = tmp_path / "cp.db"
    db.touch(mode=0o644)
    db.chmod(0o644)

    handle = await create_checkpointer(_storage(checkpoint_db_path=str(db)))
    await handle.aclose()

    assert _mode(db) == 0o600


async def test_local_branch_default_path_and_dynaconf_box(tmp_path, monkeypatch):
    import cuga.config as cuga_config

    dbs = tmp_path / "dbs"
    monkeypatch.setattr(cuga_config, "DBS_DIR", str(dbs))
    settings_file = tmp_path / "settings.toml"
    settings_file.write_text('[storage]\nmode = "local"\ncheckpointer = "durable"\n')
    storage = Dynaconf(
        settings_files=[str(settings_file)], envvar_prefix="CUGA_TEST_CHECKPOINT_FACTORY"
    ).storage

    handle = await create_checkpointer(storage)
    try:
        assert handle.kind == "sqlite"
        assert (dbs / "checkpoints.db").is_file()
    finally:
        await handle.aclose()


async def test_local_branch_honours_explicit_serde(tmp_path):
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    serde = JsonPlusSerializer()
    handle = await create_checkpointer(_storage(checkpoint_db_path=str(tmp_path / "cp.db")), serde=serde)
    try:
        assert handle.saver.serde is serde
    finally:
        await handle.aclose()


async def test_sqlite_aclose_is_idempotent_and_closes_connection(tmp_path):
    handle = await factory_mod._create_sqlite(tmp_path / "cp.db")
    conn = handle.saver.conn

    await handle.aclose()
    await handle.aclose()

    assert handle.closed
    assert conn._connection is None
    assert await handle.ping() is False


async def test_sqlite_handle_is_an_async_context_manager(tmp_path):
    async with await factory_mod._create_sqlite(tmp_path / "cp.db") as handle:
        conn = handle.saver.conn
        assert await handle.ping() is True

    assert handle.closed
    assert conn._connection is None


async def test_sqlite_setup_failure_closes_connection(tmp_path, monkeypatch):
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    opened = []
    real_connect = aiosqlite.connect

    def _spy_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        opened.append(conn)
        return conn

    async def _broken_setup(self):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(aiosqlite, "connect", _spy_connect)
    monkeypatch.setattr(AsyncSqliteSaver, "setup", _broken_setup)

    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        await create_checkpointer(_storage(checkpoint_db_path=str(tmp_path / "cp.db")))

    assert len(opened) == 1
    assert opened[0]._connection is None


async def test_sqlite_close_failure_after_setup_failure_keeps_original_error(tmp_path, monkeypatch):
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    real_close = aiosqlite.Connection.close
    close_calls = []

    async def _close_then_fail(self):
        close_calls.append(self)
        await real_close(self)  # really release the worker thread
        raise OSError("close broke")

    async def _broken_setup(self):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(aiosqlite.Connection, "close", _close_then_fail)
    monkeypatch.setattr(AsyncSqliteSaver, "setup", _broken_setup)

    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        await create_checkpointer(_storage(checkpoint_db_path=str(tmp_path / "cp.db")))

    assert len(close_calls) == 1


async def test_sqlite_cancelled_setup_propagates_and_closes_connection(tmp_path, monkeypatch):
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    opened = []
    real_connect = aiosqlite.connect

    def _spy_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        opened.append(conn)
        return conn

    async def _cancelled_setup(self):
        raise asyncio.CancelledError()

    monkeypatch.setattr(aiosqlite, "connect", _spy_connect)
    monkeypatch.setattr(AsyncSqliteSaver, "setup", _cancelled_setup)

    with pytest.raises(asyncio.CancelledError):
        await create_checkpointer(_storage(checkpoint_db_path=str(tmp_path / "cp.db")))

    assert len(opened) == 1
    assert opened[0]._connection is None


# ---------------------------------------------------------------------------
# Postgres (mode = prod), patched
# ---------------------------------------------------------------------------


class _FakeConn:
    def __init__(self, events):
        self._events = events

    async def execute(self, sql):
        self._events.append(("execute", sql))


def _install_fake_postgres(monkeypatch, *, fail_open=None, fail_setup=None, fail_close=None, probe_delay=0.0):
    """Patch the pool and saver where the factory imports them from."""
    import psycopg_pool
    from langgraph.checkpoint.postgres import aio as pg_aio

    events: list = []
    created: dict = {}

    class FakePool:
        def __init__(self, conninfo, **kwargs):
            events.append("pool.__init__")
            created["pool_args"] = (conninfo,)
            created["pool_kwargs"] = kwargs
            created["pool"] = self

        async def open(self, wait=False, timeout=30.0):
            events.append(("pool.open", wait))
            if fail_open:
                raise fail_open

        async def close(self, timeout=5.0):
            events.append("pool.close")
            if fail_close:
                raise fail_close

        @asynccontextmanager
        async def connection(self, timeout=None):
            if probe_delay:
                await asyncio.sleep(probe_delay)
            yield _FakeConn(events)

    class FakeSaver:
        def __init__(self, conn, pipe=None, serde=None):
            events.append("saver.__init__")
            created["saver_conn"] = conn
            self.conn = conn
            self.serde = serde

        async def setup(self):
            events.append("saver.setup")
            if fail_setup:
                raise fail_setup

    monkeypatch.setattr(psycopg_pool, "AsyncConnectionPool", FakePool)
    monkeypatch.setattr(pg_aio, "AsyncPostgresSaver", FakeSaver)
    return events, created, FakeSaver


async def test_prod_branch_pool_kwargs_and_open_setup_close_order(monkeypatch):
    from psycopg.rows import dict_row

    events, created, fake_saver_cls = _install_fake_postgres(monkeypatch)

    handle = await create_checkpointer(
        _storage(mode="prod", postgres_url=_PG_URL, checkpoint_pool_min_size=2, checkpoint_pool_max_size=7)
    )

    assert handle.kind == "postgres"
    assert isinstance(handle.saver, fake_saver_cls)
    assert created["pool_args"] == (_PG_URL,)
    assert created["pool_kwargs"] == {
        "min_size": 2,
        "max_size": 7,
        "kwargs": {"autocommit": True, "row_factory": dict_row, "prepare_threshold": 0},
        "open": False,
    }
    assert created["saver_conn"] is created["pool"]
    assert _is_cuga_allowlist(handle.saver.serde)
    assert events == ["pool.__init__", ("pool.open", True), "saver.__init__", "saver.setup"]

    assert await handle.ping() is True
    assert events[-1] == ("execute", "SELECT 1")

    await handle.aclose()
    await handle.aclose()
    assert events.count("pool.close") == 1
    assert await handle.ping() is False


async def test_prod_branch_honours_explicit_serde(monkeypatch):
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    _install_fake_postgres(monkeypatch)
    serde = JsonPlusSerializer()

    handle = await create_checkpointer(_storage(mode="prod", postgres_url=_PG_URL), serde=serde)

    assert handle.saver.serde is serde
    await handle.aclose()


async def test_prod_setup_failure_closes_pool(monkeypatch):
    events, _, _ = _install_fake_postgres(monkeypatch, fail_setup=PermissionError("no DDL rights"))

    with pytest.raises(PermissionError, match="no DDL rights"):
        await create_checkpointer(_storage(mode="prod", postgres_url=_PG_URL))

    assert events == ["pool.__init__", ("pool.open", True), "saver.__init__", "saver.setup", "pool.close"]


async def test_prod_open_failure_closes_pool(monkeypatch):
    events, _, _ = _install_fake_postgres(
        monkeypatch, fail_open=TimeoutError("pool initialization incomplete")
    )

    with pytest.raises(TimeoutError):
        await create_checkpointer(_storage(mode="prod", postgres_url=_PG_URL))

    assert events == ["pool.__init__", ("pool.open", True), "pool.close"]


async def test_prod_close_failure_after_setup_failure_keeps_original_error(monkeypatch):
    events, _, _ = _install_fake_postgres(
        monkeypatch, fail_setup=PermissionError("no DDL rights"), fail_close=OSError("close broke")
    )

    with pytest.raises(PermissionError, match="no DDL rights"):
        await create_checkpointer(_storage(mode="prod", postgres_url=_PG_URL))

    assert events[-1] == "pool.close"


async def test_prod_cancelled_setup_propagates_and_closes_pool(monkeypatch):
    events, _, _ = _install_fake_postgres(monkeypatch, fail_setup=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await create_checkpointer(_storage(mode="prod", postgres_url=_PG_URL))

    assert events[-1] == "pool.close"


async def test_prod_ping_times_out_without_raising(monkeypatch):
    _install_fake_postgres(monkeypatch, probe_delay=5.0)
    handle = await create_checkpointer(_storage(mode="prod", postgres_url=_PG_URL))

    assert await handle.ping(timeout=0.05) is False
    await handle.aclose()


# ---------------------------------------------------------------------------
# Credential redaction
# ---------------------------------------------------------------------------


async def test_logs_show_host_but_never_url_or_password(monkeypatch, log_text):
    _install_fake_postgres(monkeypatch)

    handle = await create_checkpointer(_storage(mode="prod", postgres_url=_PG_URL))
    await handle.aclose()

    text = "\n".join(log_text)
    assert "kind=postgres" in text
    assert "db.internal.example:6543" in text
    assert _SECRET not in text
    assert "cuga_user" not in text
    assert _PG_URL not in text


async def test_failure_logs_never_contain_password(monkeypatch, log_text):
    # The exception text itself carries the URL: only its type may be logged.
    _install_fake_postgres(monkeypatch, fail_setup=RuntimeError(f"cannot connect to {_PG_URL}"))

    with pytest.raises(RuntimeError):
        await create_checkpointer(_storage(mode="prod", postgres_url=_PG_URL))

    text = "\n".join(log_text)
    assert "db.internal.example:6543" in text
    assert _SECRET not in text
    assert "cuga_user" not in text


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (_PG_URL, "db.internal.example:6543"),
        (f"postgres://u:{_SECRET}@pg/db", "pg"),
        (f"host=pg.example port=5433 user=u password={_SECRET} dbname=cuga", "pg.example:5433"),
        (f"user=u password={_SECRET}", "localhost"),
        ("host='unterminated", "<unparsed>"),
    ],
)
def test_redacted_host(url, expected):
    redacted = factory_mod._redacted_host(url)

    assert redacted == expected
    assert _SECRET not in redacted
