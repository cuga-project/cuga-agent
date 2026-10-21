"""Tests for ``cuga.checkpointers.sqlite`` / ``postgres`` (#774, W15.3).

The Postgres tests patch ``psycopg_pool.AsyncConnectionPool`` and
``langgraph.checkpoint.postgres.aio.AsyncPostgresSaver`` at their source
modules, which the factory imports them from at call time.
"""

import subprocess
import sys
import textwrap
from unittest.mock import MagicMock

import pytest
from langgraph.checkpoint.base import empty_checkpoint

import cuga
from cuga import CugaAgent
from cuga.backend.storage.checkpoint.factory import CheckpointerHandle

pytestmark = pytest.mark.unit

_PG_URL = "postgresql://user:secret@db.example:5432/cuga"


def _run_isolated(code: str) -> None:
    # A fresh interpreter: other tests' imports would otherwise pollute sys.modules.
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_import_cuga_and_accessing_checkpointers_stay_lazy(tmp_path):
    db = tmp_path / "cp.db"
    _run_isolated(
        f"""
        import asyncio, sys

        heavy = ("aiosqlite", "psycopg_pool", "langgraph.checkpoint.sqlite", "cuga.config")

        import cuga
        assert "cuga.checkpointers" not in sys.modules
        assert not [m for m in heavy if m in sys.modules], [m for m in heavy if m in sys.modules]

        cps = cuga.checkpointers
        assert cps is sys.modules["cuga.checkpointers"]
        opener = cps.sqlite({str(db)!r})
        cps.postgres("postgresql://u@h/db")
        assert not [m for m in heavy if m in sys.modules], [m for m in heavy if m in sys.modules]

        async def main():
            async with opener as cp:
                assert cp.kind == "sqlite"

        asyncio.run(main())
        assert "aiosqlite" in sys.modules
        """
    )


def test_checkpointers_is_a_lazy_export_of_cuga():
    import cuga.checkpointers as module

    assert cuga.checkpointers is module
    assert "checkpointers" in cuga.__all__
    assert cuga._import_map["checkpointers"] == ("cuga.checkpointers", None)


async def _assert_closed(cp: CheckpointerHandle) -> None:
    assert cp.closed
    assert await cp.ping() is False
    with pytest.raises(ValueError, match="no active connection"):
        await cp.saver.conn.execute("SELECT 1")


class TestSqlite:
    @pytest.mark.asyncio
    async def test_context_manager_opens_and_closes(self, tmp_path):
        async with cuga.checkpointers.sqlite(tmp_path / "state" / "cp.db") as cp:
            assert isinstance(cp, CheckpointerHandle)
            assert cp.kind == "sqlite"
            assert await cp.ping() is True

        await _assert_closed(cp)
        assert (tmp_path / "state" / "cp.db").exists()

    @pytest.mark.asyncio
    async def test_context_manager_closes_on_exception(self, tmp_path):
        with pytest.raises(RuntimeError, match="boom"):
            async with cuga.checkpointers.sqlite(str(tmp_path / "cp.db")) as cp:
                raise RuntimeError("boom")

        await _assert_closed(cp)

    @pytest.mark.asyncio
    async def test_await_returns_an_open_handle_the_caller_closes(self, tmp_path):
        cp = await cuga.checkpointers.sqlite(tmp_path / "cp.db")
        try:
            assert isinstance(cp, CheckpointerHandle)
            assert await cp.ping() is True
        finally:
            await cp.aclose()

        await _assert_closed(cp)

    @pytest.mark.asyncio
    async def test_uses_the_cuga_allowlist_serde_by_default(self, tmp_path):
        from cuga.backend.storage.checkpoint.serde import CUGA_ALLOWED_MSGPACK_TYPES

        async with cuga.checkpointers.sqlite(tmp_path / "cp.db") as cp:
            allowed = cp.saver.serde._allowed_msgpack_modules
            assert set(CUGA_ALLOWED_MSGPACK_TYPES) <= set(allowed)

    @pytest.mark.asyncio
    async def test_serde_is_passed_through(self, tmp_path):
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

        serde = JsonPlusSerializer()
        async with cuga.checkpointers.sqlite(tmp_path / "cp.db", serde=serde) as cp:
            assert cp.saver.serde is serde

    @pytest.mark.asyncio
    async def test_opener_is_single_use(self, tmp_path):
        opener = cuga.checkpointers.sqlite(tmp_path / "cp.db")
        async with opener:
            pass

        with pytest.raises(RuntimeError, match="only be entered or awaited once"):
            await opener
        with pytest.raises(RuntimeError, match="only be entered or awaited once"):
            async with opener:
                pass

    @pytest.mark.asyncio
    async def test_cuga_agent_compiles_and_persists_with_it(self, tmp_path):
        db = tmp_path / "cp.db"
        config = {"configurable": {"thread_id": "t-1", "checkpoint_ns": ""}}

        async with cuga.checkpointers.sqlite(db) as cp:
            agent = CugaAgent(
                tools=[],
                model=MagicMock(),
                auto_load_policies=False,
                reset_policy_storage=True,
                checkpointer=cp.saver,
            )
            graph = agent.graph  # compiles; no LLM call
            written = await graph.checkpointer.aput(
                config, empty_checkpoint(), {"source": "input", "step": -1, "parents": {}}, {}
            )

        # A new connection to the same file sees the checkpoint.
        async with cuga.checkpointers.sqlite(db) as cp2:
            stored = await cp2.saver.aget_tuple(written)
            assert stored is not None
            assert stored.checkpoint["id"] == written["configurable"]["checkpoint_id"]


def _install_fake_postgres(monkeypatch):
    import psycopg_pool
    from langgraph.checkpoint.postgres import aio as pg_aio

    events: list = []
    created: dict = {}

    class FakePool:
        def __init__(self, conninfo, **kwargs):
            created["conninfo"] = conninfo
            created["pool_kwargs"] = kwargs
            created["pool"] = self

        async def open(self, wait=False, timeout=30.0):
            events.append("pool.open")

        async def close(self, timeout=5.0):
            events.append("pool.close")

    class FakeSaver:
        def __init__(self, conn, pipe=None, serde=None):
            created["saver_conn"] = conn
            self.serde = serde

        async def setup(self):
            events.append("saver.setup")

    monkeypatch.setattr(psycopg_pool, "AsyncConnectionPool", FakePool)
    monkeypatch.setattr(pg_aio, "AsyncPostgresSaver", FakeSaver)
    return events, created, FakeSaver


class TestPostgres:
    @pytest.mark.asyncio
    async def test_context_manager_forwards_pool_sizes_and_closes(self, monkeypatch):
        events, created, fake_saver_cls = _install_fake_postgres(monkeypatch)

        async with cuga.checkpointers.postgres(_PG_URL, min_size=2, max_size=7) as cp:
            assert cp.kind == "postgres"
            assert isinstance(cp.saver, fake_saver_cls)
            assert created["saver_conn"] is created["pool"]
            assert events == ["pool.open", "saver.setup"]

        assert created["conninfo"] == _PG_URL
        assert created["pool_kwargs"]["min_size"] == 2
        assert created["pool_kwargs"]["max_size"] == 7
        assert events == ["pool.open", "saver.setup", "pool.close"]
        assert cp.closed

    @pytest.mark.asyncio
    async def test_defaults_are_one_to_five(self, monkeypatch):
        _, created, _ = _install_fake_postgres(monkeypatch)

        async with cuga.checkpointers.postgres(_PG_URL):
            pass

        assert (created["pool_kwargs"]["min_size"], created["pool_kwargs"]["max_size"]) == (1, 5)

    @pytest.mark.asyncio
    async def test_closes_on_exception(self, monkeypatch):
        events, _, _ = _install_fake_postgres(monkeypatch)

        with pytest.raises(RuntimeError, match="boom"):
            async with cuga.checkpointers.postgres(_PG_URL):
                raise RuntimeError("boom")

        assert events[-1] == "pool.close"

    @pytest.mark.asyncio
    async def test_await_form_and_serde_passthrough(self, monkeypatch):
        events, _, _ = _install_fake_postgres(monkeypatch)
        serde = object()

        cp = await cuga.checkpointers.postgres(_PG_URL, serde=serde)
        assert cp.saver.serde is serde
        assert "pool.close" not in events
        await cp.aclose()
        assert events[-1] == "pool.close"


# ---------------------------------------------------------------------------
# Argument validation happens at call time, before anything is opened
# ---------------------------------------------------------------------------

_SQLITE_BAD_ARGS = [
    ("", ValueError, r"cuga\.checkpointers\.sqlite\(\): path must not be empty"),
    ("   ", ValueError, r"cuga\.checkpointers\.sqlite\(\): path must not be empty"),
    (None, TypeError, r"cuga\.checkpointers\.sqlite\(\): path must be a str or os\.PathLike"),
    (b"cp.db", TypeError, r"cuga\.checkpointers\.sqlite\(\): path must be a str or os\.PathLike"),
    (123, TypeError, r"cuga\.checkpointers\.sqlite\(\): path must be a str or os\.PathLike"),
]

_PG = r"cuga\.checkpointers\.postgres\(\): "
_POSTGRES_BAD_ARGS = [
    ({"url": ""}, ValueError, _PG + "url must not be empty"),
    ({"url": "  "}, ValueError, _PG + "url must not be empty"),
    ({"url": None}, TypeError, _PG + "url must be a str"),
    ({"url": _PG_URL, "min_size": 0}, ValueError, _PG + "min_size must be >= 1"),
    ({"url": _PG_URL, "max_size": 0}, ValueError, _PG + "max_size must be >= 1"),
    ({"url": _PG_URL, "min_size": -1}, ValueError, _PG + "min_size must be >= 1"),
    ({"url": _PG_URL, "min_size": True}, TypeError, _PG + "min_size must be an int"),
    ({"url": _PG_URL, "max_size": 2.0}, TypeError, _PG + "max_size must be an int"),
    ({"url": _PG_URL, "max_size": "5"}, TypeError, _PG + "max_size must be an int"),
    (
        {"url": _PG_URL, "min_size": 6, "max_size": 5},
        ValueError,
        _PG + r"min_size \(6\) must be <= max_size \(5\)",
    ),
]


@pytest.fixture
def _factory_must_not_run(monkeypatch):
    from cuga.backend.storage.checkpoint import factory

    async def _must_not_be_called(*args, **kwargs):
        raise AssertionError("the factory must not run for invalid arguments")

    monkeypatch.setattr(factory, "_create_sqlite", _must_not_be_called)
    monkeypatch.setattr(factory, "_create_postgres", _must_not_be_called)


@pytest.mark.usefixtures("_factory_must_not_run")
@pytest.mark.parametrize(("path", "exc", "match"), _SQLITE_BAD_ARGS)
def test_sqlite_rejects_bad_path_at_call_time(path, exc, match):
    with pytest.raises(exc, match=match) as info:
        cuga.checkpointers.sqlite(path)
    assert "storage." not in str(info.value)


@pytest.mark.usefixtures("_factory_must_not_run")
@pytest.mark.parametrize(("kwargs", "exc", "match"), _POSTGRES_BAD_ARGS)
def test_postgres_rejects_bad_args_at_call_time(kwargs, exc, match):
    kwargs = dict(kwargs)
    url = kwargs.pop("url")
    with pytest.raises(exc, match=match) as info:
        cuga.checkpointers.postgres(url, **kwargs)
    assert "storage." not in str(info.value)


def test_invalid_args_import_no_backend(tmp_path):
    _run_isolated(
        """
        import sys

        import cuga

        for call in (
            lambda: cuga.checkpointers.sqlite(""),
            lambda: cuga.checkpointers.postgres(""),
            lambda: cuga.checkpointers.postgres("postgresql://u@h/db", min_size=6, max_size=5),
        ):
            try:
                call()
            except ValueError:
                pass
            else:
                raise AssertionError("expected ValueError")

        heavy = ("aiosqlite", "psycopg_pool", "psycopg", "langgraph.checkpoint.sqlite", "cuga.config")
        assert not [m for m in heavy if m in sys.modules], [m for m in heavy if m in sys.modules]
        """
    )
