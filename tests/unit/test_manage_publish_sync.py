from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cuga.backend.server.auth import require_auth
from cuga.backend.server.config_store import load_config, load_draft, reset_config_db
from cuga.backend.server.manage_routes import router


class _FakeKnowledgeEngine:
    def prepare_knowledge_update(self, knowledge_cfg: dict):
        return SimpleNamespace(knowledge_cfg=knowledge_cfg)

    def commit_knowledge_update(self, prepared) -> dict:
        return {"reindex_recommended": False, "prepared": prepared.knowledge_cfg}

    async def list_documents(self, collection: str) -> list[dict]:
        return []


async def _allow_publish(*_args, **_kwargs) -> None:
    return None


def test_publish_syncs_draft_with_published_knowledge_flags(monkeypatch):
    reset_config_db()
    monkeypatch.setattr("cuga.backend.server.manage_routes._apply_published_config", _allow_publish)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = SimpleNamespace(
        knowledge_engine=_FakeKnowledgeEngine(),
        agent=None,
        config_version=None,
        tools_include_version=0,
    )

    client = TestClient(app)
    response = client.post(
        "/api/manage/config",
        params={"agent_id": "test-agent"},
        json={
            "config": {
                "knowledge": {
                    "enabled": True,
                    "agent_level_enabled": True,
                    "session_level_enabled": False,
                }
            }
        },
    )

    assert response.status_code == 200

    draft = asyncio.run(load_draft("test-agent"))
    published, _ = asyncio.run(load_config(None, "test-agent"))

    assert draft is not None
    assert published is not None
    assert draft["knowledge"]["session_level_enabled"] is False
    assert published["knowledge"]["session_level_enabled"] is False
    assert draft["knowledge"]["agent_level_enabled"] is True


def test_publish_syncs_draft_with_published_agent_level_disabled(monkeypatch):
    reset_config_db()
    monkeypatch.setattr("cuga.backend.server.manage_routes._apply_published_config", _allow_publish)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = SimpleNamespace(
        knowledge_engine=_FakeKnowledgeEngine(),
        agent=None,
        config_version=None,
        tools_include_version=0,
    )

    client = TestClient(app)
    response = client.post(
        "/api/manage/config",
        params={"agent_id": "test-agent"},
        json={
            "config": {
                "knowledge": {
                    "enabled": True,
                    "agent_level_enabled": False,
                    "session_level_enabled": True,
                }
            }
        },
    )

    assert response.status_code == 200

    draft = asyncio.run(load_draft("test-agent"))
    published, _ = asyncio.run(load_config(None, "test-agent"))

    assert draft is not None
    assert published is not None
    assert draft["knowledge"]["agent_level_enabled"] is False
    assert published["knowledge"]["agent_level_enabled"] is False
    assert draft["knowledge"]["session_level_enabled"] is True
