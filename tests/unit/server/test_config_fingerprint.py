"""Tests for the configuration fingerprint module (#774)."""

from __future__ import annotations

import pytest

from cuga.backend.server.config_fingerprint import agent_config_hash

pytestmark = pytest.mark.unit


def test_key_order_does_not_change_the_hash():
    assert agent_config_hash({"z": 1, "a": 2}) == agent_config_hash({"a": 2, "z": 1})


def test_tool_include_list_change_changes_the_hash():
    base = {"tools": [{"name": "search", "include": ["app1"]}]}
    changed = {"tools": [{"name": "search", "include": ["app2"]}]}
    assert agent_config_hash(base) != agent_config_hash(changed)


def test_volatile_keys_are_dropped_at_any_depth():
    base = {"updated_at": "2024-01-01", "tools": [{"name": "search", "created_at": "2024-01-01"}]}
    changed = {"updated_at": "2025-12-31", "tools": [{"name": "search", "created_at": "2025-12-31"}]}
    assert agent_config_hash(base) == agent_config_hash(changed)


def test_secret_value_never_reaches_the_digest_input():
    # Rotating a bare credential changes nothing: the digest equals that of the config without it.
    assert agent_config_hash({"llm": {"api_key": "sk-abc"}}) == agent_config_hash({"llm": {}})
    assert agent_config_hash({"llm": {"api_key": "sk-xyz"}}) == agent_config_hash({"llm": {}})


@pytest.mark.parametrize(
    "first, second",
    [
        ("vault://prod/llm-key", "vault://staging/llm-key"),
        ("env://PROD_OPENAI_KEY", "env://STAGING_OPENAI_KEY"),
        ("db://openai-prod", "db://openai-staging"),
        ("aws://prod/openai", "aws://staging/openai"),
    ],
)
def test_secret_reference_change_changes_the_hash(first, second):
    base = {"llm": {"provider": "openai", "api_key": first}}
    changed = {"llm": {"provider": "openai", "api_key": second}}
    assert agent_config_hash(base) != agent_config_hash(changed)


def test_published_snapshot_and_applied_config_hash_alike():
    """Publish applies the config with the knowledge secret and without the export-only state."""
    applied = {"knowledge": {"embedding_api_key": "sk-live", "enabled": True}}
    stored = {
        "knowledge": {"embedding_api_key": "", "enabled": True},
        "knowledge_state": {"collection": "kb_agent_x"},
    }
    assert agent_config_hash(applied) == agent_config_hash(stored)
