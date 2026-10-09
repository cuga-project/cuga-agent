"""Publishing a non-default agent must not mutate the process-wide default runtime."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from cuga.backend.server.auth import require_auth
from cuga.backend.server.config_store import reset_config_db
from cuga.backend.server.manage_routes import config_routes, router

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


@pytest.mark.asyncio
async def test_publish_rejects_unknown_agent_before_writes(monkeypatch):
    save_draft = AsyncMock()
    save_config = AsyncMock()
    monkeypatch.setattr("cuga.backend.server.agent_registry.is_agent_registry_enabled", lambda: True)
    monkeypatch.setattr(
        "cuga.backend.server.config_store.list_agents_with_configs",
        AsyncMock(return_value=[{"agent_id": "registered-agent"}]),
    )
    monkeypatch.setattr("cuga.backend.server.config_store.save_draft", save_draft)
    monkeypatch.setattr("cuga.backend.server.config_store.save_config", save_config)
    request = SimpleNamespace(json=AsyncMock(), app=SimpleNamespace(state=SimpleNamespace(app_state=None)))

    with pytest.raises(HTTPException) as exc_info:
        await config_routes.save_manage_config_publish(request, agent_id="unknown-agent")

    assert exc_info.value.status_code == 404
    request.json.assert_not_awaited()
    save_draft.assert_not_awaited()
    save_config.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_passes_registry_owned_agent_id_to_policy_creation(monkeypatch):
    registry_agent_id = "".join(["registered", "-agent"])
    requested_agent_id = registry_agent_id.encode().decode()
    assert requested_agent_id == registry_agent_id
    assert requested_agent_id is not registry_agent_id

    captured = {}

    async def _capture_policy_creation(*, agent_id, draft, policies_data):
        captured["agent_id"] = agent_id
        captured["draft"] = draft
        captured["policies_data"] = policies_data

    monkeypatch.setattr("cuga.backend.server.agent_registry.is_agent_registry_enabled", lambda: True)
    monkeypatch.setattr(
        "cuga.backend.server.config_store.list_agents_with_configs",
        AsyncMock(return_value=[{"agent_id": registry_agent_id}]),
    )
    monkeypatch.setattr("cuga.backend.server.config_store.load_config", AsyncMock(return_value=(None, None)))
    monkeypatch.setattr("cuga.backend.server.config_store.save_draft", AsyncMock())
    monkeypatch.setattr("cuga.backend.server.config_store.save_config", AsyncMock(return_value="1"))
    monkeypatch.setattr(
        "cuga.backend.cuga_graph.policy.configurable.create_agent_policy_system",
        _capture_policy_creation,
    )
    monkeypatch.setattr(config_routes, "invalidate_agent_graph_cache", AsyncMock())
    request = SimpleNamespace(
        json=AsyncMock(
            return_value={
                "config": {
                    "agent": {"name": "Registered Agent"},
                    "policies": {"policies": [{"id": "policy-1"}]},
                }
            }
        ),
        app=SimpleNamespace(state=SimpleNamespace(app_state=_default_runtime())),
    )

    response = await config_routes.save_manage_config_publish(request, agent_id=requested_agent_id)

    assert response.status_code == 200
    assert captured["agent_id"] is registry_agent_id
    assert captured["draft"] is False
    assert captured["policies_data"] == [{"id": "policy-1"}]


def test_publishing_non_default_agent_does_not_mutate_singleton_runtime(monkeypatch):
    monkeypatch.setattr("cuga.backend.server.agent_registry.is_agent_registry_enabled", lambda: True)
    monkeypatch.setattr(
        "cuga.backend.server.config_store.list_agents_with_configs",
        AsyncMock(return_value=[{"agent_id": "sales-east"}]),
    )
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
