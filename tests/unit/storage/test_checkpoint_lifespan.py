"""The server lifespan owns the checkpointer handle (issue #774).

``lifespan()`` opens the handle, runs ``_app_lifespan`` (the rest of startup and
shutdown) inside it, and closes the handle last. These tests patch
``_app_lifespan`` with a stub, so no browser, graph or knowledge stack starts.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from loguru import logger

from cuga.backend.server import main
from cuga.backend.server.conversation_history import ConversationHistoryDB
from cuga.backend.server.thread_lease import ThreadLease
from cuga.backend.storage.checkpoint import CheckpointerHandle
from cuga.backend.storage.relational.local import LocalRelationalStore

pytestmark = pytest.mark.unit

# The function-local ``from cuga.backend.storage.checkpoint import create_checkpointer``
# in ``main._open_checkpointer`` reads the package attribute at call time.
FACTORY = "cuga.backend.storage.checkpoint.create_checkpointer"
SECRET = "s3cr3t-pw"  # pragma: allowlist secret


def _storage(checkpointer: str = "memory", mode: str = "local") -> SimpleNamespace:
    return SimpleNamespace(
        checkpointer=checkpointer,
        mode=mode,
        postgres_url=f"postgresql://user:{SECRET}@127.0.0.1:1/cuga",  # pragma: allowlist secret
    )


def _fake_handle(events: list[str]) -> CheckpointerHandle:
    async def _close() -> None:
        events.append("aclose")

    handle = CheckpointerHandle(saver=MagicMock(name="saver"), kind="postgres", _close=_close)
    handle.aclose = AsyncMock(wraps=handle.aclose)
    return handle


def _stub_app_lifespan(events: list[str], *, fail_startup: bool = False):
    @asynccontextmanager
    async def _app_lifespan(app):
        events.append("startup")
        # Graphs are built during startup, so the handle must already exist.
        events.append(f"handle_set={main.app_state.checkpointer_handle is not None}")
        if fail_startup:
            raise RuntimeError("later startup step failed")
        yield
        events.append("shutdown")  # graph owners closed, background tasks cancelled

    return _app_lifespan


@pytest.fixture(autouse=True)
def _reset_handle():
    main.app_state.checkpointer_handle = None
    yield
    main.app_state.checkpointer_handle = None


@pytest.fixture
def log_lines():
    lines: list[str] = []
    sink_id = logger.add(lambda m: lines.append(m.record["level"].name + " " + m.record["message"]))
    yield lines
    logger.remove(sink_id)


async def test_handle_opened_before_startup_and_closed_once_after_shutdown():
    events: list[str] = []
    handle = _fake_handle(events)
    factory = AsyncMock(return_value=handle)

    with (
        patch.object(main, "_initialize_conversation_storage", AsyncMock()),
        patch(FACTORY, factory),
        patch.object(main, "settings", SimpleNamespace(storage=_storage("durable", "prod"))),
        patch.object(main, "_app_lifespan", _stub_app_lifespan(events)),
    ):
        async with main.lifespan(MagicMock()):
            assert main.app_state.checkpointer_handle is handle
            assert main.app_state.checkpointer is handle.saver
            handle.aclose.assert_not_awaited()

    factory.assert_awaited_once()
    handle.aclose.assert_awaited_once()
    assert events == ["startup", "handle_set=True", "shutdown", "aclose"]
    assert main.app_state.checkpointer_handle is None
    assert main.app_state.checkpointer is None


async def test_durable_creation_failure_fails_startup_without_leaking_url(log_lines):
    events: list[str] = []
    factory = AsyncMock(
        side_effect=OSError(
            f"connect to postgresql://user:{SECRET}@127.0.0.1:1 failed"  # pragma: allowlist secret
        )
    )

    with (
        patch.object(main, "_initialize_conversation_storage", AsyncMock()),
        patch(FACTORY, factory),
        patch.object(main, "settings", SimpleNamespace(storage=_storage("durable", "prod"))),
        patch.object(main, "_app_lifespan", _stub_app_lifespan(events)),
    ):
        with pytest.raises(OSError):
            async with main.lifespan(MagicMock()):
                pytest.fail("startup must not complete")

    assert events == [], "the app must not start on a broken durable checkpointer"
    assert main.app_state.checkpointer_handle is None  # no fallback to memory
    errors = [line for line in log_lines if line.startswith("ERROR")]
    assert len(errors) == 1
    assert "storage.checkpointer=durable" in errors[0]
    assert "OSError" in errors[0]
    assert all(SECRET not in line for line in log_lines)


async def test_later_startup_failure_still_closes_handle():
    events: list[str] = []
    handle = _fake_handle(events)

    with (
        patch.object(main, "_initialize_conversation_storage", AsyncMock()),
        patch(FACTORY, AsyncMock(return_value=handle)),
        patch.object(main, "settings", SimpleNamespace(storage=_storage("durable", "local"))),
        patch.object(main, "_app_lifespan", _stub_app_lifespan(events, fail_startup=True)),
    ):
        with pytest.raises(RuntimeError):
            async with main.lifespan(MagicMock()):
                pytest.fail("startup must not complete")

    handle.aclose.assert_awaited_once()
    assert main.app_state.checkpointer_handle is None


async def test_startup_published_agent_writes_to_the_shared_saver(monkeypatch):
    """The startup build of ``app_state.agent`` compiles on the lifespan saver.

    Judged by storage, not identity: a strict-msgpack compile wraps the saver in a
    ``with_allowlist`` clone, so ``graph.checkpointer is saver`` does not hold.
    """
    from langgraph.checkpoint.memory import MemorySaver

    from cuga.backend.cuga_graph.nodes.cuga_lite.providers.base import ToolProviderInterface
    from cuga.backend.llm.models import LLMManager

    saver = MemorySaver()
    main.app_state.checkpointer_handle = CheckpointerHandle(saver=saver, kind="memory")
    monkeypatch.setattr(main.app_state, "policy_system", None)
    tool_provider = MagicMock(spec=ToolProviderInterface)
    tool_provider.initialize = AsyncMock()
    tool_provider.get_apps = AsyncMock(return_value=[])

    with patch.object(LLMManager, "get_model", return_value=MagicMock()):
        agent = await main._build_published_default_agent(
            tool_provider=tool_provider, startup_config={}, startup_llm_cfg={}, startup_version=3
        )

    assert agent._checkpoint_config_version == "3"
    cfg = {"configurable": {"thread_id": "startup-thread"}}
    await agent.graph.aupdate_state(cfg, {"input": "published turn"}, as_node=agent.chat.chat_agent.name)
    stored = await saver.aget_tuple(cfg)
    assert stored is not None
    assert stored.checkpoint["channel_values"]["input"] == "published turn"


async def test_startup_supervisor_with_a_missing_sub_agent_still_builds(monkeypatch):
    """A roster ref to a deleted or draft-only agent is skipped, as before durable fingerprints."""
    from langgraph.checkpoint.memory import MemorySaver

    from cuga.backend.cuga_graph.nodes.cuga_lite.providers.base import ToolProviderInterface
    from cuga.backend.llm.models import LLMManager

    main.app_state.checkpointer_handle = CheckpointerHandle(saver=MemorySaver(), kind="memory")
    monkeypatch.setattr(main.app_state, "policy_system", None)
    monkeypatch.setattr("cuga.backend.server.config_store.load_config", AsyncMock(return_value=(None, None)))
    tool_provider = MagicMock(spec=ToolProviderInterface)
    tool_provider.initialize = AsyncMock()
    tool_provider.get_apps = AsyncMock(return_value=[])
    supervisor = {
        "agent": {"kind": "supervisor"},
        "supervisor": {"subAgents": [{"kind": "internal", "ref": "deleted-agent"}]},
    }

    with patch.object(LLMManager, "get_model", return_value=MagicMock()):
        agent = await main._build_published_default_agent(
            tool_provider=tool_provider, startup_config=supervisor, startup_llm_cfg={}, startup_version=2
        )

    assert agent._checkpoint_config_version == "2"
    assert agent._config_fingerprint is not None


@pytest.mark.parametrize("failure", [None, "conversation", "lease"])
@pytest.mark.parametrize("mode", ["memory", "durable"])
async def test_schemas_gate_startup_and_failure_closes_connections(monkeypatch, failure, mode):
    events = []

    async def conversation_schema():
        events.append("conversation")
        if failure == "conversation":
            raise RuntimeError("conversation DDL denied")

    async def lease_schema():
        events.append("lease")
        if failure == "lease":
            raise RuntimeError("lease DDL denied")

    handle = CheckpointerHandle(saver=MagicMock(), kind="memory", _close=AsyncMock())
    monkeypatch.setattr(
        main,
        "settings",
        SimpleNamespace(
            storage=SimpleNamespace(
                mode="local",
                checkpointer=mode,
                postgres_url="",
            )
        ),
    )
    monkeypatch.setattr(main.app_state, "checkpointer_handle", None)
    monkeypatch.setattr("cuga.backend.storage.checkpoint.create_checkpointer", AsyncMock(return_value=handle))
    monkeypatch.setattr(
        main, "get_conversation_db", lambda: SimpleNamespace(initialize_schema=conversation_schema)
    )
    monkeypatch.setattr(
        "cuga.backend.server.thread_lease.get_thread_lease",
        lambda: SimpleNamespace(initialize_schema=lease_schema),
    )
    close_stores = AsyncMock()
    monkeypatch.setattr(
        "cuga.backend.storage.facade.get_storage",
        lambda: SimpleNamespace(close_relational_stores=close_stores),
    )

    @asynccontextmanager
    async def app_lifespan(app):
        events.append("app-startup")
        yield
        events.append("app-shutdown")

    monkeypatch.setattr(main, "_app_lifespan", app_lifespan)
    if failure:
        with pytest.raises(RuntimeError, match="DDL denied"):
            async with main.lifespan(MagicMock()):
                pytest.fail("schema failure must prevent serving requests")
        assert "app-startup" not in events
        close_stores.assert_awaited_once()
        assert events == (["conversation"] if failure == "conversation" else ["conversation", "lease"])
    else:
        async with main.lifespan(MagicMock()):
            assert events == ["conversation", "lease", "app-startup"]
        assert events[-1] == "app-shutdown"
        close_stores.assert_not_awaited()
    handle._close.assert_awaited_once()
    assert main.app_state.checkpointer_handle is None


async def test_startup_upgrades_existing_conversation_schema_and_preserves_rows(tmp_path, monkeypatch):
    store = LocalRelationalStore(str(tmp_path / "conversation.db"))
    db, lease = ConversationHistoryDB(), ThreadLease()
    monkeypatch.setattr(db, "_get_store", lambda: store)
    monkeypatch.setattr(lease, "_get_store", lambda: store)
    monkeypatch.setattr(main, "get_conversation_db", lambda: db)
    monkeypatch.setattr("cuga.backend.server.thread_lease.get_thread_lease", lambda: lease)
    try:
        await store.execute("""CREATE TABLE runtime_thread_owners (
            tenant_id TEXT NOT NULL, instance_id TEXT NOT NULL, thread_id TEXT NOT NULL,
            user_id TEXT NOT NULL, agent_id TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, instance_id, thread_id))""")
        await store.execute(
            "INSERT INTO runtime_thread_owners VALUES ('tenant', 'instance', 'old', 'user', 'agent', '2026-01-01')"
        )
        await store.commit()
        await main._initialize_conversation_storage()
        # Safe to repeat; later requests need no new migration step.
        await main._initialize_conversation_storage()
        row = await store.fetchone("SELECT * FROM runtime_thread_owners WHERE thread_id = 'old'")
        assert row["user_id"] == "user"
        assert row["status"] == "legacy"
        assert row["last_turn_at"] != ""  # retention for existing rows starts at the upgrade
        columns = await store.fetchall("PRAGMA table_info(thread_leases)")
        assert {column["name"] for column in columns} >= {"holder_id", "expires_at", "stop_requested"}
    finally:
        await store.close()
