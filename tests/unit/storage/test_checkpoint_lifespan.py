"""The server lifespan owns the checkpointer handle (issue #774, W02.3).

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
from cuga.backend.storage.checkpoint import CheckpointerHandle

pytestmark = pytest.mark.unit

# The function-local ``from cuga.backend.storage.checkpoint import create_checkpointer``
# in ``main._open_checkpointer`` reads the package attribute at call time.
FACTORY = "cuga.backend.storage.checkpoint.create_checkpointer"
SECRET = "s3cr3t-pw"


def _storage(checkpointer: str = "memory", mode: str = "local") -> SimpleNamespace:
    return SimpleNamespace(
        checkpointer=checkpointer,
        mode=mode,
        postgres_url=f"postgresql://user:{SECRET}@127.0.0.1:1/cuga",
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
    factory = AsyncMock(side_effect=OSError(f"connect to postgresql://user:{SECRET}@127.0.0.1:1 failed"))

    with (
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
    assert "storage.postgres_url" in errors[0]  # actionable hint
    assert all(SECRET not in line for line in log_lines)


async def test_later_startup_failure_still_closes_handle():
    events: list[str] = []
    handle = _fake_handle(events)

    with (
        patch(FACTORY, AsyncMock(return_value=handle)),
        patch.object(main, "settings", SimpleNamespace(storage=_storage("durable", "local"))),
        patch.object(main, "_app_lifespan", _stub_app_lifespan(events, fail_startup=True)),
    ):
        with pytest.raises(RuntimeError):
            async with main.lifespan(MagicMock()):
                pytest.fail("startup must not complete")

    handle.aclose.assert_awaited_once()
    assert main.app_state.checkpointer_handle is None


async def test_close_failure_is_logged_not_raised(log_lines):
    async def _boom() -> None:
        raise ConnectionError(f"pool close failed for {SECRET}")

    handle = CheckpointerHandle(saver=MagicMock(), kind="postgres", _close=_boom)

    with (
        patch(FACTORY, AsyncMock(return_value=handle)),
        patch.object(main, "settings", SimpleNamespace(storage=_storage("durable", "prod"))),
        patch.object(main, "_app_lifespan", _stub_app_lifespan([])),
    ):
        async with main.lifespan(MagicMock()):
            pass

    assert handle.closed
    assert main.app_state.checkpointer_handle is None
    assert any("ConnectionError" in line for line in log_lines if line.startswith("WARNING"))
    assert all(SECRET not in line for line in log_lines)


async def test_default_memory_mode_uses_the_real_factory():
    events: list[str] = []
    with (
        patch.object(main, "settings", SimpleNamespace(storage=_storage("memory"))),
        patch.object(main, "_app_lifespan", _stub_app_lifespan(events)),
    ):
        async with main.lifespan(MagicMock()):
            handle = main.app_state.checkpointer_handle
            assert handle is not None and handle.kind == "memory"
            from langgraph.checkpoint.memory import MemorySaver

            assert isinstance(main.app_state.checkpointer, MemorySaver)

    assert handle.closed
    assert main.app_state.checkpointer_handle is None


async def test_startup_published_agent_writes_to_the_shared_saver(monkeypatch):
    """The startup build of ``app_state.agent`` compiles on the lifespan saver (W04.2).

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
            tool_provider=tool_provider, startup_config={}, startup_llm_cfg={}
        )

    cfg = {"configurable": {"thread_id": "startup-thread"}}
    await agent.graph.aupdate_state(cfg, {"input": "published turn"}, as_node=agent.chat.chat_agent.name)
    stored = await saver.aget_tuple(cfg)
    assert stored is not None
    assert stored.checkpoint["channel_values"]["input"] == "published turn"
