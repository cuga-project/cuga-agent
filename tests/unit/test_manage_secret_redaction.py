"""Secret redaction must reach secrets nested inside LIST items, not just dicts.

Review finding: ``_redact_secrets_in_config`` only recursed into dict values, so a
config shape like ``{"tools": [{"api_key": "..."}]}`` returned the nested secret  # pragma: allowlist secret
unredacted from GET /config. The walker now recurses into list items too.
"""

from __future__ import annotations

from cuga.backend.server.manage_routes import _redact_secrets_in_config
from cuga.backend.server.manage_routes.helpers import is_secret_ref


def test_redacts_secret_inside_list_items():
    cfg = {
        "tools": [{"name": "crm", "api_key": "sk-should-be-hidden"}],  # pragma: allowlist secret
        "knowledge": {"embedding_api_key": "k-should-be-hidden"},  # pragma: allowlist secret
    }
    _redact_secrets_in_config(cfg)
    assert cfg["tools"][0]["api_key"] == "", "secret inside a list item must be redacted"
    assert cfg["knowledge"]["embedding_api_key"] == "", "nested-dict secret still redacted"
    assert cfg["tools"][0]["name"] == "crm", "non-secret fields preserved"


def test_redacts_deeply_nested_list_of_dicts():
    cfg = {"a": [{"b": [{"api_key": "sk-deep"}]}]}  # pragma: allowlist secret
    _redact_secrets_in_config(cfg)
    assert cfg["a"][0]["b"][0]["api_key"] == ""


def test_preserves_secret_refs_on_redact():
    cfg = {
        "llm": {
            "api_key": "vault://secret/openai-api-key#value",
            "provider": "openai",
        },
        "knowledge": {"embedding_api_key": "db://embed-key"},  # pragma: allowlist secret
        "tools": [{"name": "mcp", "api_key": "aws://my-secret"}],  # pragma: allowlist secret
    }
    _redact_secrets_in_config(cfg)
    assert cfg["llm"]["api_key"] == "vault://secret/openai-api-key#value"
    assert cfg["knowledge"]["embedding_api_key"] == "db://embed-key"
    assert cfg["tools"][0]["api_key"] == "aws://my-secret"
    assert cfg["llm"]["provider"] == "openai"


def test_redacts_plaintext_but_keeps_env_ref():
    cfg = {
        "llm": {"api_key": "sk-live-secret"},  # pragma: allowlist secret
        "other": {"token": "env://OPENAI_API_KEY"},
    }
    _redact_secrets_in_config(cfg)
    assert cfg["llm"]["api_key"] == ""
    assert cfg["other"]["token"] == "env://OPENAI_API_KEY"


def test_is_secret_ref_helper():
    assert is_secret_ref("vault://secret/x#value")
    assert is_secret_ref("db://slug")
    assert not is_secret_ref("sk-plain")  # pragma: allowlist secret
    assert not is_secret_ref("")
