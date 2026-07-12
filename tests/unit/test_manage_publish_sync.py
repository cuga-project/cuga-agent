from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cuga.backend.server.auth import require_auth
from cuga.backend.server.config_store import load_config, load_draft, reset_config_db
from cuga.backend.server.manage_routes import router


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
                "agent": {"name": "Test Agent"},
                "knowledge": {
                    "enabled": True,
                    "agent_level_enabled": True,
                    "session_level_enabled": False,
                },
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
                "agent": {"name": "Test Agent"},
                "knowledge": {
                    "enabled": True,
                    "agent_level_enabled": False,
                    "session_level_enabled": True,
                },
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


def test_publish_defers_flip_when_reindex_started(monkeypatch):
    """#c2 / #c1: when a vector-config publish triggers a reindex that returns
    status='started', publish must NOT promote the active pointer immediately —
    it defers to the strict flip — AND it keeps the OLD active collection busy
    in _reindex_in_progress for the deferred window, so uploads/deletes to it
    can't be lost when the flip promotes the new hash."""
    import tempfile
    from pathlib import Path

    from cuga.backend.server.manage_routes import config_routes

    reset_config_db()
    _tmp = Path(tempfile.mkdtemp(prefix="cuga-pub-defer-"))
    _doc = SimpleNamespace(filename="a.pdf", chunk_count=1, status="ok", ingested_at="t0")

    class _DeferEngine:
        def __init__(self):
            self._reindex_in_progress: set[str] = set()
            self._reindex_deferred: set[str] = set()
            self._config = SimpleNamespace(persist_dir=_tmp)
            self._files_dir = _tmp
            self._metadata = SimpleNamespace(get_collection_config=self._get_collection_config)

        def prepare_knowledge_update(self, cfg):
            return SimpleNamespace(knowledge_cfg=cfg)

        def commit_knowledge_update(self, prepared):
            return {"reindex_recommended": False}

        async def list_documents(self, collection: str):
            # OLD/snapshot collection (no hash suffix) has docs; the NEW
            # (hashed) target is empty → triggers the migration + reindex.
            return [_doc] if collection == "kb_agent_test_agent" else []

        async def _get_collection_config(self, collection: str):
            return None

        async def copy_source_files(self, src: str, dst: str) -> int:
            return 1

        async def reindex(self, collection: str) -> dict:
            return {"status": "started", "task_ids": ["t1"]}

    engine = _DeferEngine()

    # Capture the deferred-flip call (its internals have their own tests).
    # Record state DURING the flip — before its wrapper's finally releases the
    # old-collection flag — so the #c1 assertion doesn't race the bg task.
    _flip_seen: dict = {}

    async def _capture_flip(_agent_id, live_engine, _live_state, target, target_hash, task_ids):
        _flip_seen["old_busy"] = "kb_agent_test_agent" in live_engine._reindex_in_progress
        _flip_seen["target"] = target
        _flip_seen["task_ids"] = list(task_ids)

    monkeypatch.setattr(config_routes, "deferred_reindex_complete_and_flip", _capture_flip)
    monkeypatch.setattr(config_routes, "apply_published_config", _allow_publish)
    monkeypatch.setattr(config_routes, "rebuild_production_agent", _allow_publish)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_auth] = lambda: None
    app.state.app_state = SimpleNamespace(
        knowledge_engine=engine,
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
                "agent": {"name": "Test Agent"},
                "knowledge": {
                    "enabled": True,
                    "embedding_provider": "fastembed",
                    "embedding_model": "BAAI/bge-small-en-v1.5",
                },
            }
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    # Reindex was started asynchronously.
    assert body.get("reindex", {}).get("status") == "started"
    # Pointer NOT promoted immediately — deferred to the strict flip.
    assert getattr(app.state.app_state, "knowledge_config_hash", None) is None
    # Deferred flip was spawned with the task_ids, targeting the NEW collection.
    assert _flip_seen.get("task_ids") == ["t1"]
    assert _flip_seen.get("target", "").startswith("kb_agent_test_agent_")
    # OLD active collection was busy DURING the deferred window (#c1).
    assert _flip_seen.get("old_busy") is True
    # The new vector hash must NOT be persisted before the strict flip verifies
    # success — draft + published stay on the previous (empty) hash until then.
    draft = asyncio.run(load_draft("test-agent"))
    published, _ = asyncio.run(load_config(None, "test-agent"))
    assert (draft.get("knowledge") or {}).get("_vector_config_hash") in (None, "")
    assert (published.get("knowledge") or {}).get("_vector_config_hash") in (None, "")
