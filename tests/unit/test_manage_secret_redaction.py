"""Secret redaction must reach secrets nested inside LIST items, not just dicts.

Review finding: ``_redact_secrets_in_config`` only recursed into dict values, so a
config shape like ``{"tools": [{"api_key": "..."}]}`` returned the nested secret
unredacted from GET /config. The walker now recurses into list items too.
"""

from __future__ import annotations

from cuga.backend.server.manage_routes import _redact_secrets_in_config


def test_redacts_secret_inside_list_items():
    cfg = {
        "tools": [{"name": "crm", "api_key": "sk-should-be-hidden"}],
        "knowledge": {"embedding_api_key": "k-should-be-hidden"},
    }
    _redact_secrets_in_config(cfg)
    assert cfg["tools"][0]["api_key"] == "", "secret inside a list item must be redacted"
    assert cfg["knowledge"]["embedding_api_key"] == "", "nested-dict secret still redacted"
    assert cfg["tools"][0]["name"] == "crm", "non-secret fields preserved"


def test_redacts_deeply_nested_list_of_dicts():
    cfg = {"a": [{"b": [{"api_key": "sk-deep"}]}]}
    _redact_secrets_in_config(cfg)
    assert cfg["a"][0]["b"][0]["api_key"] == ""
