"""Regression test: PATCH /api/manage/config/draft/knowledge must apply to the live engine.

Bug recap: user changed ``docling_pdf_mode`` to "fast" in the UI, the PATCH
returned HTTP 200, but the next upload still ran in "accurate" mode. Root
cause was that the PATCH endpoint saved the draft but never called
``engine.apply_knowledge_config`` on the live engine. This test pins the
fix so the bug doesn't come back.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cuga.backend.knowledge.config import KnowledgeConfig
from cuga.backend.knowledge.engine import KnowledgeEngine
from cuga.backend.server.manage_routes import router as manage_router


@pytest.fixture
def app_with_live_engine(monkeypatch):
    """Build a FastAPI app with a real KnowledgeEngine on app.state.app_state."""
    # Isolate per-test so the engine flock doesn't fight a running cuga server.
    tmp = tempfile.mkdtemp(prefix="cuga-patch-live-")
    cfg = KnowledgeConfig(enabled=True, persist_dir=Path(tmp))
    engine = KnowledgeEngine(cfg)

    app = FastAPI()
    app.include_router(manage_router)  # router already carries /api/manage prefix
    # The handler reads ``app.state.app_state.knowledge_engine``.
    app.state.app_state = SimpleNamespace(knowledge_engine=engine, agent_id="cuga-default")
    app.state.draft_app_state = SimpleNamespace()  # no draft agent rebuild path

    yield app, engine

    try:
        engine.shutdown()
    except Exception:
        pass


@pytest.mark.asyncio
async def test_patch_knowledge_applies_to_live_engine(app_with_live_engine, monkeypatch):
    """The fix for the bug: PATCH must mutate engine._config, not just the draft."""
    app, engine = app_with_live_engine

    # Stub the draft persistence + registry reload so the test stays in-process.
    async def _fake_load_draft(_agent_id):
        return {}

    async def _fake_load_and_patch_draft(_agent_id, _section, value):
        return {"knowledge": value}

    monkeypatch.setattr(
        "cuga.backend.server.config_store.load_draft",
        _fake_load_draft,
    )
    # patch_draft_knowledge now calls the lock-free variant directly inside
    # the per-agent lock (7be12e08); _load_and_patch_draft is only used by
    # the other draft sections. Patch both so the test works regardless of
    # which path the route takes.
    monkeypatch.setattr(
        "cuga.backend.server.manage_routes._load_and_patch_draft",
        _fake_load_and_patch_draft,
    )
    monkeypatch.setattr(
        "cuga.backend.server.manage_routes._save_draft_section_unlocked",
        _fake_load_and_patch_draft,
    )

    # Block the http reload call.
    monkeypatch.setattr(
        "cuga.backend.tools_env.registry.utils.api_utils.get_registry_base_url",
        lambda: "http://localhost:0",
    )

    # Pre-condition: live engine starts in 'accurate' mode (default).
    assert engine._config.docling_pdf_mode == "accurate"

    with TestClient(app) as client:
        resp = client.patch(
            "/api/manage/config/draft/knowledge?agent_id=cuga-default",
            json={"knowledge": {"docling_pdf_mode": "fast", "embedding_batch_size": 128}},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "success"
    assert body["live_applied"] is True

    # The bug fix in action: engine._config now reflects the PATCH.
    assert engine._config.docling_pdf_mode == "fast"
    assert engine._config.embedding_batch_size == 128


@pytest.mark.asyncio
async def test_patch_knowledge_rejects_bad_config_without_partial_apply(app_with_live_engine, monkeypatch):
    """An invalid value must NOT silently land on the engine.

    The previous draft-only flow returned 200 even for nonsense values
    because validation only ran against the dict, not the engine. With the
    live-apply path, validation runs through the engine's preflight too.
    """
    app, engine = app_with_live_engine

    async def _fake_load_draft(_agent_id):
        return {}

    async def _fake_load_and_patch_draft(_agent_id, _section, value):
        return {"knowledge": value}

    monkeypatch.setattr("cuga.backend.server.config_store.load_draft", _fake_load_draft)
    monkeypatch.setattr(
        "cuga.backend.server.manage_routes._load_and_patch_draft",
        _fake_load_and_patch_draft,
    )

    pre = engine._config.docling_pdf_mode

    with TestClient(app) as client:
        resp = client.patch(
            "/api/manage/config/draft/knowledge?agent_id=cuga-default",
            json={"knowledge": {"docling_pdf_mode": "ultra-fast-mega"}},
        )

    assert resp.status_code == 400
    assert "docling_pdf_mode" in resp.text
    # Engine config is unchanged.
    assert engine._config.docling_pdf_mode == pre
