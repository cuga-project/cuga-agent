"""Unit tests for ACP settings validation (Task 2.1).

Tests for Task 2.4 (app construction) will be added to this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

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
