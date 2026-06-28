"""PATCH knowledge — preflight failure handling.

When the user switches embedding provider, our field-reset clears the API key
field. The 800ms debounced PATCH then fires with key="". If the server has a
stale OPENAI_API_KEY env var (e.g. someone else's invalid key in .env), the
engine preflight would 401. Previously this returned 400 and the UI showed a
red "Knowledge settings rejected" toast — confusing because the user hadn't
even seen the new fields yet.

Now:
  - If the user supplied a key AND it's wrong → 400 (their explicit choice broken)
  - If the user did NOT supply a key (env-var fallback failed) → 200 with a
    `preflight_warning` field that the UI shows as a yellow warning toast

This file pins both cases.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cuga.backend.knowledge.config import KnowledgeConfig
from cuga.backend.knowledge.engine import KnowledgeEngine


def _build_app(eng):
    from cuga.backend.server.manage_routes import router
    from cuga.backend.server.auth import require_auth

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = SimpleNamespace(
        knowledge_engine=eng, agent=None, config_version=None, tools_include_version=0
    )
    return app


def _cfg():
    return KnowledgeConfig(
        enabled=True,
        persist_dir=Path(tempfile.mkdtemp(prefix="cuga-preflight-")),
        embedding_provider="fastembed",
    )


def _401_error():
    return RuntimeError(
        "Error code: 401 - {'error': {'message': 'Incorrect API key provided: sk-uTUoC***Z96A', "
        "'type': 'invalid_request_error', 'code': 'invalid_api_key'}}"
    )


def test_user_supplied_bad_key_still_returns_400():
    """When the user explicitly types a key and it's wrong, hard-fail.
    Their input is broken — they need to see the error and fix it."""
    eng = KnowledgeEngine(_cfg())
    app = _build_app(eng)
    client = TestClient(app)
    with patch(
        "cuga.backend.knowledge.engine.create_embeddings",
        side_effect=_401_error(),
    ):
        r = client.patch(
            "/api/manage/config/draft/knowledge",
            params={"agent_id": "user-key-bad"},
            json={
                "embedding_provider": "openai",
                "embedding_model": "text-embedding-3-small",
                "embedding_api_key": "sk-user-typed-this",
            },
        )
    assert r.status_code == 400, r.text
    assert "rejected" in r.json()["detail"].lower()


def test_env_var_fallback_failure_returns_200_with_warning(monkeypatch):
    """When the user does NOT supply a key (just switched provider) and the
    env-var fallback fails auth, return 200 + preflight_warning instead of
    blocking the save."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-bad-env-var")
    eng = KnowledgeEngine(_cfg())
    app = _build_app(eng)
    client = TestClient(app)
    with patch(
        "cuga.backend.knowledge.engine.create_embeddings",
        side_effect=_401_error(),
    ):
        r = client.patch(
            "/api/manage/config/draft/knowledge",
            params={"agent_id": "env-fallback-bad"},
            json={
                "embedding_provider": "openai",
                # No embedding_api_key — would fall back to env var
            },
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "preflight_warning" in body, f"Expected soft-fail warning in response; got {body!r}"
    assert "key" in body["preflight_warning"].lower()
    # Soft-fail intentionally KEEPS the engine on its last-known-good config —
    # so existing ingests/queries don't break. The new state is persisted in
    # the draft so the UI reflects it and a future valid key applies cleanly.


def test_non_auth_engine_failures_still_block_save(monkeypatch):
    """Soft-fail is scoped to auth errors specifically. Other engine errors
    (e.g. unknown model crash, dim-detection logic bug) must still hard-fail."""
    eng = KnowledgeEngine(_cfg())
    app = _build_app(eng)
    client = TestClient(app)
    # Not a 401/auth-looking error
    with patch(
        "cuga.backend.knowledge.engine.create_embeddings",
        side_effect=RuntimeError("Model not found: my-broken-model"),
    ):
        r = client.patch(
            "/api/manage/config/draft/knowledge",
            params={"agent_id": "other-error"},
            json={
                "embedding_provider": "openai",
                "embedding_model": "my-broken-model",
            },
        )
    assert r.status_code == 400


def test_soft_fail_only_for_credentialed_providers():
    """For fastembed (no credentials concept), failures still hard-fail —
    soft-fail logic only applies to openai/openrouter/litellm where env-var
    fallback is meaningful."""
    eng = KnowledgeEngine(_cfg())
    app = _build_app(eng)
    client = TestClient(app)
    # Change the model so embedding_changed=True → create_embeddings runs
    with patch(
        "cuga.backend.knowledge.engine.create_embeddings",
        side_effect=_401_error(),  # not realistic for fastembed but tests the gate
    ):
        r = client.patch(
            "/api/manage/config/draft/knowledge",
            params={"agent_id": "fastembed-fail"},
            json={
                "embedding_provider": "fastembed",
                "embedding_model": "BAAI/bge-base-en-v1.5",  # different from default to trigger preflight
            },
        )
    # fastembed doesn't trigger soft-fail — error propagates
    assert r.status_code == 400, r.text
