"""Publishing a non-default agent must not mutate the process-wide default runtime."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cuga.backend.server.auth import require_auth
from cuga.backend.server.config_store import reset_config_db
from cuga.backend.server.manage_routes import router

pytestmark = pytest.mark.unit


class _FakeKnowledgeEngine:
    def __init__(self):
        self._reindex_in_progress: set[str] = set()
        self._reindex_deferred: set[str] = set()

    def prepare_knowledge_update(self, knowledge_cfg: dict):
        return SimpleNamespace(knowledge_cfg=knowledge_cfg)

    def commit_knowledge_update(self, prepared) -> dict:
        return {"reindex_recommended": False, "prepared": prepared.knowledge_cfg}

    async def list_documents(self, collection: str) -> list[dict]:
        return []


def _client(app_state) -> TestClient:
    reset_config_db()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = app_state
    return TestClient(app)


def _default_runtime():
    agent = SimpleNamespace(special_instructions="default-instructions")
    return SimpleNamespace(
        knowledge_engine=_FakeKnowledgeEngine(),
        agent=agent,
        current_llm="default-llm",
        tools_include_by_app={"crm": ["get_account"]},
        config_version=1,
        tools_include_version=1,
        agent_graphs_cache={("sales-east", False): object(), ("sales-east", True): object()},
        policy_system=None,
        policy_filesystem_sync=False,
    )


def test_publishing_non_default_agent_does_not_mutate_singleton_runtime():
    state = _default_runtime()
    default_agent = state.agent
    cached_published = state.agent_graphs_cache[("sales-east", False)]
    client = _client(state)

    response = client.post(
        "/api/manage/config",
        params={"agent_id": "sales-east"},
        json={
            "config": {
                "agent": {"name": "Sales East"},
                "llm": {"provider": "openai", "model": "gpt-4o-mini"},
                "tools": [{"name": "other_app", "include": ["list_items"]}],
                "special_instructions": "east-only",
            }
        },
    )

    assert response.status_code == 200
    assert state.agent is default_agent
    assert state.agent.special_instructions == "default-instructions"
    assert state.current_llm == "default-llm"
    assert state.tools_include_by_app == {"crm": ["get_account"]}
    assert ("sales-east", False) not in state.agent_graphs_cache
    assert cached_published is not None


def test_publishing_default_agent_still_applies_to_singleton(monkeypatch):
    state = _default_runtime()
    applied = {}

    async def _capture_apply(app_state, config):
        applied["config"] = config
        app_state.current_llm = "updated-llm"
        app_state.tools_include_by_app = {"updated": ["x"]}

    async def _capture_rebuild(app_state, config):
        applied["rebuilt"] = True
        app_state.agent.special_instructions = config.get("special_instructions")

    monkeypatch.setattr(
        "cuga.backend.server.manage_routes.config_routes.apply_published_config",
        _capture_apply,
    )
    monkeypatch.setattr(
        "cuga.backend.server.manage_routes.config_routes.rebuild_production_agent",
        _capture_rebuild,
    )
    client = _client(state)

    response = client.post(
        "/api/manage/config",
        params={"agent_id": "cuga-default"},
        json={
            "config": {
                "agent": {"name": "Default"},
                "special_instructions": "new-default",
            }
        },
    )

    assert response.status_code == 200
    assert applied.get("config") is not None
    assert applied.get("rebuilt") is True
    assert state.current_llm == "updated-llm"
    assert state.agent.special_instructions == "new-default"
