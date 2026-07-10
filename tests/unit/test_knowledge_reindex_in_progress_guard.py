"""Regression tests for issue #396 — engine config drift mid-reindex.

The bug: while a reindex is in flight, the user clicks Use on a different
embedder. The PATCH succeeds, ``engine._config`` mutates to the new embedder
mid-stream. Queued ingest workers read the NEW embedder but still write to
the OLD collection name (computed at migration start). Result: collection
named for one config contains vectors shaped by another. Future
resolve_collection lookups return a name-vs-content mismatch — either silent
garbage or a dim-mismatch crash.

The fix is layered:

  1. Layer 1 — patch_draft_knowledge returns 409 (`reindex_in_progress`)
     when the agent's collections are in ``_reindex_in_progress``. Fast UX.
  2. Layer 2 — engine.apply_knowledge_config raises ReindexInProgressError
     on a vector-affecting change while reindex is running. SDK guard.
  3. Layer 3 — pointer flip deferred to a background task that waits for
     all per-file workers to reach terminal state, then re-checks the
     engine config under ``_agent_draft_lock`` and refuses to flip if
     the engine has moved on.

These tests pin each layer independently, plus a few edge cases the audit
flagged (non-vector-affecting PATCH should still work during reindex; a
cross-agent reindex STILL blocks a vector-affecting PATCH because Layer 2's
engine-global guard applies regardless of agent; deferred flip respects the
engine-config re-check).
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from cuga.backend.knowledge.config import KnowledgeConfig
from cuga.backend.knowledge.engine import KnowledgeEngine, ReindexInProgressError


# ---------------------------------------------------------------------------
# Layer 2 — engine-level guard (apply_knowledge_config raises during reindex)
# ---------------------------------------------------------------------------


def _make_engine() -> KnowledgeEngine:
    tmp = tempfile.mkdtemp(prefix="cuga-rip-test-")
    cfg = KnowledgeConfig(enabled=True, persist_dir=Path(tmp))
    return KnowledgeEngine(cfg)


class TestLayer2EngineApplyGuard:
    """``apply_knowledge_config`` must reject VECTOR-affecting changes
    while any reindex is in progress; non-vector changes must still work."""

    def test_vector_affecting_change_during_reindex_raises(self):
        eng = _make_engine()
        eng._reindex_in_progress.add("kb_agent_x_old")
        try:
            with pytest.raises(ReindexInProgressError) as exc:
                eng.apply_knowledge_config(
                    {
                        "embedding_provider": "litellm",
                        "embedding_model": "watsonx/intfloat/multilingual-e5-large",
                    }
                )
            # Error message mentions the in-flight collection so operators
            # can grep / surface it.
            assert "kb_agent_x_old" in str(exc.value)
        finally:
            eng._reindex_in_progress.discard("kb_agent_x_old")

    def test_chunk_size_change_during_reindex_raises(self):
        # chunking_changed is also vector-affecting.
        eng = _make_engine()
        eng._reindex_in_progress.add("kb_agent_x_old")
        try:
            with pytest.raises(ReindexInProgressError):
                eng.apply_knowledge_config({"chunk_size": 600})
        finally:
            eng._reindex_in_progress.discard("kb_agent_x_old")

    def test_non_vector_affecting_change_during_reindex_allowed(self):
        # rerank / search settings don't affect the worker contract — they
        # must still apply during reindex (UX: user tunes search-side knobs
        # while heavy ingest runs).
        eng = _make_engine()
        eng._reindex_in_progress.add("kb_agent_x_old")
        try:
            result = eng.apply_knowledge_config(
                {
                    "rerank_top_k_in": 30,
                    "search_query_transform": "multi_query",
                }
            )
            assert result.get("embedding_changed") is False
        finally:
            eng._reindex_in_progress.discard("kb_agent_x_old")

    def test_apply_when_no_reindex_in_flight_succeeds(self):
        # Baseline: nothing in _reindex_in_progress → vector change applies.
        eng = _make_engine()
        assert not eng._reindex_in_progress
        result = eng.apply_knowledge_config({"chunk_size": 600})
        assert result.get("chunking_changed") is True

    def test_vector_change_during_reindex_rejected_before_preflight(self, monkeypatch):
        # Sami review: the reindex-conflict guard must fire BEFORE the embedding
        # preflight (create_embeddings / embed_query round-trip), so a change
        # we'll reject anyway doesn't hit the provider.
        import cuga.backend.knowledge.engine as eng_mod

        eng = _make_engine()
        eng._reindex_in_progress.add("kb_agent_x_old")

        def _boom(_cfg):
            raise AssertionError("create_embeddings must not run for a rejected change")

        monkeypatch.setattr(eng_mod, "create_embeddings", _boom)
        try:
            with pytest.raises(ReindexInProgressError):
                eng.apply_knowledge_config(
                    {
                        "embedding_provider": "litellm",
                        "embedding_model": "watsonx/intfloat/multilingual-e5-large",
                    }
                )
        finally:
            eng._reindex_in_progress.discard("kb_agent_x_old")


# ---------------------------------------------------------------------------
# Layer 3 — deferred pointer flip
# ---------------------------------------------------------------------------


class TestLayer3DeferredFlip:
    """The pointer flip must:
    (a) wait for every per-file worker to reach terminal state,
    (b) only flip if at least one worker completed,
    (c) re-check engine._config still matches target_hash under the lock,
    (d) bail out after a wall-clock timeout if workers never finish.
    """

    def test_flip_promotes_hash_when_all_tasks_complete(self, monkeypatch):
        from cuga.backend.server import manage_routes

        manage_routes._AGENT_DRAFT_LOCKS.clear()
        live_state = SimpleNamespace(knowledge_config_hash="old_hash")

        # Tasks listed as completed.
        async def fake_list_tasks(coll):
            return [
                {"task_id": "t1", "status": "completed"},
                {"task_id": "t2", "status": "completed"},
            ]

        engine = SimpleNamespace(
            _reindex_in_progress=set(),  # already empty → no wait
            _metadata=SimpleNamespace(list_tasks=fake_list_tasks),
            _config=SimpleNamespace(vector_config_hash=lambda: "new_hash"),
        )

        asyncio.run(
            manage_routes._deferred_reindex_complete_and_flip(
                "cuga-default", engine, live_state, "kb_agent_x_new", "new_hash", ["t1", "t2"]
            )
        )
        assert live_state.knowledge_config_hash == "new_hash"

    def test_flip_refuses_when_all_tasks_failed(self, monkeypatch):
        from cuga.backend.server import manage_routes

        manage_routes._AGENT_DRAFT_LOCKS.clear()
        live_state = SimpleNamespace(knowledge_config_hash="old_hash")

        async def fake_list_tasks(coll):
            return [
                {"task_id": "t1", "status": "failed"},
                {"task_id": "t2", "status": "cancelled"},
            ]

        engine = SimpleNamespace(
            _reindex_in_progress=set(),
            _metadata=SimpleNamespace(list_tasks=fake_list_tasks),
            _config=SimpleNamespace(vector_config_hash=lambda: "new_hash"),
        )

        asyncio.run(
            manage_routes._deferred_reindex_complete_and_flip(
                "cuga-default", engine, live_state, "kb_agent_x_new", "new_hash", ["t1", "t2"]
            )
        )
        # Critical: 0/2 succeeded → pointer must NOT have moved.
        assert live_state.knowledge_config_hash == "old_hash"

    def test_flip_refuses_when_engine_moved_on(self):
        # The exact #396 scenario: reindex completes successfully, but the
        # engine config drifted between when reindex started and when it
        # finished. Flipping now would point queries at a collection whose
        # content (current embedder) doesn't match the engine's config.
        from cuga.backend.server import manage_routes

        manage_routes._AGENT_DRAFT_LOCKS.clear()
        live_state = SimpleNamespace(knowledge_config_hash="old_hash")

        async def fake_list_tasks(coll):
            return [{"task_id": "t1", "status": "completed"}]

        engine = SimpleNamespace(
            _reindex_in_progress=set(),
            _metadata=SimpleNamespace(list_tasks=fake_list_tasks),
            # Engine moved to a DIFFERENT hash while reindex was running.
            _config=SimpleNamespace(vector_config_hash=lambda: "drifted_hash"),
        )

        asyncio.run(
            manage_routes._deferred_reindex_complete_and_flip(
                "cuga-default", engine, live_state, "kb_agent_x_new", "new_hash", ["t1"]
            )
        )
        # Pointer stays put — user must trigger a fresh reindex to converge.
        assert live_state.knowledge_config_hash == "old_hash"

    def test_flip_waits_for_in_progress_then_flips(self):
        # Simulate the realistic case: engine.reindex returned, _reindex_in_progress
        # is still set, workers finish a moment later, then the flip happens.
        from cuga.backend.server import manage_routes

        manage_routes._AGENT_DRAFT_LOCKS.clear()
        live_state = SimpleNamespace(knowledge_config_hash="old_hash")

        async def fake_list_tasks(coll):
            return [{"task_id": "t1", "status": "completed"}]

        in_progress = {"kb_agent_x_new"}
        engine = SimpleNamespace(
            _reindex_in_progress=in_progress,
            _metadata=SimpleNamespace(list_tasks=fake_list_tasks),
            _config=SimpleNamespace(vector_config_hash=lambda: "new_hash"),
        )

        async def drain_after_delay():
            await asyncio.sleep(0.3)
            in_progress.discard("kb_agent_x_new")

        async def run():
            await asyncio.gather(
                manage_routes._deferred_reindex_complete_and_flip(
                    "cuga-default", engine, live_state, "kb_agent_x_new", "new_hash", ["t1"]
                ),
                drain_after_delay(),
            )

        asyncio.run(run())
        assert live_state.knowledge_config_hash == "new_hash"


# ---------------------------------------------------------------------------
# Layer 1 — HTTP endpoint guard (patch_draft_knowledge returns 409)
# ---------------------------------------------------------------------------


@pytest.fixture
def app_with_engine(monkeypatch):
    """Same minimal app fixture used in test_knowledge_patch_live_apply.py
    so the 409 guard is exercised through the real route."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from cuga.backend.server.manage_routes import router as manage_router

    eng = _make_engine()

    app = FastAPI()
    app.include_router(manage_router)
    app.state.app_state = SimpleNamespace(knowledge_engine=eng, agent_id="cuga-default")
    app.state.draft_app_state = SimpleNamespace()

    # Stub the draft helpers so the route doesn't hit a real SQLite write.
    async def _fake_load_draft(_agent_id="cuga-default"):
        return {}

    async def _fake_save(_agent_id, _section, value):
        return {"knowledge": value}

    monkeypatch.setattr("cuga.backend.server.config_store.load_draft", _fake_load_draft)
    monkeypatch.setattr("cuga.backend.server.manage_routes._load_and_patch_draft", _fake_save)
    monkeypatch.setattr("cuga.backend.server.manage_routes._save_draft_section_unlocked", _fake_save)
    monkeypatch.setattr(
        "cuga.backend.tools_env.registry.utils.api_utils.get_registry_base_url",
        lambda: "http://localhost:0",
    )

    return TestClient(app), eng


class TestLayer1HttpGuard:
    """The HTTP 409 fast-path. Returns the structured error shape the FE
    expects so the user gets a clean toast instead of a generic save-failed."""

    def test_returns_409_when_agent_collection_in_progress(self, app_with_engine):
        client, engine = app_with_engine
        engine._reindex_in_progress.add("kb_agent_cuga_default_oldhash")
        try:
            resp = client.patch(
                "/api/manage/config/draft/knowledge?agent_id=cuga-default",
                json={"knowledge": {"chunk_size": 600}},
            )
        finally:
            engine._reindex_in_progress.discard("kb_agent_cuga_default_oldhash")

        assert resp.status_code == 409, resp.text
        body = resp.json()
        detail = body.get("detail") or body
        assert detail.get("error") == "reindex_in_progress"
        assert "kb_agent_cuga_default_oldhash" in detail.get("collections", [])
        assert "Re-index" in detail.get("message", "")

    def test_other_agent_passes_layer1_but_layer2_still_holds(self, app_with_engine, monkeypatch):
        """Layer 1 is per-agent (fast-rejection for the common case).
        Layer 2 (engine.apply_knowledge_config) is engine-global because
        engine config is global — a different agent's reindex IS still
        reading from the engine config, so we can't safely mutate
        embedder/chunking while ANY collection is reindexing.

        This test pins both behaviors: Layer 1 SKIPS the 409 (the other
        agent's prefix doesn't match ours), then Layer 2 catches and
        re-raises a 409 with the same shape. Defense in depth — and a
        warning to future devs that "Layer 1 only is per-agent."
        """
        client, engine = app_with_engine
        # Foreign-agent collection in flight.
        engine._reindex_in_progress.add("kb_agent_other_agent_xyz")
        try:
            resp = client.patch(
                "/api/manage/config/draft/knowledge?agent_id=cuga-default",
                json={"knowledge": {"chunk_size": 600}},
            )
        finally:
            engine._reindex_in_progress.discard("kb_agent_other_agent_xyz")

        # Layer 2 catches it. Same JSON shape as Layer 1.
        assert resp.status_code == 409, resp.text
        detail = resp.json().get("detail") or resp.json()
        assert detail.get("error") == "reindex_in_progress"
        assert "kb_agent_other_agent_xyz" in detail.get("collections", [])

    def test_allows_patch_when_nothing_in_progress(self, app_with_engine):
        client, engine = app_with_engine
        assert not engine._reindex_in_progress

        resp = client.patch(
            "/api/manage/config/draft/knowledge?agent_id=cuga-default",
            json={"knowledge": {"chunk_size": 600}},
        )
        assert resp.status_code == 200, resp.text

    def test_reindex_endpoint_rejects_during_reindex(self, app_with_engine):
        """Rapid double-click on Re-index: second call must 409 with
        reindex_in_progress, matching the PATCH /draft/knowledge shape."""
        client, engine = app_with_engine
        engine._reindex_in_progress.add("kb_agent_cuga_default_active")
        try:
            resp = client.post("/api/manage/knowledge/reindex_for_config?agent_id=cuga-default")
        finally:
            engine._reindex_in_progress.discard("kb_agent_cuga_default_active")

        assert resp.status_code == 409, resp.text
        detail = resp.json().get("detail") or resp.json()
        assert detail.get("error") == "reindex_in_progress"
        assert "kb_agent_cuga_default_active" in detail.get("collections", [])
