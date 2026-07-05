"""Regression tests for issue #396 — engine config drift mid-reindex.

The bug: while a reindex is in flight, the user clicks Use on a different
embedder. The PATCH succeeds, ``engine._config`` mutates to the new embedder
mid-stream. Queued ingest workers read the NEW embedder but still write to
the OLD collection name (computed at migration start). Result: collection
named for one config contains vectors shaped by another. Future
resolve_collection lookups return a name-vs-content mismatch — either silent
garbage or a dim-mismatch crash.

The fix is layered:

  1. Engine guard — engine.apply_knowledge_config raises
     ReindexInProgressError on a VECTOR-AFFECTING change (embedding /
     chunking / metric) while a reindex is running, comparing the
     incoming config against the live ``_config`` so it's
     timing-independent. patch_draft_knowledge maps that to a 409
     (``reindex_in_progress``) for the FE. A redundant no-op / non-vector
     PATCH (the debounced autosave that races a Save & Reindex click)
     genuinely changes nothing vector-affecting, so it passes through to
     200 instead of a spurious "Couldn't save". An over-broad route-level
     pre-check used to live here too but rejected on in-flight ALONE —
     deleted; this single precise check owns it now.
  2. Pointer flip — deferred to a background task that waits for all
     per-file workers to reach terminal state, then re-checks the engine
     config under ``_agent_draft_lock`` and refuses to flip if the engine
     has moved on (and refuses to flip on partial failure).

These tests pin each layer, plus edge cases the audit flagged
(non-vector-affecting PATCH must still work during reindex, cross-agent
reindex must still block a vector change because engine config is global,
deferred flip respects the engine-config re-check).
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


async def _noop_persist(*_a, **_k):
    """No-op stand-in for _persist_active_vector_config so a flip unit test
    never touches the real config DB. The durable-persist behavior has its own
    dedicated behavioral test below."""
    return None


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

    def test_delete_document_rejected_during_reindex(self):
        # #6: deleting from a collection being reindexed would let the worker
        # re-embed the deleted doc into the in-flight target and RESURRECT it
        # after the flip. delete_document must raise ReindexInProgressError
        # (the route maps it to 409). The source collection stays flagged for
        # the whole Path A so this exact-collection check covers the active
        # (source) collection a DELETE resolves to.
        eng = _make_engine()
        coll = "kb_agent_cuga_default_abc"
        eng._reindex_in_progress.add(coll)
        try:
            with pytest.raises(ReindexInProgressError):
                asyncio.run(eng.delete_document(coll, "doc.pdf"))
        finally:
            eng._reindex_in_progress.discard(coll)

    def test_delete_document_allowed_when_idle(self):
        # Sanity: with no reindex in flight, the guard doesn't block — a
        # missing doc surfaces the normal DocumentNotFoundError, NOT the
        # reindex guard.
        from cuga.backend.knowledge.engine import DocumentNotFoundError

        eng = _make_engine()
        assert not eng._reindex_in_progress
        with pytest.raises(DocumentNotFoundError):
            asyncio.run(eng.delete_document("kb_agent_cuga_default_abc", "missing.pdf"))

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
        monkeypatch.setattr(
            "cuga.backend.server.manage_routes.knowledge_reindex.persist_active_vector_config",
            _noop_persist,
        )
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

    def test_flip_waits_for_in_progress_then_flips(self, monkeypatch):
        # Simulate the realistic case: engine.reindex returned, _reindex_in_progress
        # is still set, workers finish a moment later, then the flip happens.
        from cuga.backend.server import manage_routes

        manage_routes._AGENT_DRAFT_LOCKS.clear()
        monkeypatch.setattr(
            "cuga.backend.server.manage_routes.knowledge_reindex.persist_active_vector_config",
            _noop_persist,
        )
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

    def test_flip_refuses_when_a_task_still_running(self, monkeypatch):
        # Partial-terminal guard: if any listed task is still non-terminal
        # (e.g. the reindex-worker timeout cleared the busy flag while a file
        # was mid-flight), the flip must refuse — promoting would point queries
        # at a half-built collection.
        from cuga.backend.server import manage_routes

        manage_routes._AGENT_DRAFT_LOCKS.clear()
        monkeypatch.setattr(
            "cuga.backend.server.manage_routes.knowledge_reindex.persist_active_vector_config",
            _noop_persist,
        )
        live_state = SimpleNamespace(knowledge_config_hash="old_hash")

        async def fake_list_tasks(coll):
            return [
                {"task_id": "t1", "status": "completed"},
                {"task_id": "t2", "status": "running"},  # never reached terminal
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
        assert live_state.knowledge_config_hash == "old_hash"

    def test_flip_bails_out_after_wall_clock_deadline(self, monkeypatch):
        # Deadline guard: if the busy flag never clears (a wedged worker), the
        # flip must give up after the wall-clock cap WITHOUT promoting — it must
        # neither block forever nor flip onto an unfinished collection.
        from cuga.backend.server import manage_routes

        manage_routes._AGENT_DRAFT_LOCKS.clear()
        monkeypatch.setattr(
            "cuga.backend.server.manage_routes.knowledge_reindex.persist_active_vector_config",
            _noop_persist,
        )
        # Shrink the 30-min cap so the wait loop times out promptly.
        monkeypatch.setattr(
            "cuga.backend.server.manage_routes.knowledge_reindex._DEFERRED_FLIP_TIMEOUT_S", 0.2
        )
        live_state = SimpleNamespace(knowledge_config_hash="old_hash")

        async def fake_list_tasks(coll):  # pragma: no cover — deadline fires first
            return [{"task_id": "t1", "status": "completed"}]

        engine = SimpleNamespace(
            _reindex_in_progress={"kb_agent_x_new"},  # never cleared → deadline fires
            _metadata=SimpleNamespace(list_tasks=fake_list_tasks),
            _config=SimpleNamespace(vector_config_hash=lambda: "new_hash"),
        )

        asyncio.run(
            manage_routes._deferred_reindex_complete_and_flip(
                "cuga-default", engine, live_state, "kb_agent_x_new", "new_hash", ["t1"]
            )
        )
        assert live_state.knowledge_config_hash == "old_hash"

    def test_flip_persists_hash_and_embedder_fields_behaviorally(self, monkeypatch):
        # Behavioral counterpart to the source-string durability check (#5): a
        # successful flip writes the target hash AND the embedder fields
        # together to BOTH draft and published, so a restart reloads a
        # self-consistent active pointer rather than an orphaned hash.
        from cuga.backend.server import manage_routes

        manage_routes._AGENT_DRAFT_LOCKS.clear()
        live_state = SimpleNamespace(knowledge_config_hash="old_hash")

        async def fake_list_tasks(coll):
            return [{"task_id": "t1", "status": "completed"}]

        engine = SimpleNamespace(
            _reindex_in_progress=set(),
            _metadata=SimpleNamespace(list_tasks=fake_list_tasks),
            _config=SimpleNamespace(
                vector_config_hash=lambda: "new_hash",
                embedding_provider="litellm",
                embedding_model="watsonx/intfloat/multilingual-e5-large",
                chunk_size=512,
                chunk_overlap=64,
                metric_type="COSINE",
            ),
        )

        # In-memory config-store spies — the REAL _persist_active_vector_config
        # runs against these, so we assert on what it actually wrote.
        draft_store = {"knowledge": {"embedding_provider": "fastembed"}}
        published = {"knowledge": {"embedding_provider": "fastembed"}}
        captured = {}

        async def fake_load_draft(agent_id):
            return dict(draft_store)

        async def fake_save_draft(cfg, agent_id):
            draft_store.clear()
            draft_store.update(cfg)

        async def fake_load_config(version=None, agent_id=None):
            return dict(published), "7"

        async def fake_update_published(cfg, agent_id, ver):
            captured["cfg"] = cfg
            captured["ver"] = ver

        monkeypatch.setattr("cuga.backend.server.config_store.load_draft", fake_load_draft)
        monkeypatch.setattr("cuga.backend.server.config_store.save_draft", fake_save_draft)
        monkeypatch.setattr("cuga.backend.server.config_store.load_config", fake_load_config)
        monkeypatch.setattr(
            "cuga.backend.server.config_store.update_published_config_at_version",
            fake_update_published,
        )

        asyncio.run(
            manage_routes._deferred_reindex_complete_and_flip(
                "cuga-default", engine, live_state, "kb_agent_x_new", "new_hash", ["t1"]
            )
        )

        assert live_state.knowledge_config_hash == "new_hash"
        # Draft persisted the hash AND the embedder fields together.
        assert draft_store["knowledge"]["_vector_config_hash"] == "new_hash"
        assert draft_store["knowledge"]["embedding_provider"] == "litellm"
        assert draft_store["knowledge"]["embedding_model"] == "watsonx/intfloat/multilingual-e5-large"
        # Published config updated at its version with the same self-consistent set.
        assert captured["ver"] == "7"
        assert captured["cfg"]["knowledge"]["_vector_config_hash"] == "new_hash"
        assert captured["cfg"]["knowledge"]["embedding_provider"] == "litellm"


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


class TestHttpReindexGuard:
    """The HTTP route's reindex guard, owned by the engine (Layer 2) and
    surfaced as a 409 with the structured shape the FE expects. A
    VECTOR-affecting change during reindex 409s; a no-op / non-vector
    change passes through to 200 (no spurious "Couldn't save")."""

    def test_returns_409_when_vector_change_during_reindex(self, app_with_engine):
        # chunk_size is vector-affecting → rejected while a reindex runs.
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
        # Message comes from the engine's ReindexInProgressError now.
        assert "reindex in progress" in detail.get("message", "").lower()

    def test_noop_nonvector_patch_during_reindex_succeeds(self, app_with_engine):
        """THE regression test for the user-reported 409. A redundant
        autosave that races a Save & Reindex carries the SAME (or only
        search-side) config — it changes nothing vector-affecting, so it
        must return 200 even while a reindex is in flight. The old
        over-broad route pre-check 409'd this, surfacing as a misleading
        "Couldn't save — Retry"."""
        client, engine = app_with_engine
        engine._reindex_in_progress.add("kb_agent_cuga_default_oldhash")
        try:
            resp = client.patch(
                "/api/manage/config/draft/knowledge?agent_id=cuga-default",
                json={"knowledge": {"rerank_top_k_in": 30, "search_query_transform": "multi_query"}},
            )
        finally:
            engine._reindex_in_progress.discard("kb_agent_cuga_default_oldhash")

        assert resp.status_code == 200, resp.text

    def test_other_agent_reindex_still_blocks_vector_change(self, app_with_engine, monkeypatch):
        """Engine config is GLOBAL, so a different agent's reindex is still
        reading the live config — we can't safely mutate embedder/chunking
        while ANY collection is reindexing. A foreign-agent reindex must
        therefore still 409 a vector change here (the precise engine guard
        is engine-global, not per-agent)."""
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

    def test_patch_adopts_existing_collection_as_active_pointer(self, app_with_engine, monkeypatch):
        """Regression for 'imported config-v4, no documents'. Applying a config
        whose embedder maps to an ALREADY-BUILT collection must flip the active
        pointer (app_state.knowledge_config_hash) to it immediately — no reindex
        — so /documents + retrieval resolve to that collection's docs. Before
        the fix the pointer stayed on the old collection and the user saw the
        wrong/zero documents."""
        from cuga.backend.knowledge.config import KnowledgeConfig as _KC

        client, engine = app_with_engine
        # Engine config hashes to "newhash"; that collection exists w/ docs.
        monkeypatch.setattr(_KC, "vector_config_hash", lambda self: "newhash")

        async def _fake_list_docs(coll):
            return [{"filename": "a.pdf"}] if coll == "kb_agent_cuga_default_newhash" else []

        monkeypatch.setattr(engine, "list_documents", _fake_list_docs)

        # Capture the durability persist so we can assert it ran with the
        # adopted hash (CR-N1), without touching the real config DB. Patch it on
        # knowledge_routes, where patch_draft_knowledge imported the name.
        _persist_calls: list[str] = []

        async def _capture_persist(_agent_id, _engine, target_hash):
            _persist_calls.append(target_hash)

        monkeypatch.setattr(
            "cuga.backend.server.manage_routes.knowledge_routes.persist_active_vector_config",
            _capture_persist,
        )

        # Active pointer starts on a DIFFERENT collection.
        client.app.state.app_state.knowledge_config_hash = "oldhash"
        resp = client.patch(
            "/api/manage/config/draft/knowledge?agent_id=cuga-default",
            json={"knowledge": {"rerank_top_k_in": 25}},  # non-vector; apply succeeds
        )
        assert resp.status_code == 200, resp.text
        # Pointer adopted the engine's (existing, populated) collection.
        assert client.app.state.app_state.knowledge_config_hash == "newhash"
        # ...and persisted the adopted hash durably (CR-N1).
        assert _persist_calls == ["newhash"], "adopt must persist the adopted hash"

    def test_patch_does_not_adopt_when_collection_absent(self, app_with_engine, monkeypatch):
        """A NEW embedder (no collection built yet) must NOT flip the pointer —
        the reindex/deferred-flip owns that. Guards against activating an empty
        collection."""
        from cuga.backend.knowledge.config import KnowledgeConfig as _KC

        client, engine = app_with_engine
        monkeypatch.setattr(_KC, "vector_config_hash", lambda self: "freshhash")

        async def _empty_list_docs(_coll):
            return []  # collection doesn't exist / has no docs

        monkeypatch.setattr(engine, "list_documents", _empty_list_docs)
        client.app.state.app_state.knowledge_config_hash = "oldhash"
        resp = client.patch(
            "/api/manage/config/draft/knowledge?agent_id=cuga-default",
            json={"knowledge": {"rerank_top_k_in": 25}},
        )
        assert resp.status_code == 200, resp.text
        # Pointer unchanged — no empty-collection activation.
        assert client.app.state.app_state.knowledge_config_hash == "oldhash"

    def test_publish_rejected_during_reindex(self, app_with_engine):
        """#2: POST /config (publish) must 409 while a reindex is in flight for
        the agent. Publish calls prepare/commit_knowledge_update directly, NOT
        apply_knowledge_config, so this route-level guard is the ONLY thing that
        stops a publish from bumping _apply_generation (superseding in-flight
        workers) and racing a second write of the active pointer."""
        client, engine = app_with_engine
        engine._reindex_in_progress.add("kb_agent_cuga_default_active")
        try:
            resp = client.post(
                "/api/manage/config?agent_id=cuga-default",
                json={"config": {"agent": {"name": "x"}, "knowledge": {"enabled": True}}},
            )
        finally:
            engine._reindex_in_progress.discard("kb_agent_cuga_default_active")

        assert resp.status_code == 409, resp.text
        detail = resp.json().get("detail") or resp.json()
        assert detail.get("error") == "reindex_in_progress"
        assert "kb_agent_cuga_default_active" in detail.get("collections", [])


# ---------------------------------------------------------------------------
# Publish-path unification (#1) + durable flip (#5) — source-level pins
# (a full publish-migration run needs docs on disk + a live reindex).
# ---------------------------------------------------------------------------


class TestEmbedderAvailabilityProbe:
    """The active-embedder availability probe surfaced in health() so the UI can
    warn when a collection's vectors are stranded behind an unreachable embedder."""

    def test_unavailable_on_embed_failure(self, monkeypatch):
        eng = _make_engine()
        eng._config.enabled = True
        monkeypatch.setattr(eng, "_ensure_embeddings", lambda: None)

        class _BadEmb:
            def embed_query(self, _t):
                raise RuntimeError("401 Unauthorized")

        eng._default_embeddings = _BadEmb()
        r = asyncio.run(eng.probe_active_embedder())
        assert r["available"] is False
        assert "401" in (r["error"] or "")

    def test_available_and_cached(self, monkeypatch):
        eng = _make_engine()
        eng._config.enabled = True
        calls = {"n": 0}

        class _OkEmb:
            def embed_query(self, _t):
                calls["n"] += 1
                return [0.1, 0.2, 0.3]

        monkeypatch.setattr(eng, "_ensure_embeddings", lambda: None)
        eng._default_embeddings = _OkEmb()
        r1 = asyncio.run(eng.probe_active_embedder())
        r2 = asyncio.run(eng.probe_active_embedder())
        assert r1["available"] is True and r2["available"] is True
        assert calls["n"] == 1, "second probe within TTL must use the cache, not re-embed"

    def test_none_when_disabled(self):
        eng = _make_engine()
        eng._config.enabled = False
        r = asyncio.run(eng.probe_active_embedder())
        assert r["available"] is None


# Publish unified-promotion + durable-persist are covered BEHAVIORALLY (the
# prior source-string tests were replaced per review — grep asserts can't catch
# a wrong hash / skipped write / dead code):
#   - publish deferral on reindex "started":
#       tests/unit/test_manage_publish_sync.py::test_publish_defers_flip_when_reindex_started
#   - durable persist of a successful flip:
#       TestLayer3DeferredFlip::test_flip_persists_hash_and_embedder_fields_behaviorally
