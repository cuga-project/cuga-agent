"""Unit tests for _validate_acp_protocol helper (Task 3.1).

These tests exercise the helper directly — they do not spin up a full YAML
loader, so they run fast and cover edge cases without needing temp files.
"""

from __future__ import annotations

import pytest

from cuga.supervisor_utils.supervisor_config import _validate_acp_protocol

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Happy-path
# ---------------------------------------------------------------------------


def test_minimal_valid_config():
    """Minimal valid config passes without raising."""
    cfg = {
        "enabled": True,
        "endpoint": "https://agent.example.com/acp",
        "agent_name": "remote-agent",
    }
    _validate_acp_protocol("my-agent", cfg)  # must not raise


def test_full_valid_config():
    """Fully specified config with all optional fields passes."""
    cfg = {
        "enabled": True,
        "endpoint": "https://agent.example.com/acp",
        "agent_name": "remote-agent",
        "timeout": 30,
        "verify_tls": True,
        "auth": {"type": "bearer", "token_env_var": "REMOTE_ACP_TOKEN"},
    }
    _validate_acp_protocol("my-agent", cfg)


def test_http_scheme_accepted():
    """Plain http scheme is accepted."""
    cfg = {
        "endpoint": "http://internal.svc/acp",
        "agent_name": "svc",
    }
    _validate_acp_protocol("my-agent", cfg)


# ---------------------------------------------------------------------------
# Missing required fields
# ---------------------------------------------------------------------------


def test_missing_endpoint_raises():
    cfg = {"agent_name": "remote-agent"}
    with pytest.raises(ValueError, match="endpoint is required"):
        _validate_acp_protocol("my-agent", cfg)


def test_empty_endpoint_raises():
    cfg = {"endpoint": "", "agent_name": "remote-agent"}
    with pytest.raises(ValueError, match="endpoint is required"):
        _validate_acp_protocol("my-agent", cfg)


def test_missing_agent_name_raises():
    cfg = {"endpoint": "https://agent.example.com/acp"}
    with pytest.raises(ValueError, match="agent_name is required"):
        _validate_acp_protocol("my-agent", cfg)


def test_empty_agent_name_raises():
    cfg = {"endpoint": "https://agent.example.com/acp", "agent_name": ""}
    with pytest.raises(ValueError, match="agent_name is required"):
        _validate_acp_protocol("my-agent", cfg)


# ---------------------------------------------------------------------------
# URL scheme validation
# ---------------------------------------------------------------------------


def test_ftp_scheme_raises():
    cfg = {"endpoint": "ftp://agent.example.com/acp", "agent_name": "remote"}
    with pytest.raises(ValueError, match="scheme must be"):
        _validate_acp_protocol("my-agent", cfg)


def test_ws_scheme_raises():
    cfg = {"endpoint": "ws://agent.example.com/acp", "agent_name": "remote"}
    with pytest.raises(ValueError, match="scheme must be"):
        _validate_acp_protocol("my-agent", cfg)


def test_no_scheme_raises():
    cfg = {"endpoint": "agent.example.com/acp", "agent_name": "remote"}
    with pytest.raises(ValueError, match="scheme must be"):
        _validate_acp_protocol("my-agent", cfg)


@pytest.mark.unit
@pytest.mark.parametrize(
    "endpoint",
    [
        "http:///acp",
        "https://bad host/acp",
        "https://bad\nhost/acp",
        "https://bad\thost/acp",
        "https://example.com/acp\x00suffix",
        "https://example.com/acp\x7fsuffix",
        "https://example.com/acp\x80suffix",
        "https://example.com/acp\x9fsuffix",
        "https://-bad.example/acp",
        "https://999.999.999.999/acp",
        "https://example.com:notaport/acp",
        "https://example.com:70000/acp",
    ],
)
def test_invalid_endpoint_raises(endpoint: str):
    cfg = {"endpoint": endpoint, "agent_name": "remote"}
    with pytest.raises(ValueError, match="valid"):
        _validate_acp_protocol("my-agent", cfg)


@pytest.mark.parametrize("remote_name", ["INVALID_NAME", "-remote", "remote-", "a" * 64])
def test_invalid_agent_name_raises(remote_name: str):
    cfg = {"endpoint": "https://agent.example.com/acp", "agent_name": remote_name}
    with pytest.raises(ValueError, match="RFC 1123"):
        _validate_acp_protocol("my-agent", cfg)


# ---------------------------------------------------------------------------
# Timeout validation and capping
# ---------------------------------------------------------------------------


def test_timeout_zero_raises():
    cfg = {"endpoint": "https://a.example.com", "agent_name": "a", "timeout": 0}
    with pytest.raises(ValueError, match="timeout must be > 0"):
        _validate_acp_protocol("my-agent", cfg)


def test_timeout_negative_raises():
    cfg = {"endpoint": "https://a.example.com", "agent_name": "a", "timeout": -1}
    with pytest.raises(ValueError, match="timeout must be > 0"):
        _validate_acp_protocol("my-agent", cfg)


def test_timeout_exactly_600_accepted():
    cfg = {"endpoint": "https://a.example.com", "agent_name": "a", "timeout": 600}
    _validate_acp_protocol("my-agent", cfg)
    assert cfg["timeout"] == 600


def test_timeout_above_600_capped():
    cfg = {"endpoint": "https://a.example.com", "agent_name": "a", "timeout": 9999}
    _validate_acp_protocol("my-agent", cfg)
    assert cfg["timeout"] == 600


def test_timeout_float_valid():
    cfg = {"endpoint": "https://a.example.com", "agent_name": "a", "timeout": 0.5}
    _validate_acp_protocol("my-agent", cfg)


def test_timeout_float_zero_raises():
    cfg = {"endpoint": "https://a.example.com", "agent_name": "a", "timeout": 0.0}
    with pytest.raises(ValueError, match="timeout must be > 0"):
        _validate_acp_protocol("my-agent", cfg)


def test_timeout_string_raises():
    cfg = {"endpoint": "https://a.example.com", "agent_name": "a", "timeout": "30"}
    with pytest.raises(ValueError, match="timeout must be > 0"):
        _validate_acp_protocol("my-agent", cfg)


def test_default_timeout_used_when_absent():
    """When timeout is absent the default (30) is used, which is valid."""
    cfg = {"endpoint": "https://a.example.com", "agent_name": "a"}
    _validate_acp_protocol("my-agent", cfg)  # must not raise


# ---------------------------------------------------------------------------
# verify_tls defaulting
# ---------------------------------------------------------------------------


def test_verify_tls_defaults_to_true():
    cfg = {"endpoint": "https://a.example.com", "agent_name": "a"}
    _validate_acp_protocol("my-agent", cfg)
    assert cfg["verify_tls"] is True


def test_verify_tls_false_preserved():
    cfg = {"endpoint": "https://a.example.com", "agent_name": "a", "verify_tls": False}
    _validate_acp_protocol("my-agent", cfg)
    assert cfg["verify_tls"] is False


# ---------------------------------------------------------------------------
# Token resolution is deferred — token_env_var must not be resolved at load time
# ---------------------------------------------------------------------------


def test_bearer_token_not_resolved_at_load_time(monkeypatch):
    """_validate_acp_protocol must NOT resolve the token from the environment."""
    monkeypatch.setenv("MY_SECRET_TOKEN", "super-secret")
    cfg = {
        "endpoint": "https://a.example.com",
        "agent_name": "a",
        "auth": {"type": "bearer", "token_env_var": "MY_SECRET_TOKEN"},
    }
    _validate_acp_protocol("my-agent", cfg)
    # The raw token_env_var must remain as-is; the token must NOT appear in cfg
    assert cfg["auth"]["token_env_var"] == "MY_SECRET_TOKEN"
    assert "token" not in cfg["auth"]
