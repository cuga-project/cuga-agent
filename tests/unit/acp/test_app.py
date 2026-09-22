"""Unit tests for ACP settings validation (Task 2.1) and app construction (Task 2.4)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cuga.backend.server.acp.settings import ACPSettings, normalize_acp_settings

pytestmark = pytest.mark.unit


@dataclass
class _RawSettings:
    """Minimal dynaconf-like settings stub."""

    enabled: bool = False
    path_prefix: str = "/acp"
    agent_name: str = "cuga"
    agent_description: str = "CUGA agent exposed over ACP."
    supervisor_config_path: str = ""
    auto_approve: bool = False
    store: str = "memory"
    store_limit: int = 1000
    store_ttl_seconds: int = 3600
    auth_required: bool = True
    enable_playground_cors: bool = False


def _defaults(**overrides: Any) -> _RawSettings:
    """Return default raw settings with optional overrides."""
    fields = {
        "enabled": False,
        "path_prefix": "/acp",
        "agent_name": "cuga",
        "agent_description": "CUGA agent exposed over ACP.",
        "supervisor_config_path": "",
        "auto_approve": False,
        "store": "memory",
        "store_limit": 1000,
        "store_ttl_seconds": 3600,
        "auth_required": True,
        "enable_playground_cors": False,
    }
    fields.update(overrides)
    return _RawSettings(**fields)


# ---------------------------------------------------------------------------
# Valid defaults
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_valid_defaults_returns_acp_settings() -> None:
    result = normalize_acp_settings(_defaults())
    assert isinstance(result, ACPSettings)
    assert result.enabled is False
    assert result.path_prefix == "/acp"
    assert result.agent_name == "cuga"
    assert result.store == "memory"
    assert result.store_limit == 1000
    assert result.store_ttl_seconds == 3600
    assert result.auth_required is True
    assert result.enable_playground_cors is False


@pytest.mark.unit
def test_prefix_normalization_strips_trailing_slash() -> None:
    result = normalize_acp_settings(_defaults(path_prefix="/acp/"))
    assert result.path_prefix == "/acp"


@pytest.mark.unit
def test_prefix_normalization_adds_leading_slash() -> None:
    result = normalize_acp_settings(_defaults(path_prefix="acp"))
    assert result.path_prefix == "/acp"


@pytest.mark.unit
def test_prefix_normalization_strips_multiple_leading_slashes() -> None:
    result = normalize_acp_settings(_defaults(path_prefix="///acp"))
    assert result.path_prefix == "/acp"


# ---------------------------------------------------------------------------
# Rejection cases — agent name
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_rejects_empty_agent_name() -> None:
    with pytest.raises(ValueError, match="agent_name"):
        normalize_acp_settings(_defaults(agent_name=""))


@pytest.mark.unit
def test_rejects_whitespace_only_agent_name() -> None:
    with pytest.raises(ValueError, match="agent_name"):
        normalize_acp_settings(_defaults(agent_name="   "))


@pytest.mark.unit
def test_rejects_agent_name_with_surrounding_whitespace() -> None:
    with pytest.raises(ValueError, match="agent_name"):
        normalize_acp_settings(_defaults(agent_name=" cuga "))


# ---------------------------------------------------------------------------
# Rejection cases — path prefix
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_rejects_root_prefix() -> None:
    with pytest.raises(ValueError, match="path_prefix"):
        normalize_acp_settings(_defaults(path_prefix="/"))


@pytest.mark.unit
def test_rejects_empty_prefix() -> None:
    with pytest.raises(ValueError, match="path_prefix"):
        normalize_acp_settings(_defaults(path_prefix=""))


@pytest.mark.unit
@pytest.mark.parametrize(
    "reserved",
    ["/api", "/a2a", "/docs", "/health", "/openapi.json", "/redoc", "/run", "/stream"],
)
def test_rejects_reserved_prefix(reserved: str) -> None:
    with pytest.raises(ValueError, match="reserved"):
        normalize_acp_settings(_defaults(path_prefix=reserved))


# ---------------------------------------------------------------------------
# Rejection cases — store
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_rejects_unknown_store() -> None:
    with pytest.raises(ValueError, match="store"):
        normalize_acp_settings(_defaults(store="redis"))


# ---------------------------------------------------------------------------
# Rejection cases — limits
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_rejects_zero_store_limit() -> None:
    with pytest.raises(ValueError, match="store_limit"):
        normalize_acp_settings(_defaults(store_limit=0))


@pytest.mark.unit
def test_rejects_negative_store_limit() -> None:
    with pytest.raises(ValueError, match="store_limit"):
        normalize_acp_settings(_defaults(store_limit=-1))


@pytest.mark.unit
def test_rejects_zero_store_ttl() -> None:
    with pytest.raises(ValueError, match="store_ttl_seconds"):
        normalize_acp_settings(_defaults(store_ttl_seconds=0))


@pytest.mark.unit
def test_rejects_negative_store_ttl() -> None:
    with pytest.raises(ValueError, match="store_ttl_seconds"):
        normalize_acp_settings(_defaults(store_ttl_seconds=-10))


# ---------------------------------------------------------------------------
# Task 2.4 — app construction tests
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _acp_settings(**overrides: Any) -> ACPSettings:
    """Return a normalized ACPSettings with sensible defaults and overrides."""
    fields = {
        "enabled": True,
        "path_prefix": "/acp",
        "agent_name": "test-agent",
        "agent_description": "Test ACP agent.",
        "supervisor_config_path": "",
        "auto_approve": False,
        "store": "memory",
        "store_limit": 500,
        "store_ttl_seconds": 1800,
        "auth_required": False,
        "enable_playground_cors": False,
    }
    fields.update(overrides)
    return normalize_acp_settings(_RawSettings(**fields))


def _dummy_event_stream():
    """Minimal async generator used as a stand-in for event_stream."""

    async def _gen(*args: Any, **kwargs: Any):  # type: ignore[override]
        return
        yield  # pragma: no cover — make it an async generator

    return _gen


# ---------------------------------------------------------------------------
# Runner selection
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_simple_runner_selected_when_no_supervisor_path() -> None:
    """SimpleAgentRunner is used when supervisor_config_path is empty."""
    from cuga.backend.server.acp.runner import build_acp_runner
    from cuga.backend.server.agent_protocol.simple_runner import SimpleAgentRunner

    app_state = MagicMock()
    runner = build_acp_runner(
        app_state=app_state,
        event_stream_func=_dummy_event_stream(),
        supervisor_config_path="",
        auto_approve=False,
    )
    assert isinstance(runner, SimpleAgentRunner)


@pytest.mark.unit
def test_supervisor_runner_selected_when_path_set() -> None:
    """SupervisorAgentRunner is used when supervisor_config_path is non-empty."""
    from cuga.backend.server.acp.runner import build_acp_runner
    from cuga.backend.server.agent_protocol.supervisor_runner import SupervisorAgentRunner

    app_state = MagicMock()
    runner = build_acp_runner(
        app_state=app_state,
        event_stream_func=_dummy_event_stream(),
        supervisor_config_path="/some/config.yaml",
        auto_approve=False,
    )
    assert isinstance(runner, SupervisorAgentRunner)


@pytest.mark.unit
def test_supervisor_runner_uses_acp_protocol_name_and_cache_attr() -> None:
    """SupervisorAgentRunner is configured with protocol_name='ACP' and cache_attr='acp_supervisor'."""
    from cuga.backend.server.acp.runner import build_acp_runner
    from cuga.backend.server.agent_protocol.supervisor_runner import SupervisorAgentRunner

    app_state = MagicMock()
    runner = build_acp_runner(
        app_state=app_state,
        event_stream_func=None,
        supervisor_config_path="/path/to/supervisor.yaml",
        auto_approve=False,
    )
    assert isinstance(runner, SupervisorAgentRunner)
    assert runner._protocol_name == "ACP"
    assert runner._cache_attr == "acp_supervisor"


@pytest.mark.unit
def test_simple_runner_uses_acp_user_id() -> None:
    """SimpleAgentRunner is created with caller_user_id='acp_user'."""
    from cuga.backend.server.acp.runner import build_acp_runner
    from cuga.backend.server.agent_protocol.simple_runner import SimpleAgentRunner

    app_state = MagicMock()
    runner = build_acp_runner(
        app_state=app_state,
        event_stream_func=_dummy_event_stream(),
        supervisor_config_path="",
        auto_approve=True,
    )
    assert isinstance(runner, SimpleAgentRunner)
    assert runner._caller_user_id == "acp_user"
    assert runner._auto_approve is True


# ---------------------------------------------------------------------------
# Memory-store settings
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_acp_app_returns_fastapi_app() -> None:
    """build_acp_app_for_settings returns a FastAPI instance."""
    from cuga.backend.server.acp.app import build_acp_app_for_settings

    settings = _acp_settings()
    app = build_acp_app_for_settings(settings, MagicMock(), event_stream_func=_dummy_event_stream())
    assert isinstance(app, FastAPI)


@pytest.mark.unit
def test_memory_store_limit_passed_correctly() -> None:
    """The store is created with the limit from settings."""

    from acp_sdk.server import MemoryStore

    from cuga.backend.server.acp.app import build_acp_app_for_settings

    created_stores: list[MemoryStore] = []
    original_init = MemoryStore.__init__

    def _capturing_init(self: MemoryStore, *, limit: int, ttl: Any = None) -> None:
        original_init(self, limit=limit, ttl=ttl)
        created_stores.append(self)

    with patch.object(MemoryStore, "__init__", _capturing_init):
        settings = _acp_settings(store_limit=42, store_ttl_seconds=300)
        build_acp_app_for_settings(settings, MagicMock(), event_stream_func=_dummy_event_stream())

    assert len(created_stores) == 1
    # The TTLCache maxsize reflects the limit argument.
    assert created_stores[0]._cache.maxsize == 42


@pytest.mark.unit
@pytest.mark.parametrize("enabled", [False, True])
def test_playground_cors_setting_forwarded_to_sdk(enabled: bool) -> None:
    """The ACP app factory receives the configured playground CORS value."""
    import acp_sdk.server

    from cuga.backend.server.acp.app import build_acp_app_for_settings

    original_create_app = acp_sdk.server.create_app
    captured: dict[str, Any] = {}

    def _capturing_create_app(*args: Any, **kwargs: Any) -> FastAPI:
        captured.update(kwargs)
        return original_create_app(*args, **kwargs)

    with patch.object(acp_sdk.server, "create_app", _capturing_create_app):
        build_acp_app_for_settings(
            _acp_settings(enable_playground_cors=enabled),
            MagicMock(),
            event_stream_func=_dummy_event_stream(),
        )

    assert captured["enable_playground_cors"] is enabled


@pytest.mark.unit
def test_memory_store_ttl_passed_as_timedelta() -> None:
    """The store is created with ttl=timedelta(seconds=store_ttl_seconds)."""
    from datetime import timedelta

    from acp_sdk.server import MemoryStore

    from cuga.backend.server.acp.app import build_acp_app_for_settings

    captured: list[dict[str, Any]] = []
    original_init = MemoryStore.__init__

    def _capturing_init(self: MemoryStore, *, limit: int, ttl: Any = None) -> None:
        captured.append({"limit": limit, "ttl": ttl})
        original_init(self, limit=limit, ttl=ttl)

    with patch.object(MemoryStore, "__init__", _capturing_init):
        settings = _acp_settings(store_limit=100, store_ttl_seconds=600)
        build_acp_app_for_settings(settings, MagicMock(), event_stream_func=_dummy_event_stream())

    assert len(captured) == 1
    assert captured[0]["limit"] == 100
    assert captured[0]["ttl"] == timedelta(seconds=600)


# ---------------------------------------------------------------------------
# Missing SDK
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_missing_acp_sdk_raises_import_error_with_actionable_message() -> None:
    """ImportError raised when acp_sdk is absent mentions 'cuga[acp]'."""
    from cuga.backend.server.acp.app import build_acp_app_for_settings
    from cuga.backend.server.acp.settings import normalize_acp_settings

    settings = normalize_acp_settings(_RawSettings())
    # Block the acp_sdk.server import inside build_acp_app_for_settings.
    with patch.dict(
        "sys.modules",
        {"acp_sdk": None, "acp_sdk.server": None},
    ):
        with pytest.raises(ImportError, match="cuga\\[acp\\]"):
            build_acp_app_for_settings(settings, MagicMock(), event_stream_func=None)


# ---------------------------------------------------------------------------
# Authentication — endpoints require auth when enabled
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_auth_required_true_applies_dependency() -> None:
    """When auth_required=True, build_auth_dependencies returns one dependency wrapping require_chat_access."""
    from cuga.backend.server.acp.dependencies import build_auth_dependencies
    from cuga.backend.server.auth.dependencies import require_chat_access

    deps = build_auth_dependencies(auth_required=True)
    assert len(deps) == 1
    assert deps[0].dependency is require_chat_access


@pytest.mark.unit
def test_auth_required_false_returns_empty_dependency_list() -> None:
    """When auth_required=False, build_auth_dependencies returns an empty list."""
    from cuga.backend.server.acp.dependencies import build_auth_dependencies

    deps = build_auth_dependencies(auth_required=False)
    assert deps == []


@pytest.mark.unit
def test_ping_requires_auth_when_enabled() -> None:
    """GET /ping returns 403 when auth_required=True and no credentials provided."""
    from cuga.backend.server.acp.app import build_acp_app_for_settings

    # Patch require_chat_access so it raises 403 (simulating a rejected request).
    from fastapi import HTTPException

    async def _reject() -> None:
        raise HTTPException(status_code=403, detail="Access denied")

    with patch("cuga.backend.server.acp.dependencies.build_auth_dependencies") as mock_deps:
        from fastapi import Depends

        mock_deps.return_value = [Depends(_reject)]
        settings = _acp_settings(auth_required=True)
        app = build_acp_app_for_settings(settings, MagicMock(), event_stream_func=_dummy_event_stream())

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/ping")
    assert response.status_code == 403


@pytest.mark.unit
def test_ping_reachable_without_credentials_when_auth_disabled() -> None:
    """GET /ping returns 200 when auth_required=False."""
    from cuga.backend.server.acp.app import build_acp_app_for_settings

    settings = _acp_settings(auth_required=False)
    app = build_acp_app_for_settings(settings, MagicMock(), event_stream_func=_dummy_event_stream())
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/ping")
    assert response.status_code == 200
