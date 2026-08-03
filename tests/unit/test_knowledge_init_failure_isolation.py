"""Unit tests: knowledge-init failure is isolated — lifespan does not abort.

PR #535 / #549 wrap knowledge startup so a failure during knowledge setup
degrades to subsystem status "failed" instead of propagating out of lifespan
and killing the server.

These tests exercise the production ``run_knowledge_startup`` helper directly.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from cuga.backend.server.main import run_knowledge_startup


class _FakeAppState:
    """Minimal replica of AppState for isolation tests."""

    def __init__(self):
        self.knowledge_engine = None
        self.subsystem_statuses: dict = {}
        self.background_tasks: list = []

    def set_subsystem_status(
        self, name: str, state: str, message: str = "", details: dict | None = None
    ) -> None:
        self.subsystem_statuses[name] = {
            "state": state,
            "message": message,
            "details": details or {},
        }

    def get_subsystem_status(self, name: str) -> dict:
        return self.subsystem_statuses.get(name, {"state": "unknown"})


def _make_kb_config(enabled: bool = True) -> MagicMock:
    cfg = MagicMock()
    cfg.enabled = enabled
    cfg.mcp_transport = "stdio"
    cfg.mcp_port = 7861
    cfg.persist_dir = "/tmp/kb"
    return cfg


@pytest.mark.unit
class TestKnowledgeInitFailureIsolation:
    """Verify that a failure in initialize_knowledge_engine() does not propagate."""

    def test_exception_is_swallowed(self):
        """An error inside initialize_knowledge_engine must NOT raise out of the call site."""
        app_state = _FakeAppState()
        kb_config = _make_kb_config(enabled=True)

        async def _boom(app_state, kb_config):
            raise RuntimeError("KnowledgeEngine constructor exploded")

        asyncio.run(run_knowledge_startup(app_state, kb_config, init_fn=_boom))

    def test_subsystem_status_set_to_failed_on_error(self):
        """Subsystem status must be 'failed' with the error message after a failure."""
        app_state = _FakeAppState()
        kb_config = _make_kb_config(enabled=True)
        error_msg = "pgvector connection refused"

        async def _boom(app_state, kb_config):
            raise ConnectionError(error_msg)

        asyncio.run(run_knowledge_startup(app_state, kb_config, init_fn=_boom))

        status = app_state.get_subsystem_status("knowledge")
        assert status["state"] == "failed"
        assert error_msg in status["details"].get("error", "")

    def test_knowledge_engine_is_none_on_error(self):
        """app_state.knowledge_engine must be None after a failure (not a partial object)."""
        app_state = _FakeAppState()
        app_state.knowledge_engine = object()
        kb_config = _make_kb_config(enabled=True)

        async def _boom(app_state, kb_config):
            raise OSError("token file write failed")

        asyncio.run(run_knowledge_startup(app_state, kb_config, init_fn=_boom))

        assert app_state.knowledge_engine is None

    def test_partial_engine_is_torn_down_on_error(self):
        """Failures after engine assignment must aclose/shutdown before clearing."""
        app_state = _FakeAppState()
        kb_config = _make_kb_config(enabled=True)
        engine = MagicMock()
        engine.aclose = AsyncMock()
        engine.shutdown = MagicMock()

        async def _boom_after_start(app_state, kb_config):
            app_state.knowledge_engine = engine
            raise RuntimeError("failed after background start")

        asyncio.run(run_knowledge_startup(app_state, kb_config, init_fn=_boom_after_start))

        engine.aclose.assert_awaited_once()
        engine.shutdown.assert_called_once()
        assert app_state.knowledge_engine is None
        assert app_state.get_subsystem_status("knowledge")["state"] == "failed"

    def test_disabled_knowledge_sets_disabled_status(self):
        """When knowledge is disabled the status is 'disabled', not 'failed'."""
        app_state = _FakeAppState()
        kb_config = _make_kb_config(enabled=False)

        init_fn = AsyncMock()
        asyncio.run(run_knowledge_startup(app_state, kb_config, init_fn=init_fn))

        init_fn.assert_not_called()
        assert app_state.get_subsystem_status("knowledge")["state"] == "disabled"
        assert app_state.knowledge_engine is None

    def test_success_path_does_not_mark_failed(self):
        """A successful init must NOT set subsystem status to 'failed'."""
        app_state = _FakeAppState()
        kb_config = _make_kb_config(enabled=True)

        async def _ok(app_state, kb_config):
            app_state.knowledge_engine = MagicMock()
            app_state.set_subsystem_status("knowledge", "ready", "Knowledge subsystem ready")

        asyncio.run(run_knowledge_startup(app_state, kb_config, init_fn=_ok))

        assert app_state.get_subsystem_status("knowledge")["state"] == "ready"
        assert app_state.knowledge_engine is not None
