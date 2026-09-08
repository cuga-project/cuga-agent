"""LLM/tools/policies/instructions autosaves must not mutate the default draft runtime."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cuga.backend.server.auth import require_auth
from cuga.backend.server.config_store import reset_config_db
from cuga.backend.server.manage_routes import router

pytestmark = pytest.mark.unit


def _client(app_state, draft_app_state) -> TestClient:
    reset_config_db()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = app_state
    app.state.draft_app_state = draft_app_state
    return TestClient(app)


def _states():
    draft_agent = SimpleNamespace(special_instructions="default-draft", llm_config={"model": "default"})
    draft_state = SimpleNamespace(
        current_llm="draft-llm",
        agent=draft_agent,
        policy_system=None,
        tools_include_by_app={"crm": ["get_account"]},
        tools_include_version=3,
    )
    app_state = SimpleNamespace(
        agent_graphs_cache={
            ("sales-east", True): object(),
            ("sales-east", False): object(),
        }
    )
    return app_state, draft_state, draft_agent


def test_patch_draft_llm_for_non_default_invalidates_cache_only(monkeypatch):
    monkeypatch.setattr(
        "cuga.backend.server.agent_registry.is_agent_registry_enabled",
        lambda: True,
    )
    app_state, draft_state, draft_agent = _states()
    client = _client(app_state, draft_state)

    response = client.patch(
        "/api/manage/config/draft/llm",
        params={"agent_id": "sales-east"},
        json={"llm": {"provider": "openai", "model": "gpt-4o-mini"}},
    )

    assert response.status_code == 200
    assert draft_state.current_llm == "draft-llm"
    assert draft_agent.llm_config == {"model": "default"}
    assert ("sales-east", True) not in app_state.agent_graphs_cache
    assert ("sales-east", False) in app_state.agent_graphs_cache


def test_patch_draft_tools_for_non_default_does_not_rebuild_default(monkeypatch):
    monkeypatch.setattr(
        "cuga.backend.server.agent_registry.is_agent_registry_enabled",
        lambda: True,
    )
    rebuilt = {"called": False}

    async def _rebuild(*_args, **_kwargs):
        rebuilt["called"] = True

    monkeypatch.setattr(
        "cuga.backend.server.manage_routes.draft_routes.rebuild_agent_from_config",
        _rebuild,
    )
    monkeypatch.setattr(
        "cuga.backend.server.manage_routes.draft_routes.httpx.AsyncClient",
        lambda **_kwargs: SimpleNamespace(
            __aenter__=AsyncMock(
                return_value=SimpleNamespace(
                    post=AsyncMock(
                        return_value=SimpleNamespace(raise_for_status=lambda: None, json=lambda: {})
                    )
                )
            ),
            __aexit__=AsyncMock(return_value=None),
        ),
    )
    app_state, draft_state, _draft_agent = _states()
    client = _client(app_state, draft_state)

    response = client.patch(
        "/api/manage/config/draft/tools",
        params={"agent_id": "sales-east"},
        json={"tools": [{"name": "other_app", "include": ["list_items"]}]},
    )

    assert response.status_code == 200
    assert rebuilt["called"] is False
    assert draft_state.tools_include_by_app == {"crm": ["get_account"]}
    assert draft_state.tools_include_version == 3
    assert ("sales-east", True) not in app_state.agent_graphs_cache


def test_patch_draft_policies_for_non_default_skips_shared_policy_system():
    app_state, draft_state, _draft_agent = _states()
    draft_state.policy_system = SimpleNamespace(storage=object())
    client = _client(app_state, draft_state)

    response = client.patch(
        "/api/manage/config/draft/policies",
        params={"agent_id": "sales-east"},
        json={"policies": {"policies": []}},
    )

    assert response.status_code == 200
    assert ("sales-east", True) not in app_state.agent_graphs_cache


def test_patch_draft_instructions_for_non_default_does_not_overwrite_default():
    app_state, draft_state, draft_agent = _states()
    client = _client(app_state, draft_state)

    response = client.patch(
        "/api/manage/config/draft/special_instructions",
        params={"agent_id": "sales-east"},
        json={"special_instructions": "east-only"},
    )

    assert response.status_code == 200
    assert draft_agent.special_instructions == "default-draft"
    assert ("sales-east", True) not in app_state.agent_graphs_cache
