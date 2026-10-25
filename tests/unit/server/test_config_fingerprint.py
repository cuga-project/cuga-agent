"""Tests for the configuration fingerprint module (#774, W10.1)."""

from __future__ import annotations

import pytest

from cuga.backend.server.config_fingerprint import (
    ConfigFingerprint,
    agent_config_hash,
    compute_fingerprint,
    runtime_version,
)

pytestmark = pytest.mark.unit


def test_key_order_does_not_change_the_hash():
    a = agent_config_hash({"z": 1, "a": 2})
    b = agent_config_hash({"a": 2, "z": 1})
    assert a == b


def test_tool_include_list_change_changes_the_hash():
    base = {"tools": [{"name": "search", "include": ["app1"]}]}
    changed = {"tools": [{"name": "search", "include": ["app2"]}]}
    assert agent_config_hash(base) != agent_config_hash(changed)


def test_volatile_keys_do_not_change_the_hash():
    base = {"name": "cuga-default", "updated_at": "2024-01-01"}
    changed = {"name": "cuga-default", "updated_at": "2025-12-31"}
    assert agent_config_hash(base) == agent_config_hash(changed)


def test_volatile_keys_are_dropped_at_any_depth():
    base = {"tools": [{"name": "search", "created_at": "2024-01-01"}]}
    changed = {"tools": [{"name": "search", "created_at": "2025-12-31"}]}
    assert agent_config_hash(base) == agent_config_hash(changed)


def test_secret_value_keys_do_not_change_the_hash():
    base = {"llm": {"provider": "openai", "api_key": "sk-abc"}}
    changed = {"llm": {"provider": "openai", "api_key": "sk-xyz"}}
    assert agent_config_hash(base) == agent_config_hash(changed)


def test_secret_value_never_reaches_the_digest_input():
    # The digest of a config holding a secret equals the digest of the same config without it.
    assert agent_config_hash({"llm": {"api_key": "sk-abc"}}) == agent_config_hash({"llm": {}})


def test_secret_reference_name_change_changes_the_hash():
    # A reference name is not a secret value, so it is part of the configuration.
    base = {"llm": {"provider": "openai", "key_ref": "prod-key"}}
    changed = {"llm": {"provider": "openai", "key_ref": "staging-key"}}
    assert agent_config_hash(base) != agent_config_hash(changed)


def test_published_snapshot_and_applied_config_hash_alike():
    """Publish applies the config with the knowledge secret and without the export-only state."""
    applied = {"knowledge": {"embedding_api_key": "sk-live", "enabled": True}}
    stored = {
        "knowledge": {"embedding_api_key": "", "enabled": True},
        "knowledge_state": {"collection": "kb_agent_x"},
    }
    assert agent_config_hash(applied) == agent_config_hash(stored)


def test_runtime_version_returns_a_string():
    ver = runtime_version()
    assert isinstance(ver, str)
    assert ver  # not empty


def test_fingerprint_round_trips_through_scope_dict():
    fp = ConfigFingerprint(agent_hash="abc123" * 10 + "abcd", runtime_ver="0.3.1")
    d = fp.to_scope_dict()
    restored = ConfigFingerprint.from_scope_dict(d)
    assert restored == fp


def test_short_hash_is_a_12_char_prefix():
    fp = compute_fingerprint({"tools": []})
    assert fp.short_hash() == fp.agent_hash[:12]
    assert len(fp.short_hash()) == 12


def test_compute_fingerprint_returns_stable_fingerprint():
    config = {"tools": [{"name": "search"}], "llm": {"provider": "openai"}}
    fp1 = compute_fingerprint(config)
    fp2 = compute_fingerprint(config)
    assert fp1 == fp2
    assert len(fp1.agent_hash) == 64
